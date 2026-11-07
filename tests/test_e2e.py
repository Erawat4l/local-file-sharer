"""End-to-end: a real server, over real sockets, in every share mode.

The rest of the suite talks to the handler in-process; this file starts an
actual ``ShareServer`` on a loopback port and speaks HTTP to it. It is the only
place that proves the *wheel* works rather than the objects.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from local_file_sharer.transfer.manifest import Manifest
from local_file_sharer.transfer.server import ShareServer

BIG = 1_500_000


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "share-demo"
    root.mkdir()
    (root / "notes.txt").write_text("hello from the smoke test\n", encoding="utf-8")
    # 5860 * 256 = 1_500_160; slice it so the size is exactly BIG.
    (root / "photo.jpg").write_bytes((bytes(range(256)) * (BIG // 256 + 1))[:BIG])
    return root


@pytest.fixture
def media_tree(tmp_path: Path) -> Path:
    """A tree whose only file is playable, so stream mode has something to play."""
    root = tmp_path / "media-demo"
    root.mkdir()
    (root / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
    return root


@pytest.fixture
def server(tree: Path, free_port: int, request: pytest.FixtureRequest):
    """A live server; ``(mode, pin)`` come from ``indirect`` params if given."""
    mode, pin, *flags = getattr(request, "param", ("download", ""))
    root = request.getfixturevalue("media_tree") if mode == "stream" else tree
    uploads = "upload" in flags
    srv = ShareServer(
        Manifest.build([root], mode=mode),
        host="127.0.0.1",
        port=free_port,
        pin=pin,
        allow_upload=uploads,
        upload_dir=root if uploads else None,
    )
    srv.set_base_url(f"http://127.0.0.1:{free_port}")
    srv.start()
    assert srv.wait_until_ready(timeout=5)
    try:
        yield srv
    finally:
        srv.stop()


def open_page(server, path="/", *, pin=None, jar=None):
    """GET, returning ``(status, body, headers)`` instead of raising on 4xx."""
    jar = jar if jar is not None else http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    url = f"http://127.0.0.1:{server.port}{path}"
    if pin:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({"pin": pin})
    try:
        with opener.open(url, timeout=5) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def manifest_of(server) -> dict:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return json.loads(opener.open(f"http://127.0.0.1:{server.port}/api/manifest", timeout=5).read())


# ── the download path a browser (or curl) actually takes ────────────────────
def test_full_download_flow_over_a_real_socket(server, tree):
    status, body, _ = open_page(server, "/")
    assert status == 200
    assert b"photo.jpg" in body

    manifest = manifest_of(server)
    entry = next(f for f in manifest["files"] if f["name"] == "photo.jpg")
    assert entry["bytes"] == BIG

    downloaded = urllib.request.urlopen(
        f"http://127.0.0.1:{server.port}/download/{entry['id']}", timeout=10
    ).read()
    assert (
        hashlib.sha256(downloaded).hexdigest()
        == hashlib.sha256((tree / "photo.jpg").read_bytes()).hexdigest()
    )


def test_advertised_url_is_what_the_server_answers(server):
    """Guards the `host:port//download/x` bug: follow the URL we publish."""
    entry = next(f for f in manifest_of(server)["files"] if f["name"] == "photo.jpg")
    published = entry["download_url"]
    assert published.startswith(f"http://127.0.0.1:{server.port}/download/")

    local = published.replace(f"127.0.0.1:{server.port}", f"127.0.0.1:{server.port}")
    with urllib.request.urlopen(local, timeout=10) as response:
        assert response.status == 200
        assert len(response.read()) == entry["bytes"]


def test_range_and_resume_over_a_real_socket(server):
    entry = next(f for f in manifest_of(server)["files"] if f["name"] == "photo.jpg")
    url = f"http://127.0.0.1:{server.port}/download/{entry['id']}"

    full = urllib.request.urlopen(url, timeout=10).read()
    request = urllib.request.Request(url, headers={"Range": "bytes=500000-"})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 206
        resume = response.read()
    assert resume == full[500000:]


def test_head_matches_get(server):
    entry = next(f for f in manifest_of(server)["files"] if f["name"] == "photo.jpg")
    url = f"http://127.0.0.1:{server.port}/download/{entry['id']}"
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        assert int(response.headers["Content-Length"]) == entry["bytes"]
        assert response.headers["Accept-Ranges"] == "bytes"


@pytest.mark.parametrize("server", [("serve", "")], indirect=True)
def test_serve_mode_shows_the_folder(server):
    status, body, _ = open_page(server, "/")
    assert status == 200
    assert b"Web File Server" in body or b"file browser" in body.lower()
    assert b"photo.jpg" in body


@pytest.mark.parametrize("server", [("stream", "")], indirect=True)
def test_stream_mode_serves_the_player(server):
    """`/` sends the browser to the player, which must contain a real <video>."""
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5) as response:
        assert response.status == 200
        assert response.url.endswith(tuple(f"/s/{response.url.rsplit('/', 1)[-1]}")) or "/s/" in response.url
        player = response.read()
    assert b"<video" in player
    assert b"clip.mp4" in player
    assert b"Playlist" in player


@pytest.mark.parametrize("server", [("torrent", "")], indirect=True)
def test_torrent_tracker_and_webseed_over_a_real_socket(server, tree):
    """The magnet we print has to correspond to a tracker that answers.

    ``ShareServer`` only routes; ``ShareRunner`` builds the torrent, so this
    attaches one the same way ``share.py`` does and then speaks BitTorrent's
    HTTP protocol at it.
    """
    from local_file_sharer.share import build_torrent_for

    manifest = server.context.manifest
    torrent = build_torrent_for(
        manifest,
        base_url=f"http://127.0.0.1:{server.port}",
        tracker_url=f"http://127.0.0.1:{server.port}/announce",
    )
    # Wire it exactly like ShareRunner does, then let the tracker prove itself.
    from local_file_sharer.transfer.torrent import TrackerRegistry

    server.context.tracker_registry = TrackerRegistry()
    server.context.torrent = torrent

    info_hash = urllib.parse.quote(torrent.info_hash, safe="")
    peer = urllib.parse.quote(b"-LFS001-" + b"x" * 12, safe="")
    announce = (
        f"http://127.0.0.1:{server.port}/announce"
        f"?info_hash={info_hash}&peer_id={peer}&port=6881&left={tree_size(tree)}&event=started"
    )
    payload = urllib.request.urlopen(announce, timeout=5).read()
    assert payload.startswith(b"d"), payload[:40]
    assert b"interval" in payload
    assert b"peers" in payload

    # The URL the torrent itself advertises, not one we made up. BEP-19 paths
    # are /webseed/<torrent name>/<relative path>.
    assert torrent.webseed_url.endswith("/webseed/")
    with urllib.request.urlopen(torrent.webseed_url + "photo.jpg", timeout=10) as response:
        assert response.status == 200
        assert response.read() == (tree / "photo.jpg").read_bytes()

    # A real client fetches pieces with Range requests, not one whole GET.
    request = urllib.request.Request(torrent.webseed_url + "photo.jpg", headers={"Range": "bytes=0-16383"})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 206
        assert len(response.read()) == 16_384


def tree_size(tree: Path) -> int:
    return sum(path.stat().st_size for path in tree.rglob("*") if path.is_file())


# ── the PIN gate, driven like a browser ─────────────────────────────────────
@pytest.mark.parametrize("server", [("download", "4271")], indirect=True)
def test_login_form_post_logs_the_browser_in(server):
    jar = http.cookiejar.CookieJar()
    status, body, _ = open_page(server, "/", jar=jar)
    assert status == 401
    assert b'name="pin"' in body.lower()

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data = urllib.parse.urlencode({"pin": "4271"}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with opener.open(request, timeout=5) as response:
        assert response.status == 200
        assert b"photo.jpg" in response.read()
    assert [cookie.name for cookie in jar] == ["lfs_auth"]


@pytest.mark.parametrize("server", [("download", "4271")], indirect=True)
def test_a_typo_does_not_burn_the_next_attempt(server):
    status, body, _ = open_page(server, "/?pin=0000")
    assert status == 401
    assert b"Wrong PIN" in body

    status, body, _ = open_page(server, "/?pin=4271")
    assert status == 200
    assert b"photo.jpg" in body


@pytest.mark.parametrize("server", [("torrent", "")], indirect=True)
def test_tracker_is_created_if_a_torrent_is_served_without_one(server, tree):
    """A magnet whose announce URL answers 404 is a broken share."""
    from local_file_sharer.share import build_torrent_for

    manifest = server.context.manifest
    torrent = build_torrent_for(
        manifest,
        base_url=f"http://127.0.0.1:{server.port}",
        tracker_url=f"http://127.0.0.1:{server.port}/announce",
    )
    server.context.torrent = torrent
    server.context.tracker_registry = None

    info_hash = urllib.parse.quote(torrent.info_hash, safe="")
    peer = urllib.parse.quote(b"-LFS001-" + b"y" * 12, safe="")
    url = (
        f"http://127.0.0.1:{server.port}/announce"
        f"?info_hash={info_hash}&peer_id={peer}&port=6881&left={tree_size(tree)}"
    )
    payload = urllib.request.urlopen(url, timeout=5).read()
    assert payload.startswith(b"d"), payload[:40]
    assert b"interval" in payload
    assert server.context.tracker_registry is not None


def test_parallel_range_download_reassembles(server, tree):
    """The web UI's 16-connection button: many ranges, reassembled in order."""
    entry = next(f for f in manifest_of(server)["files"] if f["name"] == "photo.jpg")
    url = f"http://127.0.0.1:{server.port}/download/{entry['id']}"
    total = entry["bytes"]

    parts = []
    chunk = -(-total // 16)  # ceil, same as the JS
    for index in range(16):
        start = index * chunk
        stop = min(total - 1, start + chunk - 1)
        if start > stop:
            break
        request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{stop}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == f"bytes {start}-{stop}/{total}"
            parts.append(response.read())

    joined = b"".join(parts)
    assert len(joined) == total
    assert hashlib.sha256(joined).hexdigest() == hashlib.sha256((tree / "photo.jpg").read_bytes()).hexdigest()


def test_downloader_js_fires_its_ranges_concurrently():
    """A refactor that puts `await` back inside the loop silently serialises it."""
    source = Path(__file__).resolve().parents[1] / "src/local_file_sharer/web/assets/downloader.html"
    if not source.is_file():
        pytest.skip("web assets not present")
    script = source.read_text(encoding="utf-8")

    function = script[script.index("async function downloadParallel") :]
    function = function[: function.index("$('download-all')")]

    assert "Promise.all(parts.map(" in function, "ranges must be requested concurrently"
    # The request belongs *inside* the Promise.all callback. A `for … of` loop
    # with a bare `await` is exactly what used to serialise the 16 requests.
    # Ranges go through `fetchRange`, which owns the retry loop, so the call to
    # it is what has to sit inside the callback.
    before_parallel, _, after_parallel = function.partition("Promise.all(parts.map(")
    assert "await fetchRange(" not in before_parallel, "a bare await before Promise.all serialises the ranges"
    assert "await fetchRange(" in after_parallel


# ── uploads ─────────────────────────────────────────────────────────────────
def test_upload_lands_in_the_shared_folder(tmp_path, free_port):
    """A file pushed back must be where the sender can find it, not in ~."""
    from local_file_sharer.share import upload_folder

    root = tmp_path / "Photos"
    root.mkdir()
    (root / "existing.jpg").write_bytes(b"x" * 10)
    manifest = Manifest.build([root])
    target = upload_folder(manifest)
    assert target == root

    server = ShareServer(manifest, host="127.0.0.1", port=free_port, allow_upload=True, upload_dir=target)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        payload = b"pushed from the browser\n" * 50
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/api/upload?name=sent-back.txt",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
        assert body["ok"] is True
        saved = root / "sent-back.txt"
        assert saved.is_file(), "upload must land in the shared folder"
        assert saved.read_bytes() == payload
        assert Path(body["path"]).parent.resolve() == root.resolve()
    finally:
        server.stop()


def test_upload_folder_is_the_parent_of_a_single_file(tmp_path):
    from local_file_sharer.share import upload_folder

    target = tmp_path / "one.jpg"
    target.write_bytes(b"x")
    assert upload_folder(Manifest.build([target])) == tmp_path


def test_uploads_are_rejected_when_disabled(server):
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/api/upload?name=nope.txt",
        data=b"nope",
        method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)
    assert excinfo.value.code == 403


