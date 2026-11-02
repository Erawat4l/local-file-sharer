"""End-to-end HTTP tests against a real server on a loopback port."""

from __future__ import annotations

import http.cookiejar
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pytest

from local_file_sharer.fmt import human_size
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


def test_index_page_shows_subfolders_as_folders(server):
    """A shared folder's sub-folders are cards; their contents are not listed."""
    body = get(server, "/").read().decode()
    # photos/2024/ is the only sub-folder of the share
    assert body.count('class="card folder"') == 1
    assert 'href="/folder/2024"' in body
    assert "1 file" in body
    # files one level down are listed; files inside a sub-folder never are
    assert "one.jpg" in body
    assert "three.txt" not in body
    # the shared folder's own name is not repeated on each card
    assert "photos/one.jpg" not in body


def test_a_share_of_many_folders_lists_each_one(share_tree, free_port):
    """Sharing a parent folder lists its sub-folders, not every file inside."""
    manifest = Manifest.build([share_tree])
    srv = ShareServer(manifest, host="127.0.0.1", port=free_port)
    srv.start()
    try:
        assert srv.wait_until_ready(timeout=5)
        body = get(srv, "/").read().decode()
    finally:
        srv.stop()
    assert 'href="/folder/photos"' in body
    assert 'href="/folder/empty"' not in body  # nothing to download
    assert body.count('class="card folder"') == 1
    assert body.count('class="card file"') == 3  # hello.txt, data.bin, clip.mp4
    for hidden in ("one.jpg", "two.png", "three.txt"):
        assert hidden not in body


