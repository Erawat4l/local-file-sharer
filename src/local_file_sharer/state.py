"""Small, atomic JSON state store: recents, favourites, totals, history.

Lives in the cache directory (``~/.cache/local-file-sharer/state.json`` on
Linux/Termux, ``%LOCALAPPDATA%`` on Windows) so it never pollutes a repo and is
safe to delete at any time.

Writes are atomic (tmp file + ``os.replace``) and best-effort: a corrupt or
read-only state file never breaks sharing.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import platforms

STATE_VERSION = 1
MAX_HISTORY = 50


def default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "recent_files": [],  # [{"path": str, "at": epoch, "count": n}]
        "favorite_folders": [],  # [{"path": str, "label": str, "at": epoch}]
        "history": [],  # [{"mode", "path", "files", "bytes", "at", "url"}]
        "totals": {"bytes_sent": 0, "files_sent": 0, "shares": 0, "downloads": 0},
        "last_dir": "",
        "theme": "",
        "first_run": True,
        "install_id": "",
    }


def _install_id() -> str:
    return os.urandom(6).hex()


@dataclass
class State:
    """Thread-safe handle around the JSON document."""

    path: Path = field(default_factory=platforms.state_path)
    data: dict[str, Any] = field(default_factory=default_state)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ── persistence ─────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path | None = None) -> State:
        path = path or platforms.state_path()
        data = default_state()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
        if not data.get("install_id"):
            data["install_id"] = _install_id()
        return cls(path=path, data=data)

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self.data, indent=2, ensure_ascii=False)
        try:
            platforms.ensure_dir(self.path.parent)
            tmp = self.path.with_suffix(".json.tmp")
            # The history holds absolute paths and share URLs; on a multi-user
            # machine that is nobody else's business. Create it 0600.
            descriptor = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # ── recents ─────────────────────────────────────────────────────────────
    def recent_files(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.data.get("recent_files", []))

    def add_recent(self, path: str | Path, *, limit: int = 12) -> None:
        text = str(Path(path))
        now = time.time()
        with self._lock:
            recents = [r for r in self.data.get("recent_files", []) if r.get("path") != text]
            entry = next((r for r in self.data.get("recent_files", []) if r.get("path") == text), None)
            count = int(entry.get("count", 0)) + 1 if entry else 1
            recents.insert(0, {"path": text, "at": now, "count": count})
            self.data["recent_files"] = recents[: max(1, limit)]
        self.save()

    def clear_recents(self) -> None:
        with self._lock:
            self.data["recent_files"] = []
        self.save()

    # ── favourites ──────────────────────────────────────────────────────────
    def favorite_folders(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.data.get("favorite_folders", []))

    def is_favorite(self, path: str | Path) -> bool:
        text = str(Path(path))
        return any(f.get("path") == text for f in self.favorite_folders())

    def toggle_favorite(self, path: str | Path, *, label: str = "", limit: int = 20) -> bool:
        """Add/remove *path*; returns True when it is now a favourite."""
        text = str(Path(path))
        with self._lock:
            favs = list(self.data.get("favorite_folders", []))
            remaining = [f for f in favs if f.get("path") != text]
            if len(remaining) == len(favs):
                remaining.insert(0, {"path": text, "label": label or Path(text).name, "at": time.time()})
                self.data["favorite_folders"] = remaining[: max(1, limit)]
                now_favorite = True
            else:
                self.data["favorite_folders"] = remaining
                now_favorite = False
        self.save()
        return now_favorite

    def remove_favorite(self, path: str | Path) -> None:
        text = str(Path(path))
        with self._lock:
            self.data["favorite_folders"] = [
                f for f in self.data.get("favorite_folders", []) if f.get("path") != text
            ]
        self.save()

    # ── last directory / theme ──────────────────────────────────────────────
    @property
    def last_dir(self) -> Path | None:
        raw = self.data.get("last_dir") or ""
        if raw and Path(raw).is_dir():
            return Path(raw)
        return None

    @last_dir.setter
    def last_dir(self, path: str | Path | None) -> None:
        with self._lock:
            self.data["last_dir"] = str(path) if path else ""

    def remember_dir(self, path: str | Path) -> None:
        self.last_dir = path
        self.save()

    @property
    def theme(self) -> str:
        return str(self.data.get("theme") or "")

    @theme.setter
    def theme(self, value: str) -> None:
        with self._lock:
            self.data["theme"] = value or ""
        self.save()

    # ── totals + history ────────────────────────────────────────────────────
    def add_transfer(self, *, bytes_sent: int, files: int) -> None:
        with self._lock:
            totals = self.data.setdefault("totals", {})
            totals["bytes_sent"] = int(totals.get("bytes_sent", 0)) + int(bytes_sent)
            totals["files_sent"] = int(totals.get("files_sent", 0)) + int(files)
        self.save()

    def add_download(self, count: int = 1) -> None:
        with self._lock:
            totals = self.data.setdefault("totals", {})
            totals["downloads"] = int(totals.get("downloads", 0)) + int(count)
        self.save()

    def add_share(self) -> None:
        with self._lock:
            totals = self.data.setdefault("totals", {})
            totals["shares"] = int(totals.get("shares", 0)) + 1
        self.save()

    def totals(self) -> dict[str, int]:
        with self._lock:
            return dict(self.data.get("totals", {}))

    def add_history(self, entry: dict[str, Any], *, limit: int = MAX_HISTORY) -> None:
        record = dict(entry)
        record.setdefault("at", time.time())
        with self._lock:
            history = [record, *self.data.get("history", [])]
            self.data["history"] = history[:limit]
        self.save()

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.data.get("history", []))

    def first_run(self) -> bool:
        return bool(self.data.get("first_run", True))

    def mark_started(self) -> None:
        with self._lock:
            self.data["first_run"] = False
        self.save()