def test_upload_rejects_path_traversal(tmp_path, free_port):
    """`?name=../../.ssh/authorized_keys` must not escape the folder."""
    root = tmp_path / "shared"
    root.mkdir()
    manifest = Manifest.build([root])
    server = ShareServer(manifest, host="127.0.0.1", port=free_port, allow_upload=True, upload_dir=root)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        evil = urllib.parse.quote("../../escaped.txt", safe="")
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/api/upload?name={evil}",
            data=b"payload",
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            json.loads(response.read())
        assert not (tmp_path.parent / "escaped.txt").exists()
        assert not (tmp_path / "escaped.txt").exists()
        assert list(root.glob("*.txt")), "the file should be stored under its bare name"
    finally:
        server.stop()


@pytest.mark.parametrize("server", [("download", "4271")], indirect=True)
def test_api_pin_header_works_without_a_cookie(server):
    """`curl -H "X-LFS-PIN: …"` is how scripts authenticate; it used to 500."""
    header = {"X-LFS-PIN": "4271"}

    with urllib.request.urlopen(
        urllib.request.Request(f"http://127.0.0.1:{server.port}/", headers=header), timeout=5
    ) as response:
        assert response.status == 200
        assert b"photo.jpg" in response.read()
        assert "lfs_auth=" in (response.headers.get("Set-Cookie") or "")

    request = urllib.request.Request(f"http://127.0.0.1:{server.port}/api/manifest", headers=header)
    manifest = json.loads(urllib.request.urlopen(request, timeout=5).read())
    assert manifest["files"], "the manifest must be readable with the header alone"

    # A wrong header is still rejected, and does not leak the page.
    bad = urllib.request.Request(f"http://127.0.0.1:{server.port}/", headers={"X-LFS-PIN": "0000"})
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(bad, timeout=5)
    assert excinfo.value.code == 401
    assert b"photo.jpg" not in excinfo.value.read()


