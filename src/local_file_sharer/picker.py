"""File picker model: directory listing, fuzzy search, selection parsing,
whole-folder analysis and shareable-status checks.

Kept free of Rich so it can be unit-tested headlessly and reused by the CLI,
the web server and the torrent builder.
"""

from __future__ import annotations

import fnmatch
import os
import re
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import platforms
from .errors import PickerError
from .fmt import human_size

# ── Icons ────────────────────────────────────────────────────────────────────
EXTENSION_ICONS: dict[str, str] = {
    # video
    "mp4": "🎬",
    "mkv": "🎬",
    "avi": "🎬",
    "mov": "🎬",
    "webm": "🎬",
    "m4v": "🎬",
    "flv": "🎬",
    "wmv": "🎬",
    "mpg": "🎬",
    "mpeg": "🎬",
    "m3u8": "🎬",
    "mts": "🎬",
    # audio
    "mp3": "🎵",
    "flac": "🎵",
    "wav": "🎵",
    "m4a": "🎵",
    "aac": "🎵",
    "ogg": "🎵",
    "opus": "🎵",
    "wma": "🎵",
    "aiff": "🎵",
    "mid": "🎵",
    # images
    "jpg": "🖼️",
    "jpeg": "🖼️",
    "png": "🖼️",
    "gif": "🖼️",
    "webp": "🖼️",
    "bmp": "🖼️",
    "svg": "🖼️",
    "ico": "🖼️",
    "heic": "🖼️",
    "avif": "🖼️",
    "tiff": "🖼️",
    # docs
    "pdf": "📕",
    "doc": "📘",
    "docx": "📘",
    "odt": "📘",
    "txt": "📄",
    "md": "📝",
    "rtf": "📄",
    "epub": "📚",
    "mobi": "📚",
    "djvu": "📚",
    # sheets / slides
    "xls": "📊",
    "xlsx": "📊",
    "csv": "📊",
    "ods": "📊",
    "ppt": "📽️",
    "pptx": "📽️",
    "odp": "📽️",
    # archives
    "zip": "🗜️",
    "rar": "🗜️",
    "7z": "🗜️",
    "tar": "🗜️",
    "gz": "🗜️",
    "bz2": "🗜️",
    "xz": "🗜️",
    "zst": "🗜️",
    "iso": "💿",
    "apk": "📦",
    "exe": "⚙️",
    "msi": "⚙️",
    "deb": "📦",
    "rpm": "📦",
    "pkg": "📦",
    "dmg": "💿",
    "jar": "☕",
    # code
    "py": "🐍",
    "js": "🟨",
    "ts": "🟦",
    "tsx": "🟦",
    "jsx": "🟨",
    "json": "🧾",
    "yml": "🧾",
    "yaml": "🧾",
    "toml": "🧾",
    "xml": "🧾",
    "html": "🌐",
    "css": "🎨",
    "sh": "⌨️",
    "bash": "⌨️",
    "ps1": "⌨️",
    "bat": "⌨️",
    "c": "🔧",
    "h": "🔧",
    "cpp": "🔧",
    "rs": "🦀",
    "go": "🐹",
    "java": "☕",
    "kt": "🟪",
    "sql": "🗃️",
    # misc
    "torrent": "🌱",
    "log": "🧻",
    "ini": "🧾",
    "cfg": "🧾",
    "conf": "🧾",
}

SPECIAL_ICONS: dict[str, str] = {
    "android": "🤖",
    "dcim": "📷",
    "download": "⬇️",
    "downloads": "⬇️",
    "documents": "📁",
    "movies": "🎬",
    "music": "🎵",
    "pictures": "🖼️",
    "videos": "🎬",
    "node_modules": "📦",
    ".git": "🔧",
}


def icon_for(path: Path, *, is_dir: bool | None = None, icons: bool = True) -> str:
    if not icons:
        return ""
    if is_dir is None:
        try:
            is_dir = path.is_dir()
        except OSError:
            is_dir = False
    if is_dir:
        return SPECIAL_ICONS.get(path.name.lower(), "📁")
    suffix = path.suffix.lower().lstrip(".")
    if not suffix:
        return "📄"
    return EXTENSION_ICONS.get(suffix, "📄")


# ── Entries ──────────────────────────────────────────────────────────────────
@dataclass
class Entry:
    path: Path
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    hidden: bool = False
    readable: bool = True
    link: bool = False
    icon: str = "📄"

    @property
    def display_size(self) -> str:
        if self.is_dir:
            return "folder"
        return human_size(self.size)

    @property
    def suffix(self) -> str:
        return "" if self.is_dir else self.path.suffix.lower()


def parent_of(path: Path) -> Path | None:
    try:
        parent = path.parent
    except (OSError, RuntimeError):
        return None
    if parent == path:
        return None
    return parent


