"""Torrent mode: .torrent creation, magnet links, a built-in tracker and webseed.

Everything here is pure Python and dependency-free so it works on Termux too.

What "torrent mode" gives you
-----------------------------
* A real ``.torrent`` file (bencoded, v1/BEP-3, multi-file) written to the share
  folder, plus a ``magnet:`` link that carries the same info-hash.
* A tiny **HTTP tracker** (``/announce`` + ``/scrape``) hosted by the share
  server, so the two devices can find each other without any external tracker.
* An **HTTP webseed** (BEP-19 ``url-list``), which is what actually makes the
  download start instantly: the peer pulls pieces straight from this machine
  over HTTP with Range requests.
* Progress reporting for the dashboard (pieces sent by the webseed).

The torrent is intentionally "seeded by the sharer only": pieces come from the
HTTP webseed, and peers exchange metadata via the built-in tracker. A normal
BitTorrent client such as qBittorrent, aria2 or Transmission picks it up
without configuration.
"""

from __future__ import annotations

import hashlib
import os
import socket
import struct
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import APP_NAME, __version__, platforms
from ..errors import TorrentError
from .manifest import FileEntry
from .zipstream import ZipItem

PIECE_MIN = 32 * 1024
PIECE_MAX = 4 * 1024 * 1024
BENCODE_INT = b"i"
TRACKER_INTERVAL = 60


# ─────────────────────────────────────────────────────────────────────────────
# Bencode
# ─────────────────────────────────────────────────────────────────────────────
def bencode(value: Any) -> bytes:
    """Encode ints, bytes, str, list and dict per BEP-3 (keys sorted)."""
    if isinstance(value, bool):
        return bencode(int(value))
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b"e"
    if isinstance(value, str):
        return bencode(value.encode("utf-8"))
    if isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
        return str(len(payload)).encode("ascii") + b":" + payload
    if isinstance(value, (list, tuple)):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        out = bytearray(b"d")
        for key in sorted(value, key=lambda k: k.encode("utf-8") if isinstance(k, str) else bytes(k)):
            raw_key = key.encode("utf-8") if isinstance(key, str) else bytes(key)
            out += bencode(raw_key)
            out += bencode(value[key])
        out += b"e"
        return bytes(out)
    raise TorrentError(f"cannot bencode {type(value).__name__}")


def bdecode(data: bytes) -> Any:
    """Decode bencode; raises :class:`TorrentError` on malformed input."""
    index = 0

    def parse() -> Any:
        nonlocal index
        if index >= len(data):
            raise TorrentError("truncated bencode")
        char = data[index : index + 1]
        if char == b"i":
            try:
                end = data.index(b"e", index)
                value = int(data[index + 1 : end])
            except (ValueError, IndexError) as exc:
                raise TorrentError("malformed integer in bencode") from exc
            index = end + 1
            return value
        if char == b"l":
            index += 1
            items = []
            while data[index : index + 1] != b"e":
                items.append(parse())
            index += 1
            return items
        if char == b"d":
            index += 1
            mapping: dict[bytes, Any] = {}
            while data[index : index + 1] != b"e":
                key = parse()
                mapping[bytes(key)] = parse()
            index += 1
            return mapping
        if char.isdigit():
            colon = data.index(b":", index)
            length = int(data[index:colon])
            start = colon + 1
            value = data[start : start + length]
            if len(value) != length:
                raise TorrentError("truncated bencode string")
            index = start + length
            return value
        raise TorrentError(f"unexpected bencode byte {char!r}")

    return parse()


def info_hash(info: dict) -> bytes:
    """SHA-1 of the bencoded info dictionary (the torrent's identity)."""
    return hashlib.sha1(bencode(info)).digest()


def info_hash_hex(info: dict) -> str:
    return info_hash(info).hex()


# ─────────────────────────────────────────────────────────────────────────────
# Piece planning + hashing
# ─────────────────────────────────────────────────────────────────────────────
def choose_piece_length(total_bytes: int) -> int:
    if total_bytes >= 8 * 1024**3:
        return 4 * 1024 * 1024
    if total_bytes >= 2 * 1024**3:
        return 2 * 1024 * 1024
    if total_bytes >= 256 * 1024**2:
        return 1024 * 1024
    if total_bytes >= 32 * 1024**2:
        return 512 * 1024
    if total_bytes >= 4 * 1024**2:
        return 256 * 1024
    return 64 * 1024


@dataclass
class TorrentFile:
    path: Path
    rel: str
    size: int
    root_name: str = ""

    @property
    def parts(self) -> list[str]:
        """Path components inside the torrent (the first dir is the torrent name)."""
        parts = [p for p in self.rel.replace("\\", "/").split("/") if p]
        # ``share/photos/a.jpg`` → ``photos/a.jpg``: the torrent name is the root.
        if len(parts) > 1 and parts[0] == self.root_name:
            parts = parts[1:]
        return parts or [self.path.name]

    def as_info(self) -> dict:
        return {"length": self.size, "path": self.parts}


