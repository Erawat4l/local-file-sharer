"""On-the-fly ZIP generation with ZIP64 support and byte-range resumes.

Folders are zipped *while they are sent*: nothing is written to disk, so a
40 GB folder starts downloading immediately and a cancelled transfer does not
leave a half-written archive behind.

Why this exists
---------------
``zipfile`` needs the compressed size of every member before it can write the
central directory. Streaming archives therefore have to either (a) know the
sizes up front or (b) be non-resumable. This module picks sizes up front so
``Range`` resumes are byte-exact:

* **Store (method 0)** for everything by default — zero CPU, and on a LAN the
  bottleneck is the network, not deflate. Media files are incompressible anyway.
* **Deflate (method 8)** for small, text-ish files when compression is enabled:
  they are compressed once into memory (bounded by :data:`INLINE_BUDGET`), which
  keeps the byte layout deterministic *and* resumable.

The writer emits a data descriptor (flag bit 3) plus ZIP64 extra fields when a
size or offset exceeds 4 GiB, and computes CRC32s for skipped ranges off-disk
when a resume starts mid-file.
"""

from __future__ import annotations

import os
import struct
import time
import zlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from ..fmt import human_size

LOCAL_SIG = 0x04034B50
CENTRAL_SIG = 0x02014B50
END_SIG = 0x06054B50
ZIP64_END_SIG = 0x06064B50
ZIP64_LOCATOR_SIG = 0x07064B50
DATA_DESC_SIG = 0x08074B50

FLAG_DATA_DESCRIPTOR = 0x0008
FLAG_UTF8 = 0x0800
U32_MAX = 0xFFFFFFFF
U16_MAX = 0xFFFF
ZIP64_THRESHOLD = U32_MAX

#: Compress at most this many bytes into memory for deflate-able members.
INLINE_BUDGET = 64 * 1024 * 1024
#: Files bigger than this are always stored (never deflated in memory).
INLINE_FILE_LIMIT = 8 * 1024 * 1024
#: Extensions that are worth deflating when small.
COMPRESSIBLE_EXT = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".c",
    ".h",
    ".cpp",
    ".rs",
    ".go",
    ".java",
    ".sh",
    ".sql",
    ".svg",
    ".srt",
    ".vtt",
    ".tex",
    ".rst",
}