def list_entries(
    directory: Path,
    *,
    show_hidden: bool = False,
    icons: bool = True,
    files_only: bool = False,
) -> list[Entry]:
    """List a directory: folders first, then files, both case-insensitive."""
    directory = platforms.normalize_path(directory)
    if not directory.is_dir():
        raise PickerError(
            f"{directory} is not a folder",
            hints=["Press `d` to list drives", "Press `h` to go home"],
        )
    try:
        names = list(os.scandir(directory))
    except PermissionError as exc:
        raise PickerError(
            f"Permission denied: {directory}",
            hints=[
                "Termux: run `termux-setup-storage` once to grant storage access",
                "Windows: run the terminal as your normal user, not as a service",
                "Try a folder inside your home directory instead",
            ],
        ) from exc
    except OSError as exc:
        raise PickerError(f"Could not read {directory}: {exc}") from exc

    entries: list[Entry] = []
    for item in names:
        try:
            is_dir = item.is_dir(follow_symlinks=True)
        except OSError:
            is_dir = False
        name = item.name
        hidden = name.startswith(".") or (platforms.WINDOWS and _is_hidden_win(item))
        if hidden and not show_hidden:
            continue
        if files_only and is_dir:
            continue
        path = Path(item.path)
        size = 0
        mtime = 0.0
        readable = True
        link = False
        try:
            st = item.stat(follow_symlinks=True)
            size = st.st_size
            mtime = st.st_mtime
            link = stat.S_ISLNK(item.stat(follow_symlinks=False).st_mode)
            readable = os.access(path, os.R_OK) if not is_dir else _dir_readable(path)
        except OSError:
            readable = False
        entries.append(
            Entry(
                path=path,
                name=name,
                is_dir=is_dir,
                size=size,
                mtime=mtime,
                hidden=hidden,
                readable=readable,
                link=link,
                icon=icon_for(path, is_dir=is_dir, icons=icons),
            )
        )
    entries.sort(key=lambda e: (not e.is_dir, e.name.casefold()))
    return entries


def _is_hidden_win(item: os.DirEntry) -> bool:
    try:
        attrs = item.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_HIDDEN)


def _dir_readable(path: Path) -> bool:
    try:
        with os.scandir(path) as scan:
            next(iter(scan), None)
        return True
    except OSError:
        return False