@dataclass
class TorrentBuild:
    content: bytes
    info: dict
    name: str
    piece_count: int
    piece_length: int
    total_bytes: int
    tracker_url: str
    webseed_url: str
    created_at: float = field(default_factory=time.time)

    @property
    def info_hash(self) -> bytes:
        return info_hash(self.info)

    @property
    def info_hash_hex(self) -> str:
        return self.info_hash.hex()

    @property
    def magnet(self) -> str:
        return build_magnet(
            self.info_hash_hex,
            name=self.name,
            tracker=self.tracker_url,
            webseeds=[self.webseed_url],
            total_bytes=self.total_bytes,
        )

    def save(self, path: Path | None = None) -> Path:
        target = path or Path.cwd() / f"{self.name}.torrent"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.content)
        return target


def hash_pieces(files: Sequence[TorrentFile], piece_length: int, *, progress=None) -> bytes:
    """Concatenate all file bytes logically and SHA-1 them per piece."""
    pieces = bytearray()
    buffer = bytearray()
    processed = 0
    for tf in files:
        try:
            handle = open(tf.path, "rb")  # noqa: SIM115 - closed in finally
        except OSError as exc:
            raise TorrentError(
                f"cannot read {tf.path.name} while hashing",
                hints=["Check file permissions", f"Detail: {exc}"],
            ) from exc
        try:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                buffer += chunk
                while len(buffer) >= piece_length:
                    pieces += hashlib.sha1(buffer[:piece_length]).digest()
                    del buffer[:piece_length]
                    processed += 1
                    if progress and processed % 8 == 0:
                        progress(processed)
        finally:
            handle.close()
    if buffer:
        pieces += hashlib.sha1(bytes(buffer)).digest()
        processed += 1
        if progress:
            progress(processed)
    return bytes(pieces)


