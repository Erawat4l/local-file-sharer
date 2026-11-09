"""Torrent: bencode, piece hashing, .torrent structure, magnet and tracker."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from local_file_sharer.errors import TorrentError
from local_file_sharer.transfer.torrent import (
    TorrentFile,
    TrackerRegistry,
    bdecode,
    bencode,
    build_magnet,
    build_torrent,
    choose_piece_length,
    files_from_entries,
    info_hash,
    info_hash_hex,
    parse_torrent,
    read_torrent,
    torrent_hint,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, b"i0e"),
        (42, b"i42e"),
        (-7, b"i-7e"),
        ("abc", b"3:abc"),
        (b"\x00\xff", b"2:\x00\xff"),
        ([1, "a"], b"li1e1:ae"),
        ({"b": 1, "a": 2}, b"d1:ai2e1:bi1ee"),
        (True, b"i1e"),
    ],
)
def test_bencode(value, expected):
    assert bencode(value) == expected


def test_bencode_roundtrip():
    payload = {
        "info": {"name": "x", "piece length": 16384, "pieces": b"\x01\x02" * 20, "files": []},
        "creation date": 1700000000,
        "url-list": ["http://a/b"],
    }
    assert bdecode(bencode(payload))[b"info"][b"name"] == b"x"


@pytest.mark.parametrize("bad", [b"i", b"5:ab", b"l", b"x"])
def test_bdecode_errors(bad):
    with pytest.raises(TorrentError):
        bdecode(bad)


def test_choose_piece_length():
    assert choose_piece_length(1024) == 64 * 1024
    assert choose_piece_length(10 * 1024**2) == 256 * 1024
    assert choose_piece_length(3 * 1024**3) == 2 * 1024 * 1024
    assert choose_piece_length(20 * 1024**3) == 4 * 1024 * 1024


def _files(share_tree: Path) -> list[TorrentFile]:
    return [
        TorrentFile(
            path=share_tree / "hello.txt",
            rel="share/hello.txt",
            size=(share_tree / "hello.txt").stat().st_size,
            root_name="share",
        ),
        TorrentFile(
            path=share_tree / "photos" / "one.jpg",
            rel="share/photos/one.jpg",
            size=(share_tree / "photos" / "one.jpg").stat().st_size,
            root_name="share",
        ),
    ]


def test_build_torrent_multi_file(share_tree):
    files = _files(share_tree)
    build = build_torrent(
        files,
        name="share",
        tracker_url="http://192.168.1.4:8080/announce",
        webseed_url="http://192.168.1.4:8080/webseed/",
    )
    meta = parse_torrent(build.content)
    info = meta[b"info"]
    assert info[b"name"] == b"share"
    assert info[b"piece length"] == build.piece_length
    assert len(info[b"pieces"]) == build.piece_count * 20
    assert len(info[b"files"]) == 2
    assert info[b"files"][0][b"path"] == [b"hello.txt"]
    assert info[b"files"][1][b"path"] == [b"photos", b"one.jpg"]
    assert meta[b"announce"] == b"http://192.168.1.4:8080/announce"
    assert meta[b"url-list"] == [b"http://192.168.1.4:8080/webseed/"]
    assert hashlib.sha1(bencode(info)).hexdigest() == build.info_hash_hex


def test_single_file_torrent_has_length(share_tree):
    one = TorrentFile(path=share_tree / "hello.txt", rel="hello.txt", size=1200)
    build = build_torrent([one], name="hello.txt")
    info = parse_torrent(build.content)[b"info"]
    assert info[b"length"] == 1200
    assert b"files" not in info


def test_torrent_empty():
    with pytest.raises(TorrentError):
        build_torrent([], name="x")


def test_torrent_save_and_read(share_tree, tmp_path):
    build = build_torrent(_files(share_tree), name="share")
    path = build.save(tmp_path / "share.torrent")
    assert read_torrent(path)[b"info"][b"name"] == b"share"
    assert path.suffix == ".torrent"


def test_piece_hash_matches_concatenation(share_tree):
    files = [
        TorrentFile(path=share_tree / "hello.txt", rel="a/hello.txt", size=1200),
        TorrentFile(path=share_tree / "photos" / "one.jpg", rel="a/one.jpg", size=5002),
    ]
    build = build_torrent(files, name="a", piece_length=64 * 1024)
    combined = b"".join(f.path.read_bytes() for f in files)
    expected = hashlib.sha1(combined).digest()
    assert build.info["pieces"] == expected  # everything fits in one piece


def test_magnet_contains_fields(share_tree):
    build = build_torrent(_files(share_tree), name="share", tracker_url="http://t/announce")
    magnet = build.magnet
    assert magnet.startswith("magnet:?xt=urn:btih:")
    assert build.info_hash_hex in magnet
    assert "dn=share" in magnet
    assert "tr=" in magnet
    standalone = build_magnet("abc123", name="x", total_bytes=10)
    assert "xl=10" in standalone


def test_tracker_announce():
    registry = TrackerRegistry()
    hash_ = b"\x11" * 20
    peers = registry.announce(hash_, peer_id=b"-LFS-0001-AAAA", ip="10.0.0.2", port=6881, left=100)
    assert peers == []
    peers = registry.announce(hash_, peer_id=b"-LFS-0001-BBBB", ip="10.0.0.3", port=6882, left=0)
    assert len(peers) == 1
    assert peers[0].ip == "10.0.0.2"
    assert peers[0].compact() == b"\x0a\x00\x00\x02\x1a\xe1"
    stats = registry.stats(hash_)
    assert stats["complete"] == 1
    assert stats["incomplete"] == 1


def test_tracker_completed_event():
    registry = TrackerRegistry()
    registry.announce(b"\x22" * 20, peer_id=b"p1", ip="1.1.1.1", port=1, event="completed")
    assert registry.stats(b"\x22" * 20)["downloaded"] == 1


def test_info_hash_hex():
    info = {"name": "x", "piece length": 1, "pieces": b"\x00" * 20}

    def _digest() -> str:
        return hashlib.sha1(bencode(info)).hexdigest()

    assert info_hash_hex(info) == _digest()
    assert info_hash(info) == bytes.fromhex(_digest())


def test_files_from_entries_strips_root(share_tree):
    from local_file_sharer.transfer.manifest import Manifest

    manifest = Manifest.build([share_tree / "photos"])
    files = files_from_entries(manifest.iter_files(), root_name="photos")
    assert {f.rel for f in files} == {"photos/one.jpg", "photos/two.png", "photos/2024/three.txt"}
    assert files[0].parts[0] != "photos"


# ── tracker swarm bookkeeping ───────────────────────────────────────────────
def _registry():
    from local_file_sharer.transfer.torrent import TrackerRegistry

    return TrackerRegistry(), b"\x01" * 20


def test_stopped_event_removes_the_peer_at_once():
    """BEP-3 goodbye: peers must not keep dialling a client that has quit."""
    registry, info_hash = _registry()
    registry.announce(info_hash, peer_id=b"A" * 20, ip="10.0.0.1", port=6881, left=0)
    registry.announce(info_hash, peer_id=b"B" * 20, ip="10.0.0.2", port=6882, left=5000)
    assert registry.stats(info_hash) == {"complete": 1, "incomplete": 1, "downloaded": 0}

    assert registry.announce(info_hash, peer_id=b"B" * 20, ip="10.0.0.2", port=6882, event="stopped") == []
    assert registry.stats(info_hash) == {"complete": 1, "incomplete": 0, "downloaded": 0}
    assert [p.port for p in registry.peers(info_hash)] == [6881]


def test_stopping_an_unknown_peer_is_harmless():
    registry, info_hash = _registry()
    registry.announce(info_hash, peer_id=b"A" * 20, ip="10.0.0.1", port=6881, left=0)
    assert registry.announce(info_hash, peer_id=b"Z" * 20, ip="10.0.0.9", port=1, event="stopped") == []
    assert registry.stats(info_hash)["complete"] == 1


def test_a_peer_never_sees_itself_and_reannouncing_does_not_duplicate():
    registry, info_hash = _registry()
    first = registry.announce(info_hash, peer_id=b"A" * 20, ip="10.0.0.1", port=6881, left=0, event="started")
    assert first == [], "a lone peer has nobody else to connect to"

    registry.announce(info_hash, peer_id=b"B" * 20, ip="10.0.0.2", port=6882, left=0)
    seen_by_a = registry.announce(info_hash, peer_id=b"A" * 20, ip="10.0.0.1", port=6881, left=0)
    assert [p.port for p in seen_by_a] == [6882]
    assert len(registry.peers(info_hash)) == 2, "re-announcing must replace, not append"


def test_numwant_caps_the_peer_list():
    registry, info_hash = _registry()
    for n in range(10):
        registry.announce(info_hash, peer_id=bytes([n]) * 20, ip=f"10.0.0.{n + 1}", port=7000 + n, left=0)
    seen = registry.announce(info_hash, peer_id=b"A" * 20, ip="10.0.0.1", port=6881, left=0, numwant=3)
    assert len(seen) == 3


# ── the file has to work without our server ─────────────────────────────────
def test_the_torrent_carries_dht_bootstrap_nodes(share_tree):
    """A .torrent you cannot hand to somebody else is not a .torrent.

    The file used to name only this machine's own tracker and webseed, so it
    was an HTTP download with a torrent's clothes on: pass it to a friend, stop
    the share, and it stops working. The DHT node list (BEP-5) is what lets
    their client find peers with nothing of ours running.
    """
    build = build_torrent(_files(share_tree), name="share", tracker_url="http://192.168.1.4:8080/announce")
    meta = parse_torrent(build.content)

    assert b"nodes" in meta, "without DHT nodes the torrent dies with the share"
    nodes = dict(meta[b"nodes"])
    assert b"router.bittorrent.com" in nodes
    assert nodes[b"router.bittorrent.com"] == 6881
    assert build.portable is True


def test_a_private_torrent_stays_private(share_tree):
    """`private` means "same swarm only", so no DHT and no public trackers."""
    build = build_torrent(_files(share_tree), name="share", private=True)
    meta = parse_torrent(build.content)
    assert meta[b"info"][b"private"] == 1
    assert b"nodes" not in meta
    assert build.portable is False


def test_public_trackers_are_listed_one_per_tier(share_tree):
    """Tiers are tried one at a time, so each tracker needs its own."""
    build = build_torrent(_files(share_tree), name="share", tracker_url="http://192.168.1.4:8080/announce")
    tiers = parse_torrent(build.content)[b"announce-list"]
    assert [t.decode() for t in tiers[0]] == ["http://192.168.1.4:8080/announce"]
    assert len(tiers) > 1, "a dead built-in tracker must not be the only option"
    assert all(len(tier) == 1 for tier in tiers), "one tracker per tier"
    flat = [t.decode() for tier in tiers for t in tier]
    assert any(u.startswith("udp://") for u in flat)


def test_the_magnet_carries_a_peer_to_bootstrap_from(share_tree):
    """A magnet without a peer or tracker is a hash nobody can act on."""
    build = build_torrent(_files(share_tree), name="share")
    assert "x.pe=" in build.magnet
    assert "router.bittorrent.com" in build.magnet


def test_the_hint_says_whether_it_outlives_the_share(share_tree):
    portable = build_torrent(_files(share_tree), name="share")
    assert "without this device running" in torrent_hint(portable)

    private = build_torrent(_files(share_tree), name="share", private=True)
    assert "stops working" in torrent_hint(private)