# ── Fuzzy search ─────────────────────────────────────────────────────────────
def fuzzy_score(query: str, text: str) -> int:
    """Subsequence score with bonuses for word starts and consecutive runs.

    Higher is better; ``<= 0`` means no match.
    """
    if not query:
        return 1
    q = query.lower()
    t = text.lower()
    if q in t:
        base = 1000 - t.index(q)
        if t.startswith(q):
            base += 250
        return base - len(t)
    score = 0
    ti = 0
    streak = 0
    for char in q:
        found = t.find(char, ti)
        if found == -1:
            return 0
        if found == ti:
            streak += 1
            score += 8 + streak * 2
        else:
            streak = 0
            score += 2 if found == 0 or t[found - 1] in " _-./" else 1
        ti = found + 1
    return max(1, score - (len(t) - len(q)) // 4)


def fuzzy_filter(entries: Sequence[Entry], query: str, *, limit: int | None = None) -> list[Entry]:
    if not query.strip():
        return list(entries)
    patterns = [p for p in query.split() if p]
    scored: list[tuple[int, Entry]] = []
    for entry in entries:
        total = 0
        for pattern in patterns:
            if any(ch in pattern for ch in "*?["):
                if fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
                    total += 500
                    continue
                total = 0
                break
            part = fuzzy_score(pattern, entry.name)
            if part <= 0:
                # allow matching against the parent folder too
                part = fuzzy_score(pattern, str(entry.path)) // 4
            if part <= 0:
                total = 0
                break
            total += part
        if total:
            scored.append((total + (60 if entry.is_dir else 0), entry))
    scored.sort(key=lambda pair: (-pair[0], not pair[1].is_dir, pair[1].name.casefold()))
    result = [entry for _, entry in scored]
    return result[:limit] if limit else result


# ── Selection parsing ────────────────────────────────────────────────────────
_SELECTION_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


def parse_selection(text: str, count: int) -> list[int]:
    """Parse ``6,7,8`` / ``6-8`` / ``6 - 8`` / ``all`` into 0-based indices.

    Raises :class:`PickerError` with a friendly message on bad input.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    lowered = raw.lower()
    if lowered in ("a", "all", "*"):
        return list(range(count))
    if lowered in ("none", "-"):
        return []
    indices: list[int] = []
    for chunk in re.split(r"[,\s]+", raw):
        if not chunk:
            continue
        match = _SELECTION_RE.match(chunk)
        if not match:
            raise PickerError(
                f"Could not read {chunk!r}",
                hints=["Use 3, 3,7,9 or 3-9", "Type `?` for the full command list"],
            )
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if end < start:
            start, end = end, start
        for value in range(start, end + 1):
            if not (1 <= value <= count):
                raise PickerError(
                    f"{value} is out of range (1-{count})",
                    hints=["Enter the number printed next to the file"],
                )
            if value - 1 not in indices:
                indices.append(value - 1)
    return indices


# ── Folder analysis / shareable checks ───────────────────────────────────────
BAD_DEVICE_PREFIXES = ("/dev/", "/proc/", "/sys/")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"}


@dataclass
class SelectionSummary:
    files: int = 0
    folders: int = 0
    total_bytes: int = 0
    unreadable: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0

    @property
    def shareable(self) -> bool:
        return self.files > 0 or self.folders > 0

    @property
    def size_text(self) -> str:
        return human_size(self.total_bytes)

    def headline(self) -> str:
        parts = [
            f"{self.files} file{'s' if self.files != 1 else ''}",
            f"{self.folders} subfolder{'s' if self.folders != 1 else ''}",
            self.size_text,
        ]
        return " · ".join(parts)

    def status_text(self) -> str:
        if not self.shareable:
            return "nothing to share"
        if self.unreadable:
            return f"shareable ({len(self.unreadable)} item(s) unreadable, will be skipped)"
        return "shareable"

    def as_dict(self) -> dict[str, object]:
        return {
            "files": self.files,
            "folders": self.folders,
            "bytes": self.total_bytes,
            "unreadable": [str(p) for p in self.unreadable],
            "warnings": list(self.warnings),
            "shareable": self.shareable,
        }


def analyze(paths: Iterable[Path], *, max_entries: int = 100_000) -> SelectionSummary:
    """Count files, subfolders and bytes for a mixed file/folder selection."""
    summary = SelectionSummary()
    for raw in paths:
        path = Path(raw)
        try:
            st = path.lstat()
        except FileNotFoundError:
            summary.warnings.append(f"{path.name}: no longer exists")
            summary.unreadable.append(path)
            continue
        except OSError as exc:
            summary.warnings.append(f"{path.name}: {exc.strerror or exc}")
            summary.unreadable.append(path)
            continue
        if stat.S_ISDIR(st.st_mode):
            if not os.access(path, os.R_OK | os.X_OK):
                summary.unreadable.append(path)
                summary.warnings.append(f"{path.name}: permission denied")
                continue
            folder_bytes, folder_files, subfolders = platforms.folder_size(path, limit_files=max_entries)
            summary.total_bytes += folder_bytes
            summary.files += folder_files
            summary.folders += subfolders + 1
            if folder_files == 0 and subfolders == 0:
                summary.warnings.append(f"{path.name}: folder is empty")
        elif stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            if not os.access(path, os.R_OK):
                summary.unreadable.append(path)
                summary.warnings.append(f"{path.name}: permission denied")
                continue
            summary.files += 1
            summary.total_bytes += st.st_size
        else:
            summary.skipped += 1
            summary.warnings.append(f"{path.name}: not a regular file (skipped)")
    return summary


def shareable_reasons(path: Path) -> list[str]:
    """Human reasons why *path* cannot be shared as-is (empty list = fine)."""
    reasons: list[str] = []
    text = str(path)
    if text.startswith(BAD_DEVICE_PREFIXES):
        reasons.append("system device paths are not shareable")
    try:
        st = path.lstat()
    except OSError as exc:
        return [f"cannot stat: {exc.strerror or exc}"]
    if stat.S_ISSOCK(st.st_mode):
        reasons.append("sockets cannot be sent over HTTP")
    elif stat.S_ISFIFO(st.st_mode):
        reasons.append("named pipes have no fixed size")
    elif stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode):
        reasons.append("device files cannot be shared")
    if not os.access(path, os.R_OK):
        reasons.append("permission denied")
    if platforms.WINDOWS and path.name.split(".")[0].upper() in WINDOWS_RESERVED:
        reasons.append("reserved Windows filename")
    return reasons


def collect_files(paths: Iterable[Path], *, follow_links: bool = False) -> list[Path]:
    """Expand a selection (files + folders) into a flat list of readable files."""
    out: list[Path] = []

    def _walk(directory: Path) -> None:
        try:
            with os.scandir(directory) as scan:
                for item in scan:
                    try:
                        if item.is_dir(follow_symlinks=follow_links):
                            _walk(Path(item.path))
                        elif item.is_file(follow_symlinks=True):
                            out.append(Path(item.path))
                    except OSError:
                        continue
        except OSError:
            return

    for raw in paths:
        path = Path(raw)
        try:
            if path.is_dir():
                _walk(path)
            elif path.is_file():
                out.append(path)
        except OSError:
            continue
    return out


def common_root(paths: Sequence[Path]) -> Path:
    """Nearest common ancestor (used to keep the URL tree tidy)."""
    if not paths:
        return Path.cwd()
    try:
        resolved = [p.resolve() for p in paths]
    except OSError:
        resolved = list(paths)
    if len(resolved) == 1:
        single = resolved[0]
        return single if single.is_dir() else single.parent
    try:
        return Path(os.path.commonpath([str(p) for p in resolved]))
    except ValueError:
        return Path.cwd()


def resolve_user_path(text: str, *, base: Path | None = None) -> Path:
    """Turn ``~/x``, ``C:\\x``, relative paths and quoted paths into a Path."""
    cleaned = text.strip().strip('"').strip("'")
    path = platforms.normalize_path(cleaned)
    if not path.is_absolute() and base is not None:
        path = base / path
    return path