def build_torrent(
    files: Sequence[TorrentFile],
    *,
    name: str,
    tracker_url: str = "",
    webseed_url: str = "",
    piece_length: int = 0,
    private: bool = False,
    progress=None,
) -> TorrentBuild:
    """Create a v1 multi-file torrent for *files*."""
    if not files:
        raise TorrentError(
            "nothing to seed",
            hints=["Select at least one file (folders are expanded automatically)"],
        )
    total = sum(f.size for f in files)
    if total <= 0:
        raise TorrentError("all selected files are empty", hints=["Empty files cannot form pieces"])
    piece_len = piece_length or choose_piece_length(total)
    piece_len = max(PIECE_MIN, min(piece_len, PIECE_MAX))
    if len(files) == 1 and "/" not in files[0].rel.replace("\\", "/"):
        info: dict[str, Any] = {
            "name": name,
            "length": files[0].size,
            "piece length": piece_len,
            "pieces": hash_pieces(files, piece_len, progress=progress),
        }
    else:
        info = {
            "name": name,
            "piece length": piece_len,
            "pieces": hash_pieces(files, piece_len, progress=progress),
            "files": [f.as_info() for f in files],
        }
    if private:
        info["private"] = 1
    meta: dict[str, Any] = {
        "info": info,
        "creation date": int(time.time()),
        "created by": f"{APP_NAME} {__version__}",
        "encoding": "UTF-8",
    }
    if tracker_url:
        meta["announce"] = tracker_url
        meta["announce-list"] = [[tracker_url]]
    if webseed_url:
        meta["url-list"] = [webseed_url]
    content = bencode(meta)
    return TorrentBuild(
        content=content,
        info=info,
        name=name,
        piece_count=(len(info["pieces"]) // 20),
        piece_length=piece_len,
        total_bytes=total,
        tracker_url=tracker_url,
        webseed_url=webseed_url,
    )


def files_from_entries(entries: Iterable[FileEntry], *, root_name: str = "") -> list[TorrentFile]:
    """Adapt manifest entries; *root_name* is stripped from the torrent paths."""
    return [
        TorrentFile(path=e.path, rel=e.rel, size=e.size, root_name=root_name or e.root_name) for e in entries
    ]


def files_from_items(items: Iterable[ZipItem], *, root_name: str = "") -> list[TorrentFile]:
    return [TorrentFile(path=i.path, rel=i.arcname, size=i.size, root_name=root_name) for i in items]


def build_magnet(
    info_hash_hex: str,
    *,
    name: str = "",
    tracker: str = "",
    webseeds: Sequence[str] = (),
    total_bytes: int = 0,
) -> str:
    parts = [f"magnet:?xt=urn:btih:{info_hash_hex}"]
    if name:
        parts.append(f"dn={quote(name)}")
    if total_bytes:
        parts.append(f"xl={total_bytes}")
    if tracker:
        parts.append(f"tr={quote(tracker, safe='')}")
    for seed in webseeds:
        parts.append(f"ws={quote(seed, safe='')}")
    return "&".join(parts)


def merge_magnet(base: str, **extra: str) -> str:
    params = "&".join(f"{k}={quote(v, safe='')}" for k, v in extra.items() if v)
    return f"{base}&{params}" if params else base


@dataclass
class TorrentSummary:
    name: str
    piece_count: int
    piece_length: int
    total_bytes: int
    info_hash: str
    file_count: int

    def headline(self) -> str:
        return (
            f"{self.file_count} files · {self.piece_count} pieces · "
            f"{self.piece_length // 1024} KiB each · {self.info_hash[:12]}…"
        )


def summarize(build: TorrentBuild, file_count: int) -> TorrentSummary:
    return TorrentSummary(
        name=build.name,
        piece_count=build.piece_count,
        piece_length=build.piece_length,
        total_bytes=build.total_bytes,
        info_hash=build.info_hash_hex,
        file_count=file_count,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Built-in tracker (HTTP, BEP-3 announce)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Peer:
    peer_id: bytes
    ip: str
    port: int
    left: int = 0
    uploaded: int = 0
    downloaded: int = 0
    event: str = ""
    last_seen: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return time.time() - self.last_seen < 180

    def compact(self) -> bytes:
        try:
            packed_ip = socket.inet_aton(self.ip)
        except OSError:
            return b""
        return packed_ip + struct.pack(">H", max(0, min(self.port, 65535)))


class TrackerRegistry:
    """In-memory swarm state for one or more info-hashes."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._swarms: dict[bytes, dict[bytes, Peer]] = {}
        self.announces = 0
        self.completed = 0

    def announce(
        self,
        info_hash: bytes,
        *,
        peer_id: bytes,
        ip: str,
        port: int,
        left: int = 0,
        uploaded: int = 0,
        downloaded: int = 0,
        event: str = "",
        numwant: int = 50,
    ) -> list[Peer]:
        with self._lock:
            swarm = self._swarms.setdefault(info_hash, {})
            for key in [k for k, p in swarm.items() if not p.alive]:
                swarm.pop(key, None)
            swarm[peer_id] = Peer(
                peer_id=peer_id,
                ip=ip,
                port=port,
                left=left,
                uploaded=uploaded,
                downloaded=downloaded,
                event=event,
            )
            self.announces += 1
            if event == "completed":
                self.completed += 1
            others = [p for pid, p in swarm.items() if pid != peer_id and p.port > 0]
            return others[: max(0, min(numwant, 200))]

    def stats(self, info_hash: bytes) -> dict[str, int]:
        with self._lock:
            swarm = self._swarms.get(info_hash, {})
            seeders = sum(1 for p in swarm.values() if p.left == 0 and p.alive)
            leechers = sum(1 for p in swarm.values() if p.left > 0 and p.alive)
            return {"complete": seeders, "incomplete": leechers, "downloaded": self.completed}

    def peers(self, info_hash: bytes) -> list[Peer]:
        with self._lock:
            return [p for p in self._swarms.get(info_hash, {}).values() if p.alive]

    def reset(self) -> None:
        with self._lock:
            self._swarms.clear()
            self.announces = 0
            self.completed = 0


# ─────────────────────────────────────────────────────────────────────────────
# Seed statistics for the dashboard
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SeedStats:
    requests: int = 0
    bytes_sent: int = 0
    started: float = field(default_factory=time.time)

    def add(self, count: int) -> None:
        self.requests += 1
        self.bytes_sent += count

    @property
    def rate(self) -> float:
        elapsed = max(0.5, time.time() - self.started)
        return self.bytes_sent / elapsed


def torrent_hint(build: TorrentBuild) -> str:
    """A short, honest note shown in the CLI."""
    return (
        "Open the .torrent (or magnet) in qBittorrent, Transmission or aria2 — "
        "the webseed pulls pieces straight from this device at LAN speed."
    )


def parse_torrent(data: bytes) -> dict:
    decoded = bdecode(data)
    if not isinstance(decoded, dict) or b"info" not in decoded:
        raise TorrentError("not a valid .torrent file (missing info dictionary)")
    return decoded


def read_torrent(path: Path) -> dict:
    try:
        return parse_torrent(path.read_bytes())
    except OSError as exc:
        raise TorrentError(f"cannot read {path}: {exc}") from exc


def torrent_name(meta: dict) -> str:
    info = meta.get(b"info", meta.get("info", {}))
    raw = info.get(b"name") or info.get("name") or b"torrent"
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


def webseed_path(base: str, name: str, rel: str) -> str:
    """BEP-19 path layout: ``<base><name>/<relative path>``."""
    return f"{base.rstrip('/')}/{quote(name)}/{quote(rel.replace(os.sep, '/'))}"


def default_torrent_dir() -> Path:
    return platforms.downloads_dir()
