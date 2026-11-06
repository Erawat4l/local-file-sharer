"""Streaming ZIP: structure, CRC correctness, determinism and resumes."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from local_file_sharer.transfer.zipstream import (
    ZipItem,
    ZipStream,
    estimate_zip_size,
    items_from_folder,
)


def _items(share_tree: Path) -> list[ZipItem]:
    return [
        ZipItem.from_path(share_tree / "hello.txt", arcname="hello.txt"),
        ZipItem.from_path(share_tree / "clip.mp4", arcname="media/clip.mp4"),
        ZipItem.from_path(share_tree / "photos" / "one.jpg", arcname="media/photos/one.jpg"),
    ]


@pytest.mark.parametrize("compression", [0, 1, 6])
def test_zip_is_valid(share_tree, compression):
    items = _items(share_tree)
    stream = ZipStream(items, compression=compression)
    data = b"".join(stream.iter_stream())
    assert len(data) == stream.total_size
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.testzip() is None
    assert set(archive.namelist()) == {"hello.txt", "media/clip.mp4", "media/photos/one.jpg"}
    for name in archive.namelist():
        original = next(i for i in items if i.arcname == name)
        assert archive.read(name) == original.path.read_bytes()


def test_zip_deflate_actually_compresses(share_tree):
    items = [ZipItem.from_path(share_tree / "hello.txt", arcname="hello.txt")]
    stored = ZipStream(items, compression=0)
    deflated = ZipStream(items, compression=6)
    assert deflated.compressed_size < stored.compressed_size
    assert deflated.total_size < stored.total_size


def test_zip_resume_is_byte_exact(share_tree):
    stream = ZipStream(_items(share_tree), compression=1)
    data = b"".join(stream.iter_stream())
    for cut in (1, 27, 512, len(data) // 2, len(data) - 3, stream.body_size):
        resumed = data[:cut] + b"".join(stream.iter_stream(cut))
        assert resumed == data, f"resume mismatch at {cut}"
        assert zipfile.ZipFile(io.BytesIO(resumed)).testzip() is None


def test_zip_range_slices(share_tree):
    stream = ZipStream(_items(share_tree), compression=1)
    data = b"".join(stream.iter_stream())
    for start, end in ((0, 100), (50, 60), (len(data) - 10, len(data)), (len(data), len(data) + 5)):
        assert b"".join(stream.iter_stream(start, end)) == data[start:end]


def test_zip_empty_list():
    stream = ZipStream([])
    data = b"".join(stream.iter_stream())
    assert data.startswith(b"PK")
    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == []


def test_zip_missing_file_keeps_structure(tmp_path):
    ghost = tmp_path / "ghost.txt"
    ghost.write_text("x" * 100)
    stream = ZipStream([ZipItem.from_path(ghost, arcname="ghost.txt")])
    ghost.unlink()  # disappear between listing and streaming
    data = b"".join(stream.iter_stream())
    assert len(data) == stream.total_size
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.namelist() == ["ghost.txt"]


def test_items_from_folder(share_tree):
    items = items_from_folder(share_tree / "photos")
    names = {i.arcname for i in items}
    assert "photos/one.jpg" in names
    assert "photos/2024/three.txt" in names


def test_estimate_zip_size(share_tree):
    items = _items(share_tree)
    assert estimate_zip_size(items, 0) == sum(i.size for i in items)
    assert estimate_zip_size(items, 6) < sum(i.size for i in items)


def test_zip_write_to(share_tree, tmp_path):
    stream = ZipStream(_items(share_tree))
    target = stream.write_to(tmp_path / "out.zip")
    assert target.stat().st_size == stream.total_size
    assert zipfile.ZipFile(target).testzip() is None


# ── files that vanish while the archive is being made ───────────────────────
def test_a_file_deleted_before_the_request_is_left_out(tmp_path):
    """Inventing a 5000-byte zero-filled entry is worse than omitting it."""
    from local_file_sharer.transfer.zipstream import ZipItem, ZipStream

    keep = tmp_path / "keep.txt"
    keep.write_text("keep me", encoding="utf-8")
    doomed = tmp_path / "doomed.bin"
    doomed.write_bytes(b"d" * 5000)

    items = [ZipItem.from_path(keep, arcname="s/keep.txt"), ZipItem.from_path(doomed, arcname="s/doomed.bin")]
    doomed.unlink()  # gone before the ZipStream is even built

    stream = ZipStream(items, compression=0)
    data = b"".join(stream.iter_stream(0, stream.total_size))
    assert len(data) == stream.total_size
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.namelist() == ["s/keep.txt"]
    assert archive.testzip() is None
    assert archive.read("s/keep.txt") == b"keep me"


def test_a_file_deleted_after_the_stream_starts_shows_a_notice(tmp_path):
    """The stream must stay byte-exact (resumable) *and* not lie about content."""
    from local_file_sharer.transfer.zipstream import ZipItem, ZipStream

    keep = tmp_path / "keep.txt"
    keep.write_text("keep me", encoding="utf-8")
    doomed = tmp_path / "doomed.bin"
    doomed.write_bytes(b"d" * 2000)

    stream = ZipStream(
        [ZipItem.from_path(keep, arcname="s/keep.txt"), ZipItem.from_path(doomed, arcname="s/doomed.bin")],
        compression=0,
    )
    doomed.unlink()  # vanishes after the layout is fixed

    data = b"".join(stream.iter_stream(0, stream.total_size))
    assert len(data) == stream.total_size, "a shrinking stream would break a resume"

    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.testzip() is None, "the archive must stay internally valid"
    payload = archive.read("s/doomed.bin")
    assert len(payload) == 2000, "the advertised size is kept"
    assert payload != bytes(len(payload)), "all-zero content would look like a real file"
    assert payload.startswith(b"LOCAL FILE SHARER:"), "say what happened, in the file itself"
