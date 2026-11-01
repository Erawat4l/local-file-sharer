"""HTTP Range parsing, content disposition, manifests and URL layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_file_sharer.transfer.http_handler import _content_disposition, parse_range
from local_file_sharer.transfer.manifest import (
    Manifest,
    build_manifest,
    guess_mime,
    item_from_path,
    kind_of,
    safe_join,
    walk_folder,
)


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        ("bytes=0-99", 1000, [(0, 99)]),
        ("bytes=100-", 1000, [(100, 999)]),
        ("bytes=-100", 1000, [(900, 999)]),
        ("bytes=0-0", 1000, [(0, 0)]),
        ("bytes=999-1500", 1000, [(999, 999)]),  # clamped to the file size
        ("bytes=0-49,100-149", 1000, [(0, 49), (100, 149)]),
        ("bytes=0-9, -10", 1000, [(0, 9), (990, 999)]),
        ("", 1000, []),
        ("items=0-5", 1000, []),
    ],
)
def test_parse_range(header, size, expected):
    assert parse_range(header, size) == expected


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=abc-def", "bytes=5-1", "bytes=-0"])
def test_parse_range_unsatisfiable(header):
    with pytest.raises(ValueError):
        parse_range(header, 1000)


def test_parse_range_limits_multi():
    header = "bytes=" + ",".join(f"{i}-{i}" for i in range(100))
    assert len(parse_range(header, 1000)) <= 32


def test_content_disposition():
    value = _content_disposition("my movie.mkv")
    assert value.startswith("attachment;")
    assert 'filename="my movie.mkv"' in value
    assert "filename*=UTF-8''my%20movie.mkv" in value
    assert _content_disposition("a.mp4", inline=True).startswith("inline;")


def test_guess_mime_and_kind():
    assert guess_mime(Path("a.mp4")) == "video/mp4"
    assert guess_mime(Path("a.mkv")) == "video/x-matroska"
    assert guess_mime(Path("a.unknownext")) == "application/octet-stream"
    assert kind_of(Path("a.mkv")) == "video"
    assert kind_of(Path("a.mp3")) == "audio"
    assert kind_of(Path("a.jpg")) == "image"
    assert kind_of(Path("a.pdf")) == "pdf"
    assert kind_of(Path("a.zip")) == "file"


def test_walk_folder(share_tree):
    entries = walk_folder(share_tree / "photos")
    rels = {e.rel for e in entries}
    assert "photos/one.jpg" in rels
    assert "photos/2024/three.txt" in rels
    assert all(e.size > 0 for e in entries)


def test_manifest_build(share_tree):
    manifest = Manifest.build([share_tree / "hello.txt", share_tree / "photos"])
    assert manifest.file_count == 4
    assert manifest.folder_count == 1
    assert manifest.total_bytes > 0
    assert not manifest.empty
    ids = [f.file_id for f in manifest.iter_files()]
    assert len(ids) == len(set(ids)), "file ids must be unique"
    payload = manifest.as_dict("http://1.2.3.4:8080")
    assert payload["file_count"] == 4
    assert payload["files"][0]["url"].startswith("http://1.2.3.4:8080/f/")


def test_manifest_lookup(share_tree):
    manifest = build_manifest([share_tree / "photos"])
    item = manifest.items[0]
    assert manifest.get_item(item.item_id) is item
    entry = manifest.iter_files().__next__()
    assert manifest.get_file(entry.file_id) is entry
    assert manifest.get_file_by_rel(entry.rel) is entry
    assert manifest.get_file("doesnotexist") is None


def test_manifest_expiry():
    import time

    manifest = Manifest.build([], expires_at=time.time() - 1)
    assert manifest.expired
    assert manifest.expiry_text() == "expired"
    fresh = Manifest.build([], expires_at=time.time() + 90)
    assert "expires in" in fresh.expiry_text()
    never = Manifest.build([])
    assert never.expiry_text() == "never expires"


def test_item_url_paths(share_tree):
    file_item = item_from_path(share_tree / "hello.txt")
    folder_item = item_from_path(share_tree / "photos")
    assert file_item.url_path.startswith("/f/")
    assert folder_item.url_path.startswith("/d/")
    assert file_item.is_dir is False
    assert folder_item.is_dir is True


def test_safe_join_blocks_traversal(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (base / "ok.txt").write_text("x")
    assert safe_join(base, "ok.txt") == (base / "ok.txt").resolve()
    assert safe_join(base, "../etc/passwd") is None
    assert safe_join(base, "/etc/passwd") is None


def test_manifest_duplicate_names(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "same.txt").write_text("a")
    (b / "same.txt").write_text("b")
    manifest = Manifest.build([a / "same.txt", b / "same.txt"])
    ids = [f.file_id for f in manifest.iter_files()]
    assert len(ids) == 2
    assert ids[0] != ids[1]