@pytest.mark.parametrize("server", [("download", "4271", "upload")], indirect=True)
def test_upload_with_the_pin_header_on_first_contact(tmp_path, server):
    """No cookie yet: the header authenticates the upload itself."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/api/upload?name=first.txt",
        data=b"first contact\n" * 30,
        method="POST",
        headers={"X-LFS-PIN": "4271", "Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read())
    assert body["ok"] is True
    assert body["bytes"] == len(b"first contact\n" * 30)


# ── validators: a wrong ETag must stop a resume, not corrupt it ─────────────
def test_etag_changes_when_a_file_is_rewritten_within_the_same_second(server, tree):
    """`int(mtime)` made a same-second rewrite invisible, so If-Range resumed."""
    import os

    target = tree / "notes.txt"
    entry = next(f for f in manifest_of(server)["files"] if f["name"] == "notes.txt")
    url = f"http://127.0.0.1:{server.port}/download/{entry['id']}"

    before = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)
    old_etag = before.headers["ETag"]

    stat = target.stat()
    target.write_text("different bytes, same length!!\n", encoding="utf-8")
    os.utime(target, (stat.st_atime, stat.st_mtime))  # same mtime, new bytes

    after = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)
    assert after.headers["ETag"] != old_etag


@pytest.mark.parametrize(
    ("if_range", "expect_status"),
    [
        ("match", 206),  # current validator: honour the range
        ('"not-the-etag"', 200),  # stale: RFC 9110 says send the whole file
        ("garbage", 200),  # unparseable: never enable a resume on a guess
    ],
)
def test_if_range_controls_whether_a_resume_happens(server, if_range, expect_status):
    entry = next(f for f in manifest_of(server)["files"] if f["name"] == "photo.jpg")
    url = f"http://127.0.0.1:{server.port}/download/{entry['id']}"
    head = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)
    etag = head.headers["ETag"]

    value = etag if if_range == "match" else if_range
    request = urllib.request.Request(url, headers={"Range": "bytes=0-99", "If-Range": value})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == expect_status
        body = response.read()
    assert len(body) == (100 if expect_status == 206 else entry["bytes"])


# ── multipart/byteranges: aria2c -x16 and Safari use this ───────────────────
def test_multipart_range_body_is_well_formed_and_complete(server, tree):
    """The per-part CRLFs were missing from Content-Length, so the tail was cut."""
    import email
    import email.policy

    target = tree / "photo.jpg"
    data = target.read_bytes()
    entry = next(f for f in manifest_of(server)["files"] if f["name"] == "photo.jpg")
    url = f"http://127.0.0.1:{server.port}/download/{entry['id']}"

    spans = [(0, 9), (100, 199), (len(data) - 120, len(data) - 1)]
    spec = ",".join(f"{a}-{b}" for a, b in spans)
    request = urllib.request.Request(url, headers={"Range": f"bytes={spec}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 206
        assert response.headers["Content-Type"].startswith("multipart/byteranges")
        body = response.read()
        # A short Content-Length makes the client stop mid-boundary.
        assert int(response.headers["Content-Length"]) == len(body)
        content_type = response.headers["Content-Type"]

    message = email.message_from_bytes(
        b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body,
        policy=email.policy.HTTP,
    )
    parts = list(message.iter_parts())
    assert len(parts) == len(spans), "every requested range must arrive"  # keeps zip() aligned
    for part, (start, end) in zip(parts, spans):  # noqa: B905 - 3.9 has no strict=
        assert part["Content-Range"].split()[1] == f"{start}-{end}/{len(data)}"
        assert part.get_payload(decode=True) == data[start : end + 1]


def test_repeated_uploads_of_one_name_do_not_overwrite_each_other(tmp_path, free_port):
    """Two uploads in the same second used to get the same `-<epoch>` name."""
    root = tmp_path / "shared"
    root.mkdir()
    (root / "existing.txt").write_text("ORIGINAL", encoding="utf-8")
    server = ShareServer(
        Manifest.build([root]), host="127.0.0.1", port=free_port, allow_upload=True, upload_dir=root
    )
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        names = []
        for attempt in range(4):
            payload = f"attempt {attempt}".encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.port}/api/upload?name=existing.txt",
                data=payload,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                names.append(json.loads(response.read())["name"])
        assert len(set(names)) == 4, f"names collided: {names}"
        assert (root / "existing.txt").read_text() == "ORIGINAL", "the share's own file must survive"
        assert sorted((root / name).read_text() for name in names) == [f"attempt {n}" for n in range(4)]
    finally:
        server.stop()


def test_a_short_upload_reports_failure_and_leaves_no_file(tmp_path, free_port):
    """A client that promises more than it sends must not get a clean filename."""
    import socket

    root = tmp_path / "shared"
    root.mkdir()
    server = ShareServer(
        Manifest.build([root]), host="127.0.0.1", port=free_port, allow_upload=True, upload_dir=root
    )
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=10) as sock:
            sock.sendall(
                b"POST /api/upload?name=short.txt HTTP/1.1\r\nHost: x\r\n"
                b"Content-Length: 1000\r\nConnection: close\r\n\r\nonly nine"
            )
            sock.shutdown(socket.SHUT_WR)
            reply = b""
            while chunk := sock.recv(65536):
                reply += chunk
        assert b"400" in reply.split(b"\r\n")[0]
        assert b"incomplete" in reply
        assert not (root / "short.txt").exists(), "a partial file must not be kept"
    finally:
        server.stop()


# ── the real page JS, executed (not just pattern-matched) ───────────────────
def test_the_downloader_page_actually_runs_and_saves_every_file(server):
    """Pattern-matching the source can't catch a runtime error; Node can."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    root = Path(__file__).resolve().parents[1]
    harness = root / "tests/js/downloader_smoke.js"
    html = root / "src/local_file_sharer/web/assets/downloader.html"
    if not harness.is_file() or not html.is_file():
        pytest.skip("web assets not present")

    result = subprocess.run(
        [node, str(harness), f"http://127.0.0.1:{server.port}/", str(html)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "ok" in result.stdout


# ── awkward but perfectly legal filenames ───────────────────────────────────
def test_files_with_unicode_spaces_and_percent_literals_are_served(tmp_path, free_port):
    """Real folders contain these; each must download byte-for-byte."""
    root = tmp_path / "shared"
    root.mkdir()
    expected = {
        "résumé 2024.pdf": b"pdf",
        "日本語のファイル.txt": b"japanese",
        "emoji 🎉📁.txt": b"emoji",
        "with space and#hash.txt": b"hash",
        "percent%20literal.txt": b"percent",
        "quote'and\"dquote.txt": b"quotes",
    }
    for name, payload in expected.items():
        try:
            (root / name).write_bytes(payload)
        except OSError as exc:  # pragma: no cover - Windows forbids '"' in a name
            # Windows rejects '"' outright and its filesystem encoding cannot
            # store some of these names, so there is nothing to serve and the
            # test cannot set up. POSIX creates all six.
            pytest.skip(f"this filesystem cannot store {name!r}: {exc}")

    server = ShareServer(Manifest.build([root]), host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        manifest = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{server.port}/api/manifest").read())
        by_name = {entry["name"]: entry for entry in manifest["files"]}
        assert set(expected) <= set(by_name), f"missing from the manifest: {set(expected) - set(by_name)}"
        for name, payload in expected.items():
            url = f"http://127.0.0.1:{server.port}/f/{by_name[name]['id']}"
            with urllib.request.urlopen(url, timeout=10) as response:
                assert response.read() == payload, f"{name!r} came back corrupted"
    finally:
        server.stop()


def test_a_refused_upload_answers_before_the_body_finishes(server):
    """The status must arrive even though the client is still uploading.

    The upload endpoint answered 403 and closed the socket without reading the
    body. On a loopback connection the body often arrives in one piece, so it
    looked fine; on Windows the client was still writing when the server
    closed, and urllib raised ``ConnectionAbortedError: [WinError 10053]``
    instead of the 403. Drain the body (up to a cap) before answering.
    """
    body = b"x" * 200_000
    request = (
        b"POST /api/upload?name=nope.txt HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
        sock.sendall(request)
        sock.send(body[:5000])  # deliberately unfinished
        sock.settimeout(5)
        head = sock.recv(4096)
    assert head.startswith(b"HTTP/1.1 403"), head[:80]


# ── the front page shows folders, not a wall of files ───────────────────────
def _rows(port: int, path: str) -> list[str]:
    """The row labels on a page, in the order the page shows them."""
    body = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10).read().decode()
    return re.findall(r'class="row-name"[^>]*>([^<]+)<', body)


def _nested_share(tmp_path: Path):
    """A share with a folder two levels deep, plus a loose file beside it.

    The names are deliberately generic. This fixture used to be a copy of a
    real phone: a game directory, two app builds and a set of ROM files, all of
    which were somebody's actual device contents and had no business being in a
    public repository. The shape is what the test is about, not the names.
    """
    root = tmp_path / "share"
    (root / "alpha" / "beta").mkdir(parents=True)
    (root / "alpha" / "beta" / "deep.bin").write_bytes(b"x" * 1000)
    (root / "alpha" / "index.m3u8").write_bytes(b"x" * 229)
    loose = tmp_path / "loose.apk"
    loose.write_bytes(b"x" * 24)
    return root, loose


def test_serve_mode_front_page_lists_folders_not_deep_files(tmp_path, free_port):
    """A folder's deep files must not be listed flat on the front page.

    Every file at every depth was listed with nothing but its basename, so
    alpha/beta/deep.bin appeared as a bare "deep.bin" row beside the loose
    file at the top. The folder was invisible and the page was unusable. One
    level per page: folders here, their files inside.

    The folder here is picked alongside a loose file, so the folder itself is
    one row; its contents (including the sub-folder "alpha") are inside it.
    Before the grouping fix the folder's name was stripped from every path
    beneath it, which both hid the folder and spilled "alpha/" onto the top of
    the page as if it had been picked separately.
    """
    root, loose = _nested_share(tmp_path)
    server = ShareServer(Manifest.build([root, loose], mode="serve"), host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        rows = _rows(server.port, "/")
        inside = _rows(server.port, "/browse/share")
    finally:
        server.stop()
    assert rows == ["share/", "loose.apk"], rows
    assert inside == ["alpha/"], inside


def test_each_folder_drills_down_to_its_own_contents(tmp_path, free_port):
    """Opening a folder must show that folder, all the way down."""
    root, loose = _nested_share(tmp_path)
    server = ShareServer(Manifest.build([root, loose], mode="serve"), host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        alpha = _rows(server.port, "/browse/alpha")
        deep = _rows(server.port, "/browse/alpha/beta")
    finally:
        server.stop()
    assert alpha == ["beta/", "index.m3u8"], alpha
    assert deep == ["deep.bin"], deep


def test_a_folder_row_says_how_much_is_inside_it(tmp_path, free_port):
    """The count and size have to describe the whole folder, not its top level."""
    root, loose = _nested_share(tmp_path)
    server = ShareServer(Manifest.build([root, loose], mode="serve"), host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=10).read().decode()
    finally:
        server.stop()
    # roms holds two files at two depths, 1229 bytes together
    assert "2 files" in body
    assert "1.2 KB" in body


# ── picking individual files must not flatten their folders ────────────────
def _picked_files(tmp_path: Path) -> list[Path]:
    """Files chosen one at a time from inside nested folders, with extras."""
    root = tmp_path / "phone"
    (root / "Game" / "obb").mkdir(parents=True)
    (root / "Game" / "data").mkdir(parents=True)
    (root / "Game" / "clip.mp4").write_bytes(b"v" * 50)
    (root / "Game" / "obb" / "main.obb").write_bytes(b"o" * 40)
    (root / "Game" / "data" / "save.dat").write_bytes(b"d" * 30)
    # A second folder at the top, so "Game" is itself a folder on the page and
    # the picked paths show two levels of nesting, which is the case that broke.
    (root / "Shots").mkdir()
    (root / "Shots" / "first.jpg").write_bytes(b"j" * 20)
    return [
        root / "Game" / "clip.mp4",
        root / "Game" / "obb" / "main.obb",
        root / "Game" / "data" / "save.dat",
        root / "Shots" / "first.jpg",
    ]


def test_files_picked_individually_keep_the_folder_they_lived_in(tmp_path, free_port):
    """The picker hands over a list of files, and that list used to lose folders.

    ``item_from_path`` addressed a file by ``path.name``, so choosing
    Game/obb/main.obb and Game/data/save.dat produced two rows called
    "main.obb" and "save.dat" at the top of the page, with no "obb" and no
    "data" anywhere -- the same flattening the folder share was fixed for, by
    a different route. A picked file has to keep the path below the folder the
    picker started from.
    """
    files = _picked_files(tmp_path)
    manifest = Manifest.build(files, mode="serve")
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        rows = _rows(server.port, "/")
        game = _rows(server.port, "/browse/Game")
    finally:
        server.stop()
    assert rows == ["Game/", "Shots/"], rows
    assert game == ["data/", "obb/", "clip.mp4"], game


def test_picking_one_file_still_shows_that_file(tmp_path, free_port):
    """The grouping must not swallow the simple case: one file, one row."""
    only = tmp_path / "album" / "track.mp3"
    only.parent.mkdir()
    only.write_bytes(b"m" * 10)
    manifest = Manifest.build([only], mode="serve")
    server = ShareServer(manifest, host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        rows = _rows(server.port, "/")
    finally:
        server.stop()
    assert rows == ["track.mp3"], rows


def test_a_folder_picked_whole_lists_its_contents_not_itself(tmp_path, free_port):
    """Sharing ~/Photos opens on what is inside Photos.

    One shared folder is the whole share, so the front page *is* that folder --
    asking it to render a single card named Photos, which you then have to
    click to see anything, was the original complaint about the picker. Its
    sub-folders are what should be listed.
    """
    root = tmp_path / "Photos"
    (root / "2024").mkdir(parents=True)
    (root / "2024" / "a.jpg").write_bytes(b"j" * 10)
    (root / "b.jpg").write_bytes(b"j" * 10)
    server = ShareServer(Manifest.build([root], mode="serve"), host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        rows = _rows(server.port, "/")
    finally:
        server.stop()
    assert rows == ["2024/", "b.jpg"], rows


def test_two_folders_and_a_file_keep_both_folders(tmp_path, free_port):
    """Picking folders alongside a file must not shatter the folders.

    The picker can return any mix, and the common one is "these two folders,
    and that file". ``_inside_item`` stripped *any* item's name off the front
    of a path, so ``ADM/a.txt`` became the loose file ``a.txt`` at the top of
    the page: both folder cards vanished and their contents spilled out flat
    beside the loose file. Nothing that shows folders may ever do that.

    This shape was untested -- the suites covered a list of files and a single
    folder, never the mix -- which is exactly the shape a person picks.
    """
    root = tmp_path / "Download"
    (root / "ADM").mkdir(parents=True)
    (root / "Quick Share").mkdir(parents=True)
    (root / "ADM" / "a.txt").write_bytes(b"a" * 10)
    (root / "ADM" / "b.txt").write_bytes(b"b" * 10)
    (root / "Quick Share" / "c.txt").write_bytes(b"c" * 10)
    (root / "app-debug.apk").write_bytes(b"p" * 10)
    roots = [root / "ADM", root / "Quick Share", root / "app-debug.apk"]
    server = ShareServer(Manifest.build(roots, mode="serve"), host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        rows = _rows(server.port, "/")
        inside = _rows(server.port, "/browse/ADM")
    finally:
        server.stop()
    # both folders, as folders -- and the loose file beside them
    assert rows == ["ADM/", "Quick Share/", "app-debug.apk"], rows
    # and each one still drills down to its own contents
    assert inside == ["a.txt", "b.txt"], inside


def test_a_folder_and_a_file_still_group_deep_contents(tmp_path, free_port):
    """A picked folder's *sub*-folders stay grouped, not flattened.

    Second half of the same bug: with the folder name stripped, ``ADM/season/1``
    had no ``ADM`` to group it under either, so a nested share came out as bare
    files with none of their folders.
    """
    root = tmp_path / "Download"
    (root / "ADM" / "season").mkdir(parents=True)
    (root / "ADM" / "season" / "one.mkv").write_bytes(b"v" * 10)
    (root / "ADM" / "top.txt").write_bytes(b"t" * 10)
    (root / "notes.txt").write_bytes(b"n" * 10)
    roots = [root / "ADM", root / "notes.txt"]
    server = ShareServer(Manifest.build(roots, mode="serve"), host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        rows = _rows(server.port, "/")
        inside = _rows(server.port, "/browse/ADM")
    finally:
        server.stop()
    assert rows == ["ADM/", "notes.txt"], rows
    assert inside == ["season/", "top.txt"], inside


def test_opening_a_folder_in_download_mode_keeps_the_download_page(tmp_path, free_port):
    """Download mode and serve mode are different pages, and one must not turn
    into the other on a click.

    ``/browse/<folder>`` was routed to the share-browser renderer, which always
    writes the serve-mode template (server.html) -- so a download station whose
    header said "Download Station" listed its files as a grey table with no
    stat row, no QR card and no buttons, the moment you opened a folder.
    """
    root = tmp_path / "Photos"
    (root / "2024").mkdir(parents=True)
    (root / "2024" / "a.jpg").write_bytes(b"j" * 10)
    server = ShareServer(Manifest.build([root], mode="download"), host="127.0.0.1", port=free_port)
    server.start()
    assert server.wait_until_ready(timeout=5)
    try:
        body = (
            urllib.request.urlopen(f"http://127.0.0.1:{server.port}/browse/Photos", timeout=10)
            .read()
            .decode()
        )
    finally:
        server.stop()
    # the download station's own furniture, not the serve-mode table
    assert "Download Everything" in body or 'class="hero"' in body, "lost the download station hero"
    assert 'class="card file"' in body or 'class="card folder"' in body, "lost the card grid"
    assert "qr-frame" in body, "lost the QR card"


def test_the_streaming_save_never_buffers_whole_parts():
    """Saving to disk must stream each range, not collect it in memory.

    `saveViaStream` used to fetch every part as an ArrayBuffer and only then
    write them, so a multi-gigabyte file was resident in RAM in full before a
    byte reached the disk. The tab is killed long before the transfer ends,
    which the user sees as a download that stops dead partway through.
    """
    source = Path(__file__).resolve().parents[1] / "src/local_file_sharer/web/assets/downloader.html"
    if not source.is_file():
        pytest.skip("web assets not present")
    script = source.read_text(encoding="utf-8")

    body = script[script.index("async function saveViaStream") :]
    body = body[: body.index("async function downloadParallel")]
    # Comments explain the old behaviour and name it, so judge the code only.
    code = "\n".join(line.split("//")[0] for line in body.splitlines())

    assert "arrayBuffer()" not in code, "the save path must not buffer a whole part"
    assert "r.body.getReader()" in body, "parts must be read as a stream"
    assert "writable.write(value)" in body, "each chunk must be written as it arrives"


def test_a_failed_range_is_retried_instead_of_restarting_the_file():
    """A dropped range must cost one range, not the whole transfer.

    Phones drop connections constantly. The old code let the error reach a
    catch-all that navigated away and started the file again from byte zero.
    """
    source = Path(__file__).resolve().parents[1] / "src/local_file_sharer/web/assets/downloader.html"
    if not source.is_file():
        pytest.skip("web assets not present")
    script = source.read_text(encoding="utf-8")

    assert "async function fetchRange(" in script, "ranges need a retry wrapper"
    retry = script[script.index("async function fetchRange(") :]
    retry = retry[: retry.index("async function saveViaStream")]
    assert "setTimeout" in retry, "the retry must back off between attempts"
    assert "416" in retry, "a 416 is not worth retrying"

    parallel = script[script.index("async function downloadParallel") :]
    catch = parallel[parallel.index("} catch (e) {") :]
    assert "window.location.href = file.url" not in catch, (
        "navigating away on error throws away the bytes already written"
    )