def test_folder_zip_downloads_only_that_folder(server):
    """Clicking a folder downloads that folder, not the whole share."""
    response = get(server, "/folder/2024")
    assert response.headers["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.read()))
    assert archive.testzip() is None
    # rooted inside the folder, so it opens straight onto its contents
    assert archive.namelist() == ["three.txt"]


def test_a_folder_card_opens_that_folder(server):
    """/browse/<sub-folder> lists that folder, and nested paths work too."""
    body = get(server, "/browse/2024").read().decode()
    assert "three.txt" in body
    assert "one.jpg" not in body
    # /d/<id> still serves the whole folder item, not just the sub-folder
    manifest = json.load(get(server, "/api/manifest"))
    folder = next(item for item in manifest["items"] if item["kind"] == "folder")
    full = get(server, "/d/" + folder["id"]).read().decode()
    assert "one.jpg" in full and "three.txt" in full


def test_browse_rejects_an_unknown_folder(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        get(server, "/browse/not-a-real-folder")
    assert caught.value.code == 404


def test_folder_zip_rejects_unknown_and_empty(server):
    for path, wanted in [("/folder/nope", 404), ("/folder/empty", 404), ("/folder/", 404)]:
        with pytest.raises(urllib.error.HTTPError) as caught:
            get(server, path)
        assert caught.value.code == wanted


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
    assert "LOCAL FILE SHARER" in body


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


def test_pin_brute_force_is_throttled(share_tree, free_port):
    """Wrong PINs must not be free: after a few failures the client is locked out."""
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242")
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        for _ in range(6):
            with pytest.raises(urllib.error.HTTPError):
                get(server, "/?pin=0000")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            get(server, "/?pin=4242")
        assert excinfo.value.code == 429
        assert b"Too many attempts" in excinfo.value.read()
        context = server.context
        client = next(iter(context._locked_until))
        context.note_success(client)
        assert context.locked_for(client) == 0
        assert b"hello.txt" in get(server, "/?pin=4242").read()
    finally:
        server.stop()


def test_auth_cookie_is_sent_with_the_login_page(share_tree, free_port):
    """A successful ``?pin=`` must set the cookie on the *same* response."""
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242")
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        with urllib.request.urlopen(url(server, "/?pin=4242"), timeout=10) as response:
            cookie = response.headers.get("Set-Cookie") or ""
            body = response.read()
        assert "lfs_auth=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
        assert b"hello.txt" in body
        value = cookie.split("lfs_auth=", 1)[1].split(";", 1)[0]
        request = urllib.request.Request(url(server, "/"), headers={"Cookie": f"lfs_auth={value}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
    finally:
        server.stop()


def test_expiry_endpoints_stay_open(share_tree, free_port):
    """An expired share must still answer /healthz and /api/expired."""
    import time

    manifest = Manifest.build([share_tree / "hello.txt"], expires_at=time.time() - 1)
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        assert get(server, "/healthz").status == 200
        payload = json.load(get(server, "/api/expired"))
        assert payload["expired"] is True
    finally:
        server.stop()


def test_download_headers_and_revalidation(server):
    """The wire contract the README promises (ranges, ETag, no caching)."""
    entry = json.load(get(server, "/api/manifest"))["files"][0]
    with urllib.request.urlopen(url(server, f"/f/{entry['id']}"), timeout=10) as response:
        headers = {k.lower(): v for k, v in response.headers.items()}
        response.read()
    assert headers["accept-ranges"] == "bytes"
    assert int(headers["content-length"]) == entry["bytes"]
    assert entry["size"] == human_size(entry["bytes"])
    assert headers["etag"].startswith('"')
    assert "no-store" in headers["cache-control"]
    assert headers["content-type"].startswith(("text/", "application/"))

    request = urllib.request.Request(
        url(server, f"/f/{entry['id']}"), headers={"If-None-Match": headers["etag"]}
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 304

    ranged = urllib.request.Request(
        url(server, f"/f/{entry['id']}"), headers={"Range": "bytes=2-5", "If-Range": headers["etag"]}
    )
    with urllib.request.urlopen(ranged, timeout=10) as response:
        assert response.status == 206
        assert len(response.read()) == 4


def test_head_request_matches_get(server):
    entry = json.load(get(server, "/api/manifest"))["files"][0]
    request = urllib.request.Request(url(server, f"/f/{entry['id']}"), method="HEAD")
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        assert int(response.headers["Content-Length"]) == entry["bytes"]
        assert response.read() == b""


def test_one_wrong_pin_does_not_lock_the_right_one(share_tree, free_port):
    """A mistyped PIN must not make the correct PIN look broken."""
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242")
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            get(server, "/?pin=0001")
        assert excinfo.value.code == 401
        body = excinfo.value.read()
        assert b"Wrong PIN" in body
        assert b"Too many attempts" not in body

        # ...and the very next attempt with the right PIN succeeds, immediately.
        assert b"hello.txt" in get(server, "/?pin=4242").read()
    finally:
        server.stop()


def test_lockout_page_explains_the_wait_even_for_a_correct_pin(share_tree, free_port):
    """During a lockout the page must say so — never a bare 'wrong PIN'."""
    from local_file_sharer.transfer.http_handler import FREE_PIN_ATTEMPTS

    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242")
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        for _ in range(FREE_PIN_ATTEMPTS):
            with pytest.raises(urllib.error.HTTPError):
                get(server, "/?pin=0001")

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            get(server, "/?pin=4242")
        assert excinfo.value.code == 429
        # RFC 6585: say how long to wait, so a script (or a person) need not guess.
        assert excinfo.value.headers["Retry-After"].isdigit()
        assert int(excinfo.value.headers["Retry-After"]) >= 1
        body = excinfo.value.read()
        assert b"Too many attempts" in body
        # It must not claim the PIN was wrong when nobody submitted one.
        assert b"Wrong PIN" not in body
    finally:
        server.stop()


def test_login_form_post_sets_the_cookie_on_the_redirect(share_tree, free_port):
    """The browser posts the PIN, gets a 302 to '/', and must already be logged in."""
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242")
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        body = urllib.parse.urlencode({"pin": "4242"}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # A redirect that is followed and still asks for the PIN is the bug. A
        # browser keeps the cookie automatically, so the opener needs a jar.
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(request, timeout=5) as response:
            assert response.status == 200
            assert b"hello.txt" in response.read()
        assert [c.name for c in jar if c.name == "lfs_auth"] == ["lfs_auth"]
    finally:
        server.stop()


def test_cookie_survives_a_manual_redirect_hop(share_tree, free_port):
    """Same thing without urllib's redirect handling, so the header itself is checked."""
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242")
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        body = urllib.parse.urlencode({"pin": "4242"}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        opener = urllib.request.build_opener(_NoRedirect)
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            opener.open(request, timeout=5)
        assert excinfo.value.code in (302, 303)
        assert "lfs_auth=" in (excinfo.value.headers.get("Set-Cookie") or "")
    finally:
        server.stop()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003, D102
        return None


def test_client_ip_only_trusts_x_forwarded_for_from_loopback():
    """A client must not be able to pick its own lockout identity.

    `X-Forwarded-For` is only meaningful when a proxy sets it. A LAN client
    sending it directly used to hand the server a fresh address per request and
    so reset its PIN budget forever.
    """
    from local_file_sharer.transfer.http_handler import ShareRequestHandler, _is_loopback

    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("::1") is True
    assert _is_loopback("192.168.1.42") is False
    assert _is_loopback("garbage") is False

    def ip_of(peer: str, forwarded: str) -> str:
        fake = object.__new__(ShareRequestHandler)
        fake.client_address = (peer, 50000)
        fake.headers = {"X-Forwarded-For": forwarded} if forwarded else {}
        return ShareRequestHandler.client_ip(fake)

    # A remote client claiming to be someone else is not believed.
    assert ip_of("192.168.1.42", "10.9.9.9") == "192.168.1.42"
    # A real local proxy is believed, because that is the only way it can work.
    assert ip_of("127.0.0.1", "203.0.113.7, 10.0.0.1") == "203.0.113.7"
    # No header at all: always the peer address.
    assert ip_of("192.168.1.42", "") == "192.168.1.42"


def test_two_shares_in_one_process_do_not_share_a_lockout(share_tree, free_port):
    """Attacking one share must not lock the user out of the next one."""
    first = ShareServer(
        Manifest.build([share_tree / "hello.txt"]), host="127.0.0.1", port=free_port, pin="4242"
    )
    first.start()
    try:
        assert first.wait_until_ready(timeout=5)
        for _ in range(6):
            with pytest.raises(urllib.error.HTTPError):
                get(first, "/?pin=0000")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            get(first, "/?pin=4242")
        assert excinfo.value.code == 429
    finally:
        first.stop()

    second = ShareServer(
        Manifest.build([share_tree / "hello.txt"]), host="127.0.0.1", port=free_port + 1, pin="9999"
    )
    second.start()
    try:
        assert second.wait_until_ready(timeout=5)
        assert second.context.locked_for("127.0.0.1") == 0
        assert b"hello.txt" in get(second, "/?pin=9999").read()
    finally:
        second.stop()


@pytest.mark.parametrize(
    "path_template",
    ["/f/{file}", "/download/all.zip", "/api/manifest", "/raw/hello.txt", "/webseed/hello.txt", "/torrent"],
)
def test_an_expired_share_serves_no_bytes_anywhere(share_tree, free_port, path_template):
    """Expiry must hold on every route, not just the index page."""
    import time

    manifest = Manifest.build([share_tree / "hello.txt"], expires_at=time.time() - 1)
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        entry = next(iter(manifest.iter_files()))
        path = path_template.format(file=entry.file_id)
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            get(server, path)
        assert excinfo.value.code == 410
        assert b"hello" not in excinfo.value.read().lower()
    finally:
        server.stop()


def test_expiry_cannot_be_bypassed_with_a_range_request(share_tree, free_port):
    import time

    manifest = Manifest.build([share_tree / "hello.txt"], expires_at=time.time() - 1)
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        entry = next(iter(manifest.iter_files()))
        request = urllib.request.Request(url(server, f"/f/{entry.file_id}"), headers={"Range": "bytes=0-4"})
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=5)
        assert excinfo.value.code == 410
    finally:
        server.stop()


def test_healthz_and_the_expired_api_stay_reachable_after_expiry(share_tree, free_port):
    """A monitor must still be able to tell an expired share from a dead one."""
    import time

    manifest = Manifest.build([share_tree / "hello.txt"], expires_at=time.time() - 1)
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        assert json.loads(get(server, "/healthz").read())["ok"] is True
        assert json.loads(get(server, "/api/expired").read())["expired"] is True
    finally:
        server.stop()


def test_a_share_that_has_not_expired_still_serves(share_tree, free_port):
    import time

    manifest = Manifest.build([share_tree / "hello.txt"], expires_at=time.time() + 120)
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        assert b"hello.txt" in get(server, "/").read()
        remaining = json.loads(get(server, "/api/expired").read())
        assert remaining["expired"] is False
        assert 110 < remaining["ttl"] <= 120, f"ttl should be counting down, got {remaining['ttl']}"
    finally:
        server.stop()


def test_a_share_with_many_files_zips_completely(tmp_path, free_port):
    """Hundreds of entries exercise the central directory, not just one file."""
    root = tmp_path / "many"
    root.mkdir()
    for index in range(400):
        (root / f"file_{index:04d}.txt").write_text(f"content {index}", encoding="utf-8")

    server = ShareServer(Manifest.build([root]), host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        data = get(server, "/download/all.zip").read()
        archive = zipfile.ZipFile(io.BytesIO(data))
        assert len(archive.namelist()) == 400
        assert archive.testzip() is None
        for index in (0, 199, 399):
            entry = f"many/file_{index:04d}.txt"
            assert archive.read(entry) == f"content {index}".encode()
    finally:
        server.stop()


def test_a_deep_range_returns_the_right_offsets(tmp_path, free_port):
    """Resuming a large archive deep inside must not shift the offset."""
    root = tmp_path / "big"
    root.mkdir()
    payload = bytes(range(256)) * 4096  # 1 MiB, non-repeating at every scale
    (root / "payload.bin").write_bytes(payload)

    server = ShareServer(Manifest.build([root]), host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        whole = get(server, "/download/all.zip").read()
        archive = zipfile.ZipFile(io.BytesIO(whole))
        assert archive.read("big/payload.bin") == payload

        # Local header sits at 0; find the payload inside the archive and ask for
        # a window straddling it, exactly as a resuming client would.
        offset = whole.index(payload[:1024])
        start, end = offset + 1000, offset + 2000
        window = get(server, "/download/all.zip", {"Range": f"bytes={start}-{end}"}).read()
        assert window == whole[start : end + 1]
        assert window == payload[1000:2001]
    finally:
        server.stop()


def test_stats_are_not_public_on_a_pin_protected_share(share_tree, free_port):
    """`/api/stats` was exempt, and it names files and shows visitor IPs."""
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242")
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            get(server, "/api/stats")
        assert excinfo.value.code == 401
        assert b"hello" not in excinfo.value.read().lower()

        # Logged in, the page's live counter still works.
        import http.cookiejar

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.open(url(server, "/?pin=4242"), timeout=5).read()
        payload = json.loads(opener.open(url(server, "/api/stats"), timeout=5).read())
        assert "bytes_sent" in payload and "uptime" in payload
    finally:
        server.stop()


def test_healthz_stays_public_so_a_monitor_can_poll_it(share_tree, free_port):
    """Gating stats must not take the cheap liveness probe with it."""
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242")
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        assert json.loads(get(server, "/healthz").read())["ok"] is True
    finally:
        server.stop()


def test_only_three_routes_are_reachable_without_the_pin(share_tree, free_port):
    """A future 'just exempt this one too' must fail here, loudly.

    /healthz, /favicon.ico and /robots.txt are deliberately public. Everything
    else — including the API surface — has to stay behind the PIN.
    """
    manifest = Manifest.build([share_tree / "hello.txt"])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, pin="4242", allow_upload=True)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        entry = next(iter(manifest.iter_files()))
        item = manifest.items[0]
        candidates = [
            "/",
            "/index.html",
            "/static/app.css",
            f"/f/{entry.file_id}",
            f"/download/{entry.file_id}",
            "/download/all.zip",
            f"/zip/{item.item_id}",
            f"/raw/{entry.rel}",
            "/api/manifest",
            "/api/links",
            "/api/stats",
            "/api/qr",
            "/api/expiry",
            "/api/expired",
            "/torrent",
            "/announce",
            "/scrape",
            f"/webseed/{entry.rel}",
        ]
        reachable = []
        for path in candidates:
            try:
                response = urllib.request.urlopen(url(server, path), timeout=5)
            except urllib.error.HTTPError:
                continue
            reachable.append((path, response.status))
            response.read()
        assert reachable == [], f"reachable without the PIN: {reachable}"
    finally:
        server.stop()


def test_only_the_error_page_css_answers_after_expiry(share_tree, free_port):
    """The expired page needs CSS; a monitor needs healthz and /api/expired.

    Everything else must go quiet, or an expired link keeps serving the share.
    """
    import time

    manifest = Manifest.build([share_tree / "hello.txt"], expires_at=time.time() - 1)
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    try:
        assert server.wait_until_ready(timeout=5)
        entry = next(iter(manifest.iter_files()))
        candidates = [
            "/",
            "/index.html",
            f"/f/{entry.file_id}",
            "/download/all.zip",
            "/raw/hello.txt",
            "/webseed/hello.txt",
            "/api/manifest",
            "/api/stats",
            "/api/links",
            "/api/qr",
            "/torrent",
            "/announce",
            "/scrape",
            "/static/app.css",  # expected to answer: the 410 page needs it
            "/api/expiry",  # not exempt: it is the countdown API
        ]
        reachable = []
        for path in candidates:
            try:
                response = urllib.request.urlopen(url(server, path), timeout=5)
            except urllib.error.HTTPError:
                continue
            reachable.append(path)
            response.read()
        assert sorted(reachable) == ["/static/app.css"], f"these answered after expiry: {reachable}"
    finally:
        server.stop()


def test_the_tracker_counts_exactly_what_it_sends(tmp_path, free_port):
    """Every download path goes through this; a race here would be invisible."""
    import threading

    root = tmp_path / "s"
    root.mkdir()
    size = 120_000
    (root / "a.bin").write_bytes(b"\xab" * size)
    server = ShareServer(Manifest.build([root]), host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        entry = next(iter(server.context.manifest.iter_files()))
        target = url(server, f"/f/{entry.file_id}")

        assert json.loads(get(server, "/api/stats").read())["bytes_sent"] == 0
        assert len(urllib.request.urlopen(target, timeout=30).read()) == size
        after_one = json.loads(get(server, "/api/stats").read())
        assert after_one["bytes_sent"] == size
        assert after_one["files"] == [{"name": "a.bin", "sent": size, "total": size}]

        threads = [
            threading.Thread(target=lambda: urllib.request.urlopen(target, timeout=30).read())
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # The client sees EOF the moment the last byte lands, but the server
        # records it in a `finally` a few milliseconds later. Reading /api/stats
        # straight after join() can catch the counter one transfer short, which
        # looked like lost bytes and failed only under full-suite load.
        deadline = time.monotonic() + 5
        while True:
            final = json.loads(get(server, "/api/stats").read())
            if final["bytes_sent"] >= size * 9 or time.monotonic() > deadline:
                break
            time.sleep(0.02)
        assert final["bytes_sent"] == size * 9, "concurrent sends must not lose bytes"
        assert final["requests"] == 9
        assert final["peak_speed"] >= 0
    finally:
        server.stop()