@dataclass
class ZipItem:
    """A file to include in the archive."""

    arcname: str
    path: Path
    size: int = 0
    mtime: float = 0.0

    @classmethod
    def from_path(cls, path: Path, *, arcname: str | None = None) -> ZipItem:
        path = Path(path)
        try:
            st = path.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        name = (arcname or path.name).replace("\\", "/")
        return cls(arcname=name, path=path, size=size, mtime=mtime)

    @property
    def needs_zip64(self) -> bool:
        return self.size >= ZIP64_THRESHOLD

    def exists(self) -> bool:
        """A file can vanish between building the manifest and asking for a zip."""
        try:
            return self.path.is_file()
        except OSError:
            return False

    def dos_datetime(self) -> tuple[int, int]:
        try:
            tm = time.localtime(self.mtime or time.time())
        except (OverflowError, OSError, ValueError):
            tm = time.localtime()
        year = max(1980, min(tm.tm_year, 2107))
        dos_time = (tm.tm_hour << 11) | (tm.tm_min << 5) | (tm.tm_sec // 2)
        dos_date = ((year - 1980) << 9) | (tm.tm_mon << 5) | tm.tm_mday
        return dos_time, dos_date

    @property
    def compressible(self) -> bool:
        return self.path.suffix.lower() in COMPRESSIBLE_EXT and 0 < self.size <= INLINE_FILE_LIMIT


@dataclass
class _Entry:
    """A member with its *final* layout decided at construction time."""

    item: ZipItem
    offset: int
    method: int
    compressed: int
    crc: int = 0
    payload: bytes | None = field(default=None, repr=False)

    @property
    def name_bytes(self) -> bytes:
        return self.item.arcname.encode("utf-8")

    @property
    def needs_zip64(self) -> bool:
        return (
            self.item.size >= ZIP64_THRESHOLD
            or self.compressed >= ZIP64_THRESHOLD
            or self.offset >= ZIP64_THRESHOLD
        )

    @property
    def extra_len(self) -> int:
        return 20 if self.needs_zip64 else 0

    @property
    def header_size(self) -> int:
        return 30 + len(self.name_bytes) + self.extra_len

    @property
    def descriptor_size(self) -> int:
        return 24 if self.needs_zip64 else 16

    @property
    def data_start(self) -> int:
        return self.offset + self.header_size

    @property
    def data_end(self) -> int:
        return self.data_start + self.compressed

    @property
    def end(self) -> int:
        return self.data_end + self.descriptor_size


class ZipStream:
    """Deterministic, resumable ZIP byte stream."""

    def __init__(
        self,
        items: Sequence[ZipItem],
        *,
        compression: int = 1,
        comment: str = "local-file-sharer",
    ) -> None:
        # Drop entries that are already gone. Padding them with NULs produced an
        # archive whose sizes and CRCs all checked out but whose contents were
        # fabricated zeros — the downloader had no way to notice.
        self.items = [item for item in items if item.exists()]
        self.compression = max(0, min(int(compression), 9))
        self.comment = comment
        self._entries: list[_Entry] = []
        self._inflate: dict[str, bytes] = {}
        self._crc_cache: dict[tuple[str, int], int] = {}
        self._prepare()
        self._layout()

    # ── preparation (decides per-file method and sizes) ─────────────────────
    def _prepare(self) -> None:
        budget = INLINE_BUDGET if self.compression > 0 else 0
        for item in self.items:
            if budget <= 0 or not item.compressible:
                continue
            payload, crc = self._deflate_to_memory(item)
            if payload is None:
                continue
            if len(payload) >= item.size:  # incompressible: storing is cheaper
                continue
            if len(payload) > budget:
                continue
            budget -= len(payload)
            self._inflate[item.arcname] = payload
            self._crc_cache[(str(item.path), item.size)] = crc

    def _deflate_to_memory(self, item: ZipItem) -> tuple[bytes | None, int]:
        compressor = zlib.compressobj(max(1, self.compression), zlib.DEFLATED, -15)
        parts: list[bytes] = []
        crc = 0
        read_total = 0
        try:
            with open(item.path, "rb") as handle:
                while True:
                    data = handle.read(256 * 1024)
                    if not data:
                        break
                    read_total += len(data)
                    crc = zlib.crc32(data, crc)
                    parts.append(compressor.compress(data))
        except OSError:
            return None, 0
        parts.append(compressor.flush())
        if read_total != item.size:  # file changed size since listing; store it
            return None, 0
        return b"".join(parts), crc

    def _layout(self) -> None:
        offset = 0
        for item in self.items:
            payload = self._inflate.get(item.arcname)
            if payload is not None:
                entry = _Entry(
                    item=item,
                    offset=offset,
                    method=8,
                    compressed=len(payload),
                    crc=self._crc_cache[(str(item.path), item.size)],
                    payload=payload,
                )
            else:
                entry = _Entry(item=item, offset=offset, method=0, compressed=item.size)
            self._entries.append(entry)
            offset = entry.end
        self._central_offset = offset
        # The central directory length is independent of the CRC values, so it
        # can be measured now and the real bytes generated once every member has
        # been streamed (their CRCs are only known at that point).
        self._central_len = len(self._central_directory())
        self._end_record_bytes = self._build_end_record(self._central_offset, self._central_len)

    # ── sizes ───────────────────────────────────────────────────────────────
    @property
    def body_size(self) -> int:
        """Bytes before the end-of-central-directory record."""
        return self._central_offset + self._central_len

    @property
    def total_size(self) -> int:
        return self.body_size + len(self._end_record_bytes)

    @property
    def file_count(self) -> int:
        return len(self.items)

    @property
    def uncompressed_size(self) -> int:
        return sum(i.size for i in self.items)

    @property
    def compressed_size(self) -> int:
        return sum(e.compressed for e in self._entries)

    @property
    def needs_zip64(self) -> bool:
        return (
            self.body_size >= ZIP64_THRESHOLD
            or len(self.items) >= U16_MAX
            or any(e.needs_zip64 for e in self._entries)
        )

    def summary(self) -> str:
        saved = self.uncompressed_size - self.compressed_size
        ratio = f" (saves {human_size(saved)})" if saved > 0 else ""
        return f"{self.file_count} files · {human_size(self.uncompressed_size)}{ratio}"

    # ── headers ─────────────────────────────────────────────────────────────
    def _local_header(self, entry: _Entry) -> bytes:
        dos_time, dos_date = entry.item.dos_datetime()
        flags = FLAG_DATA_DESCRIPTOR | FLAG_UTF8
        extra = b""
        if entry.needs_zip64:
            extra = struct.pack("<HHQQ", 0x0001, 16, entry.item.size, entry.compressed)
        return (
            struct.pack(
                "<IHHHHHIIIHH",
                LOCAL_SIG,
                45 if entry.needs_zip64 else 20,
                flags,
                entry.method,
                dos_time,
                dos_date,
                entry.crc if entry.payload is not None else 0,
                min(entry.compressed, U32_MAX),
                min(entry.item.size, U32_MAX),
                len(entry.name_bytes),
                len(extra),
            )
            + entry.name_bytes
            + extra
        )

    def _descriptor(self, entry: _Entry) -> bytes:
        if entry.needs_zip64:
            return struct.pack("<IIQQ", DATA_DESC_SIG, entry.crc, entry.compressed, entry.item.size)
        return struct.pack("<IIII", DATA_DESC_SIG, entry.crc, entry.compressed, entry.item.size)

    def _central_directory(self) -> bytes:
        out = bytearray()
        for entry in self._entries:
            dos_time, dos_date = entry.item.dos_datetime()
            flags = FLAG_DATA_DESCRIPTOR | FLAG_UTF8
            payload = b""
            if entry.item.size >= ZIP64_THRESHOLD:
                payload += struct.pack("<QQ", entry.item.size, entry.compressed)
            if entry.offset >= ZIP64_THRESHOLD:
                payload += struct.pack("<Q", entry.offset)
            extra = struct.pack("<HH", 0x0001, len(payload)) + payload if payload else b""
            out += struct.pack(
                "<IHHHHHHIIIHHHHHII",
                CENTRAL_SIG,
                45 if entry.needs_zip64 else 20,  # version made by
                45 if entry.needs_zip64 else 20,  # version needed
                flags,
                entry.method,
                dos_time,
                dos_date,
                entry.crc,
                min(entry.compressed, U32_MAX),
                min(entry.item.size, U32_MAX),
                len(entry.name_bytes),
                len(extra),
                0,  # comment length
                0,  # disk number
                0,  # internal attrs
                0o100644 << 16,  # external attrs: -rw-r--r--
                min(entry.offset, U32_MAX),
            )
            out += entry.name_bytes + extra
        return bytes(out)

    def _build_end_record(self, central_offset: int, central_size: int) -> bytes:
        count = len(self.items)
        record = struct.pack(
            "<IHHHHIIH",
            END_SIG,
            0,
            0,
            min(count, U16_MAX),
            min(count, U16_MAX),
            min(central_size, U32_MAX),
            min(central_offset, U32_MAX),
            0,
        )
        if not self.needs_zip64:
            return record
        zip64 = struct.pack(
            "<IQHHIIQQQQ",
            ZIP64_END_SIG,
            44,  # size of the remaining record
            45,
            45,
            0,
            0,
            count,
            count,
            central_size,
            central_offset,
        )
        locator = struct.pack("<IIQI", ZIP64_LOCATOR_SIG, 0, self.body_size, 1)
        return record + zip64 + locator

    # ── streaming ───────────────────────────────────────────────────────────
    def iter_stream(self, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        """Yield archive bytes from ``start`` (inclusive) to ``end`` (exclusive)."""
        target_end = self.total_size if end is None else min(end, self.total_size)
        if start >= target_end:
            return
        for entry in self._entries:
            header = self._local_header(entry)
            chunk = self._slice(header, entry.offset, start, target_end)
            if chunk:
                yield chunk
            if entry.data_end > start:
                yield from self._stream_data(entry, start, target_end)
            elif entry.payload is None:
                entry.crc = self._crc_of(entry, entry.item.size)
            descriptor = self._descriptor(entry)
            chunk = self._slice(descriptor, entry.data_end, start, target_end)
            if chunk:
                yield chunk
            if entry.end >= target_end:
                return
        # Every member is now final, so the central directory can be emitted.
        blob = self._central_directory() + self._end_record_bytes
        chunk = self._slice(blob, self._central_offset, start, target_end)
        if chunk:
            yield chunk

    @staticmethod
    def _slice(data: bytes, offset: int, start: int, end: int) -> bytes:
        seg_start = max(start, offset)
        seg_end = min(end, offset + len(data))
        if seg_start >= seg_end:
            return b""
        return data[seg_start - offset : seg_end - offset]

    def _stream_data(self, entry: _Entry, start: int, end: int) -> Iterator[bytes]:
        if entry.payload is not None:
            chunk = self._slice(entry.payload, entry.data_start, start, end)
            if chunk:
                yield chunk
            return
        # Stored member: stream straight from disk with a byte-exact skip.
        skip = max(0, start - entry.data_start)
        crc = 0
        if skip:
            crc = self._crc_of(entry, skip)
        position = entry.data_start + skip
        remaining = entry.item.size - skip
        try:
            handle: BinaryIO = open(entry.item.path, "rb")  # noqa: SIM115 - closed below
        except OSError:
            yield from self._emit_marker(entry, start, end, skip, crc)
            return
        try:
            if skip:
                handle.seek(skip)
            while remaining > 0 and position < end:
                data = handle.read(min(1024 * 1024, remaining, end - position + 1))
                if not data:
                    break
                remaining -= len(data)
                crc = zlib.crc32(data, crc)
                seg_start = max(start, position)
                seg_end = min(end, position + len(data))
                if seg_start < seg_end:
                    yield data[seg_start - position : seg_end - position]
                position += len(data)
        finally:
            handle.close()
        entry.crc = crc

    def _emit_marker(self, entry: _Entry, start: int, end: int, skip: int, crc: int) -> Iterator[bytes]:
        """A file vanished mid-transfer: keep the archive valid but *honest*.

        The entry keeps its advertised size so the stream stays resumable, and
        the payload is a visible marker rather than NULs — an all-zero file
        downloads perfectly, verifies its CRC and is silently wrong, which is
        the worst possible outcome for someone backing up a folder.
        """
        notice = (
            f"local-file-sharer: {entry.item.arcname} disappeared while this archive was being built.\n"
        ).encode()
        block = (notice * (256 * 1024 // max(1, len(notice)) + 1))[: 256 * 1024]
        position = entry.data_start + skip
        remaining = entry.item.size - skip
        while remaining > 0:
            piece = block[: min(256 * 1024, remaining)]
            remaining -= len(piece)
            crc = zlib.crc32(piece, crc)
            seg_start = max(start, position)
            seg_end = min(end, position + len(piece))
            if seg_start < seg_end:
                yield piece[seg_start - position : seg_end - position]
            position += len(piece)
        entry.crc = crc

    def _crc_of(self, entry: _Entry, length: int) -> int:
        key = (str(entry.item.path), length)
        cached = self._crc_cache.get(key)
        if cached is not None:
            return cached
        crc = 0
        remaining = min(length, entry.item.size)
        try:
            with open(entry.item.path, "rb") as handle:
                while remaining > 0:
                    data = handle.read(min(1024 * 1024, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    crc = zlib.crc32(data, crc)
        except OSError:
            return 0
        self._crc_cache[key] = crc
        return crc

    # ── convenience ─────────────────────────────────────────────────────────
    def write_to(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            for chunk in self.iter_stream():
                handle.write(chunk)
        return path


# ── builders ────────────────────────────────────────────────────────────────
def items_from_folder(folder: Path, *, prefix: str | None = None) -> list[ZipItem]:
    """Every file under *folder*, with arcnames rooted at the folder name."""
    from .manifest import walk_folder  # noqa: PLC0415 - avoid an import cycle

    root_name = prefix or folder.name
    return [
        ZipItem(arcname=entry.rel.replace("\\", "/"), path=entry.path, size=entry.size, mtime=entry.mtime)
        for entry in walk_folder(folder, root_name=root_name)
    ]


def items_from_files(paths: Iterable[Path]) -> list[ZipItem]:
    return [ZipItem.from_path(path) for path in paths]


def estimate_zip_size(items: Sequence[ZipItem], compression: int = 1) -> int:
    """Rough archive size before a byte is sent."""
    if compression <= 0:
        return sum(i.size for i in items)
    total = 0
    for item in items:
        total += item.size if not item.compressible else int(item.size * 0.45)
    return total


def is_zip_worthwhile(folder: Path) -> bool:
    """Folders always benefit from a single download; kept for API symmetry."""
    try:
        return folder.is_dir()
    except OSError:
        return False


def describe(items: Sequence[ZipItem]) -> str:
    total = sum(i.size for i in items)
    return f"{len(items)} file{'s' if len(items) != 1 else ''}, {human_size(total)}"


def open_bytes(path: Path, start: int = 0, length: int | None = None) -> Iterator[bytes]:
    """Simple file reader used by the torrent webseed."""
    with open(path, "rb") as handle:
        handle.seek(max(0, start))
        remaining = length if length is not None else os.fstat(handle.fileno()).st_size - start
        while remaining and remaining > 0:
            data = handle.read(min(1024 * 1024, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


@dataclass
class ZipStats:
    files: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    started: float = 0.0
    finished: float = 0.0
    fields: dict = field(default_factory=dict)

    def ratio(self) -> float:
        if not self.bytes_in:
            return 1.0
        return self.bytes_out / self.bytes_in
