"""End-to-end HTTP tests against a real server on a loopback port."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from local_file_sharer.transfer.manifest import Manifest
from local_file_sharer.transfer.server import ShareServer


@pytest.fixture
def server(share_tree: Path, free_port: int):
    manifest = Manifest.build([share_tree / "hello.txt", share_tree / "photos", share_tree / "clip.mp4"])
    srv = ShareServer(manifest, host="127.0.0.1", port=free_port)
    srv.start()
    assert srv.wait_until_ready(timeout=5)
    yield srv
    srv.stop()


def url(server: ShareServer, path: str) -> str:
    return f"http://127.0.0.1:{server.port}{path}"


def get(server: ShareServer, path: str, headers: dict | None = None):
    request = urllib.request.Request(url(server, path), headers=headers or {})
    return urllib.request.urlopen(request, timeout=15)


def test_health_and_disabled_caching(server):
    response = get(server, "/healthz")
    assert response.status == 200
    payload = json.load(response)
    assert payload["ok"] is True
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"


def test_index_page_lists_every_file(server):
    body = get(server, "/").read().decode()
    assert body.count('class="card file"') == 5
    assert "hello.txt" in body
    assert "Download" in body
    # the 16-connection button is present
    assert "16 connections" in body


def test_manifest_api(server):
    payload = json.load(get(server, "/api/manifest"))
    assert payload["file_count"] == 5
    assert payload["total_bytes"] > 0
    assert payload["items"]


def test_file_download(server, share_tree):
    payload = json.load(get(server, "/api/manifest"))
    entry = next(f for f in payload["files"] if f["name"] == "hello.txt")
    body = get(server, f"/f/{entry['id']}").read()
    assert body == (share_tree / "hello.txt").read_bytes()
    head = urllib.request.urlopen(urllib.request.Request(url(server, f"/f/{entry['id']}"), method="HEAD"))
    assert head.headers["Content-Length"] == str(len(body))
    assert head.headers["Accept-Ranges"] == "bytes"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bytes=0-9", 10),
        ("bytes=10-19", 10),
        ("bytes=-10", 10),
    ],
)
def test_range_requests(server, header, expected):
    entry = json.load(get(server, "/api/manifest"))["files"][0]
    response = get(server, f"/f/{entry['id']}", {"Range": header})
    assert response.status == 206
    assert len(response.read()) == expected
    assert "Content-Range" in response.headers


def test_unsatisfiable_range(server):
    entry = json.load(get(server, "/api/manifest"))["files"][0]
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(server, f"/f/{entry['id']}", {"Range": "bytes=99999999-"})
    assert excinfo.value.code == 416


def test_etag_revalidation(server):
    entry = json.load(get(server, "/api/manifest"))["files"][0]
    first = get(server, f"/f/{entry['id']}")
    etag = first.headers["ETag"]
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(server, f"/f/{entry['id']}", {"If-None-Match": etag})
    assert excinfo.value.code == 304


def test_multi_range_multipart(server):
    entry = json.load(get(server, "/api/manifest"))["files"][0]
    response = get(server, f"/f/{entry['id']}", {"Range": "bytes=0-4,10-14"})
    assert response.status == 206
    assert response.headers["Content-Type"].startswith("multipart/byteranges")
    body = response.read()
    assert body.count(b"Content-Range") == 2


def test_whole_share_zip(server):
    response = get(server, "/download/all.zip")
    data = response.read()
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.testzip() is None
    assert len(archive.namelist()) == 5


def test_zip_resume(server):
    complete = get(server, "/download/all.zip").read()
    cut = len(complete) // 2
    part = get(server, "/download/all.zip", {"Range": f"bytes={cut}-"}).read()
    archive = zipfile.ZipFile(io.BytesIO(complete[:cut] + part))
    assert archive.testzip() is None


def test_folder_browser(server):
    payload = json.load(get(server, "/api/manifest"))
    folder = next(i for i in payload["items"] if i["kind"] == "folder")
    body = get(server, f"/d/{folder['id']}").read().decode()
    assert "one.jpg" in body
    assert "three.txt" in body
    zipped = get(server, f"/zip/{folder['id']}").read()
    assert zipfile.ZipFile(io.BytesIO(zipped)).testzip() is None


def test_player_page(server):
    payload = json.load(get(server, "/api/manifest"))
    video = next(f for f in payload["files"] if f["name"].endswith(".mp4"))
    body = get(server, f"/s/{video['id']}").read().decode()
    assert "Playlist" in body
    assert video["id"] in body


def test_static_css_and_qr(server):
    css = get(server, "/static/app.css")
    assert css.status == 200
    assert len(css.read()) > 1000
    qr = get(server, "/api/qr?data=hello%20world")
    assert qr.status == 200
    assert b"<svg" in qr.read()


def test_404_page_is_friendly(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(server, "/nope")
    assert excinfo.value.code == 404
    body = excinfo.value.read().decode()
    assert "local-file-sharer" in body


def test_path_traversal_blocked(server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(server, "/static/../pyproject.toml")
    assert excinfo.value.code in (400, 404)


def test_stats_endpoint_counts_transfers(server):
    entry = json.load(get(server, "/api/manifest"))["files"][0]
    get(server, f"/f/{entry['id']}").read()
    stats = json.load(get(server, "/api/stats"))
    assert stats["bytes_sent"] > 0
    assert stats["device_count"] >= 1
    assert stats["requests"] >= 1


def test_pin_protection(share_tree, free_port):
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242")
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            get(server, "/")
        assert excinfo.value.code == 401
        assert "PIN" in excinfo.value.read().decode()
        # healthz stays open so the dashboard can poll
        assert get(server, "/healthz").status == 200
        # ?pin= sets a cookie-free access for this request
        body = get(server, "/?pin=4242").read()
        assert b"hello.txt" in body
    finally:
        server.stop()


def test_expired_share(share_tree, free_port):
    import time

    manifest = Manifest.build([share_tree / "hello.txt"], expires_at=time.time() - 1)
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            get(server, "/")
        assert excinfo.value.code == 410
        assert "expired" in excinfo.value.read().decode().lower()
    finally:
        server.stop()


def test_upload_disabled_by_default(share_tree, free_port):
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        request = urllib.request.Request(
            url(server, "/api/upload?name=evil.txt"), data=b"payload", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=10)
        assert excinfo.value.code == 403
    finally:
        server.stop()


def test_upload_enabled(share_tree, free_port, tmp_path):
    manifest = Manifest.build([share_tree / "hello.txt"])
    uploads = tmp_path / "uploads"
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, allow_upload=True, upload_dir=uploads)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        request = urllib.request.Request(
            url(server, "/api/upload?name=incoming.txt"), data=b"payload", method="POST"
        )
        payload = json.load(urllib.request.urlopen(request, timeout=10))
        assert payload["ok"] is True
        assert (uploads / "incoming.txt").read_bytes() == b"payload"
    finally:
        server.stop()


def test_serve_mode_routes_to_browser(share_tree, free_port):
    manifest = Manifest.build([share_tree / "photos"], mode="serve")
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        body = get(server, "/").read().decode()
        assert "web file server" in body
        assert "one.jpg" in body
    finally:
        server.stop()


def test_stream_mode_redirects_to_player(share_tree, free_port):
    manifest = Manifest.build([share_tree / "clip.mp4"], mode="stream")
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        body = get(server, "/").read().decode()
        assert "Playlist" in body or "player" in body.lower()
    finally:
        server.stop()
