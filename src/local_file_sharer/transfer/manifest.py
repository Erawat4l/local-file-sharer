"""The share manifest: what is being shared and how it is addressed.

A manifest is the single source of truth for the HTTP server, the web UIs, the
one-liners, the torrent builder and the dashboard. IDs are short and stable so
URLs stay copy-pasteable:

    /f/3f2a9c1b            a single file
    /d/7b1c4e90/           a folder (web file browser)
    /download/all.zip      every file as a resumable zip
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import secrets
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from .. import platforms
from ..fmt import human_size

CHUNK_FOR_HASH = 1024 * 1024


def short_id(path: Path | str, *, salt: str = "") -> str:
    digest = hashlib.sha256(f"{salt}{path}".encode("utf-8", "surrogatepass")).hexdigest()
    return digest[:8]


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    suffix = path.suffix.lower()
    extra = {
        ".mkv": "video/x-matroska",
        ".m4v": "video/x-m4v",
        ".flac": "audio/flac",
        ".opus": "audio/ogg",
        ".webm": "video/webm",
        ".apk": "application/vnd.android.package-archive",
        ".iso": "application/x-iso9660-image",
        ".torrent": "application/x-bittorrent",
        ".srt": "text/plain; charset=utf-8",
        ".vtt": "text/vtt; charset=utf-8",
    }
    return extra.get(suffix, "application/octet-stream")


VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".ogv", ".ts"}
AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma", ".aiff"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".svg"}
TEXT_EXT = {".txt", ".md", ".log", ".csv", ".json", ".xml", ".yml", ".yaml", ".ini", ".toml"}
PDF_EXT = {".pdf"}


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXT:
        return "video"
    if suffix in AUDIO_EXT:
        return "audio"
    if suffix in IMAGE_EXT:
        return "image"
    if suffix in PDF_EXT:
        return "pdf"
    if suffix in TEXT_EXT:
        return "text"
    return "file"


@dataclass
class ShareItem:
    """One shareable file or folder."""

    path: Path
    root: Path
    kind: str = "file"  # file | folder
    item_id: str = ""
    files: list[FileEntry] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

    @property
    def is_dir(self) -> bool:
        return self.kind == "folder"

    @property
    def rel_path(self) -> str:
        try:
            return str(self.path.relative_to(self.root))
        except ValueError:
            return self.path.name

    @property
    def url_path(self) -> str:
        prefix = "d" if self.is_dir else "f"
        return f"/{prefix}/{self.item_id}"

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def size_text(self) -> str:
        return human_size(self.total_bytes)

    def as_dict(self, base_url: str = "") -> dict:
        base_url = base_url.rstrip("/")
        return {
            "id": self.item_id,
            "name": self.name,
            "kind": self.kind,
            "path": self.rel_path,
            "url": f"{base_url}{self.url_path}" if base_url else self.url_path,
            "bytes": self.total_bytes,
            "size": self.size_text,
            "files": self.file_count,
            "children": [f.as_dict(base_url) for f in self.files] if self.is_dir else [],
        }


@dataclass
class FileEntry:
    """A concrete file on disk plus the URL path used to fetch it."""

    path: Path
    rel: str
    size: int = 0
    mtime: float = 0.0
    file_id: str = ""
    root_name: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def mime(self) -> str:
        return guess_mime(self.path)

    @property
    def kind(self) -> str:
        return kind_of(self.path)

    @property
    def size_text(self) -> str:
        return human_size(self.size)

    @property
    def url_path(self) -> str:
        return f"/f/{self.file_id}"

    @property
    def quoted_name(self) -> str:
        return quote(self.rel)

    def as_dict(self, base_url: str = "") -> dict:
        base_url = base_url.rstrip("/")
        return {
            "id": self.file_id,
            "name": self.name,
            "rel": self.rel,
            "url": f"{base_url}{self.url_path}" if base_url else self.url_path,
            "download_url": f"{base_url}/download/{self.file_id}"
            if base_url
            else f"/download/{self.file_id}",
            "bytes": self.size,
            "size": self.size_text,
            "mime": self.mime,
            "kind": self.kind,
        }


class Manifest:
    """Everything being shared, indexed by id."""

    def __init__(
        self,
        items: Sequence[ShareItem],
        *,
        expires_at: float | None = None,
        mode: str = "download",
        title: str = "",
    ) -> None:
        self.items: list[ShareItem] = list(items)
        self.expires_at = expires_at
        self.mode = mode
        self.title = title or "LOCAL FILE SHARER"
        self.created_at = time.time()
        self.token = secrets.token_urlsafe(9)
        self._files_by_id: dict[str, FileEntry] = {}
        self._files_by_rel: dict[str, FileEntry] = {}
        self._items_by_id: dict[str, ShareItem] = {}
        self._index()

    # ── indexing ────────────────────────────────────────────────────────────
    def _index(self) -> None:
        salt = ""
        for item in self.items:
            item.item_id = item.item_id or short_id(item.path)
            self._items_by_id[item.item_id] = item
            for entry in item.files:
                entry.file_id = entry.file_id or short_id(entry.rel, salt=salt)
                # Disambiguate duplicate ids (same rel in two shares).
                while entry.file_id in self._files_by_id:
                    entry.file_id = short_id(entry.rel + entry.file_id, salt="dup")
                self._files_by_id[entry.file_id] = entry
                self._files_by_rel.setdefault(entry.rel.replace("\\", "/"), entry)

    @classmethod
    def build(
        cls,
        paths: Iterable[Path],
        *,
        mode: str = "download",
        expires_at: float | None = None,
        title: str = "",
    ) -> Manifest:
        roots = [Path(p) for p in paths]
        items = [item_from_path(p, root=p.parent) for p in roots]
        return cls(items, mode=mode, expires_at=expires_at, title=title)

    # ── access ──────────────────────────────────────────────────────────────
    @property
    def files(self) -> list[FileEntry]:
        return list(self._files_by_id.values())

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self._files_by_id.values())

    @property
    def file_count(self) -> int:
        return len(self._files_by_id)

    @property
    def folder_count(self) -> int:
        return sum(1 for i in self.items if i.is_dir)

    @property
    def empty(self) -> bool:
        return not self._files_by_id

    def get_file(self, file_id: str) -> FileEntry | None:
        return self._files_by_id.get(file_id)

    def get_file_by_rel(self, rel: str) -> FileEntry | None:
        return self._files_by_rel.get(rel.replace("\\", "/"))

    def get_item(self, item_id: str) -> ShareItem | None:
        return self._items_by_id.get(item_id)

    def iter_files(self) -> Iterator[FileEntry]:
        yield from self._files_by_id.values()

    # ── expiry ──────────────────────────────────────────────────────────────
    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at

    @property
    def ttl(self) -> float | None:
        if self.expires_at is None:
            return None
        return max(0.0, self.expires_at - time.time())

    def expiry_text(self) -> str:
        if self.expires_at is None:
            return "never expires"
        remaining = int(self.ttl or 0)
        if remaining <= 0:
            return "expired"
        hours, rem = divmod(remaining, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"expires in {hours}h {minutes:02d}m"
        if minutes:
            return f"expires in {minutes}m {seconds:02d}s"
        return f"expires in {seconds}s"

    # ── serialisation ───────────────────────────────────────────────────────
    def as_dict(self, base_url: str = "") -> dict:
        base_url = base_url.rstrip("/")
        return {
            "title": self.title,
            "mode": self.mode,
            "created": self.created_at,
            "expires_at": self.expires_at,
            "expired": self.expired,
            "file_count": self.file_count,
            "folder_count": self.folder_count,
            "total_bytes": self.total_bytes,
            "total_size": human_size(self.total_bytes),
            "items": [item.as_dict(base_url) for item in self.items],
            "files": [f.as_dict(base_url) for f in self.iter_files()],
        }

    def __len__(self) -> int:
        return len(self._files_by_id)


# ── Builder helpers ─────────────────────────────────────────────────────────
def file_entry(path: Path, *, rel: str, root_name: str = "") -> FileEntry:
    try:
        st = path.stat()
        size = st.st_size
        mtime = st.st_mtime
    except OSError:
        size = 0
        mtime = 0.0
    return FileEntry(path=path, rel=rel, size=size, mtime=mtime, root_name=root_name)


def _stays_inside(path: Path, folder: Path) -> bool:
    """True when *path* really resolves to something under *folder*."""
    try:
        path.resolve().relative_to(folder.resolve())
    except (OSError, ValueError):
        return False
    return True


def walk_folder(folder: Path, *, root_name: str = "", max_files: int = 200_000) -> list[FileEntry]:
    """Recursively collect files under *folder* with POSIX-style relative paths."""
    entries: list[FileEntry] = []
    folder_name = root_name or folder.name
    for dirpath, dirnames, filenames in os.walk(folder, onerror=lambda _e: None):
        dirnames[:] = sorted(d for d in dirnames if d not in ("$RECYCLE.BIN", "System Volume Information"))
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            try:
                rel = full.relative_to(folder).as_posix()
            except ValueError:
                rel = filename
            # A dangling symlink is listed by os.walk but cannot be served, so
            # skip it outright. This used to rely only on _stays_inside(), which
            # escapes the folder via resolve() -- but resolve() reports a broken
            # link differently across platforms and Python versions, and on
            # some of them the link resolved *inside* the folder and was
            # published as a file that 404s on download.
            if not full.exists():
                continue
            # A symlink pointing outside the shared folder would quietly publish
            # whatever it targets (~/.ssh/id_rsa for a shared ~/Documents). Only
            # files that really live under the share are listed.
            if not _stays_inside(full, folder):
                continue
            entries.append(file_entry(full, rel=f"{folder_name}/{rel}", root_name=folder_name))
            if len(entries) >= max_files:
                return entries
    return entries


def item_from_path(path: Path, *, root: Path | None = None) -> ShareItem:
    path = Path(path)
    root = root or path.parent
    if path.is_dir():
        return ShareItem(path=path, root=root, kind="folder", files=walk_folder(path))
    return ShareItem(path=path, root=root, kind="file", files=[file_entry(path, rel=path.name)])


def build_manifest(paths: Iterable[Path], **kwargs) -> Manifest:
    return Manifest.build(paths, **kwargs)


def safe_join(base: Path, rel: str) -> Path | None:
    """Resolve *rel* under *base*, rejecting traversal attempts."""
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    return candidate


def platform_safe_name(rel: str) -> str:
    return "/".join(platforms.safe_filename(part) for part in rel.split("/") if part)
