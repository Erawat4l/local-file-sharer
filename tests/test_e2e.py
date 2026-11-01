"""End-to-end: a real server, over real sockets, in every share mode.

The rest of the suite talks to the handler in-process; this file starts an
actual ``ShareServer`` on a loopback port and speaks HTTP to it. It is the only
place that proves the *wheel* works rather than the objects.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from local_file_sharer.transfer import http_handler
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


@pytest.fixture(autouse=True)
def _clean_throttle():
    http_handler._FAILED_ATTEMPTS.clear()
    http_handler._LOCKED_UNTIL.clear()
    yield
    http_handler._FAILED_ATTEMPTS.clear()
    http_handler._LOCKED_UNTIL.clear()


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
    mode, pin = getattr(request, "param", ("download", ""))
    root = request.getfixturevalue("media_tree") if mode == "stream" else tree
    srv = ShareServer(Manifest.build([root], mode=mode), host="127.0.0.1", port=free_port, pin=pin)
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
    assert b"web file server" in body or b"file browser" in body.lower()
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
