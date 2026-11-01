"""The share HTTP server: Range, ETag, parallel-friendly, zero-copy sends.

Features
--------
* ``Accept-Ranges: bytes`` with single and multi-range support (multipart
  ``byteranges``) so ``aria2c -x16``, ``curl -C -`` and browsers can resume.
* Strong ``ETag`` + ``Last-Modified`` and ``If-Range`` validation.
* ``os.sendfile`` zero-copy path on Linux/Android/Termux and ``socket.sendfile``
  on Windows (see :func:`local_file_sharer.platforms.sendfile`).
* Streaming ZIP for folders (resumable, ZIP64) and a 16-connection parallel
  download button in the web UI.
* Optional PIN/password gate, uploads (when enabled), expiry enforcement.
* Cache-busting headers everywhere: browser caching disabled for pages/API.

The handler is intentionally dependency-free (stdlib only) and runs on
``ThreadingHTTPServer`` with ``daemon_threads`` so a client that stalls cannot
block the whole share.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import html
import ipaddress
import json
import math
import mimetypes
import os
import re
import socket
import threading
import time
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable

from .. import APP_NAME, __version__, platforms
from ..fmt import human_size
from .manifest import FileEntry, Manifest, guess_mime, kind_of
from .stats import TransferTracker
from .torrent import TRACKER_INTERVAL, bencode
from .zipstream import ZipItem, ZipStream

CHUNK = 256 * 1024
#: Paths that keep answering after the share expires (liveness + branding).
EXPIRY_EXEMPT = ("/healthz", "/favicon.ico", "/robots.txt", "/static/", "/api/expired")
MULTI_RANGE_LIMIT = 32
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, no-transform",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Accel-Buffering": "no",
}


# ── Request context ─────────────────────────────────────────────────────────
@dataclass
class ShareContext:
    """Everything the handler needs, shared by every request thread."""

    manifest: Manifest
    tracker: TransferTracker
    #: Public origin of the share, e.g. ``http://192.168.1.42:8080`` — never with
    #: a trailing slash, because every caller concatenates ``/path`` onto it and
    #: a stray slash produced ``http://host:port//download/abc`` in the manifest.
    base_url: str = ""
    pin: str = ""
    allow_upload: bool = False
    upload_dir: Path | None = None
    assets: dict[str, str] | None = None  # name -> html string
    extra: dict[str, str] | None = None
    peer_name: str = ""
    started_at: float = 0.0
    stop_callback: Callable[[], None] | None = None
    one_liners: dict[str, str] | None = None
    theme: str = "dark"
    torrent: object | None = None  # TorrentBuild, when torrent mode is on
    tracker_registry: object | None = None  # TrackerRegistry for /announce
    torrent_stats: object | None = None  # SeedStats for the dashboard

    #: Per-share lockout state. Keeping this on the context rather than at
    #: module level means one share's attackers cannot lock the user out of a
    #: different share started later in the same process.
    _attempts: dict[str, int] = field(default_factory=dict, repr=False)
    _locked_until: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = time.time()
        if self.assets is None:
            self.assets = {}

    @property
    def cookie_name(self) -> str:
        return "lfs_auth"

    def auth_ok(self, provided: str) -> bool:
        if not self.pin:
            return True
        return hmac.compare_digest(provided.encode("utf-8", "replace"), self.pin.encode("utf-8"))

    # ── brute-force protection ──────────────────────────────────────────────
    def note_failure(self, client: str) -> None:
        """Count a wrong PIN; only lock out after :data:`FREE_PIN_ATTEMPTS`."""
        with _ATTEMPT_LOCK:
            attempts = self._attempts.get(client, 0) + 1
            self._attempts[client] = attempts
            if attempts < FREE_PIN_ATTEMPTS:
                # A typo must never block the right PIN: the next attempt is free.
                self._locked_until.pop(client, None)
                return
            over = attempts - FREE_PIN_ATTEMPTS + 1
            self._locked_until[client] = time.time() + min(MAX_PIN_LOCKOUT, 2.0 * over)

    def note_success(self, client: str) -> None:
        with _ATTEMPT_LOCK:
            self._attempts.pop(client, None)
            self._locked_until.pop(client, None)

    def locked_for(self, client: str) -> float:
        """Seconds the client must still wait after too many wrong PINs."""
        with _ATTEMPT_LOCK:
            until = self._locked_until.get(client, 0.0)
        return max(0.0, until - time.time())


_EPHEMERAL_TOKENS: dict[str, float] = {}
#: Wrong PINs allowed before the client has to wait. One typo is not an attack,
#: and locking out the fifth person to mistype their own PIN is user-hostile.
FREE_PIN_ATTEMPTS = 5
#: Longest any client is made to wait, in seconds.
MAX_PIN_LOCKOUT = 60.0
_ATTEMPT_LOCK = threading.Lock()


def _unique_upload_path(target_dir: Path, name: str) -> Path:
    """A path that does not exist yet, created exclusively.

    Two uploads of the same name in the same second must not collide: the old
    ``f"{stem}-{int(time.time())}"`` suffix produced the *same* name for both,
    so the second silently overwrote the first while both reported success.
    ``O_EXCL`` makes the choice atomic, so concurrent uploads cannot race.
    """
    stem, suffix = Path(name).stem, Path(name).suffix
    for counter in range(1000):
        candidate = target_dir / (name if counter == 0 else f"{stem}-{counter}{suffix}")
        try:
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        os.close(descriptor)
        return candidate
    raise OSError(f"no free filename for {name!r} in {target_dir}")


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _etag_for(stat: os.stat_result, path: Path) -> str:
    """A strong validator that changes whenever the bytes might have changed.

    ``int(mtime)`` truncates to whole seconds, so rewriting a file twice inside
    one second produced the same ETag — and a client that resumed with
    ``If-Range`` then got a 206 and spliced new bytes onto old ones. Include
    ``st_mtime_ns`` and the inode as well, and keep the path so a different
    file with identical contents does not share a validator.
    """
    inode = getattr(stat, "st_ino", 0)
    return (
        f'"{stat.st_mtime_ns}-{stat.st_size}-{inode:x}-'
        f'{hashlib.sha1(str(path).encode("utf-8", "replace")).hexdigest()[:8]}"'
    )


def issue_token(pin: str, *, ttl: float = 12 * 3600) -> str:
    """A signed-ish opaque token so the PIN prompt only appears once."""
    raw = f"{pin}:{int(time.time() // ttl)}".encode()
    token = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()[:16]).decode("ascii").rstrip("=")
    _EPHEMERAL_TOKENS[token] = time.time() + ttl
    return token


def token_valid(pin: str, token: str) -> bool:
    if not pin:
        return True
    if not token:
        return False
    expected_now = issue_token(pin)
    # Accept tokens issued in the previous window so long downloads keep working.
    for offset in (-1, 0, 1):
        raw = f"{pin}:{int(time.time() // (12 * 3600)) + offset}".encode()
        candidate = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()[:16]).decode("ascii").rstrip("=")
        if hmac.compare_digest(candidate, token):
            return True
    return hmac.compare_digest(token, expected_now)


# ── Handler ────────────────────────────────────────────────────────────────
class ShareRequestHandler(BaseHTTPRequestHandler):
    """BaseHTTPRequestHandler wired to a :class:`ShareContext`."""

    server_version = f"{APP_NAME}/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    context: ShareContext  # injected by the server subclass

    # ── infrastructure ─────────────────────────────────────────────────────
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        # Keep the terminal clean: the dashboard is the log.
        return

    def handle_one_request(self) -> None:  # pragma: no cover - network path
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, socket.timeout):
            self.close_connection = True

    @property
    def ctx(self) -> ShareContext:
        return self.context

    def client_ip(self) -> str:
        """The address used for the PIN lockout, so it must not be client-chosen.

        ``X-Forwarded-For`` is only meaningful when a reverse proxy sets it. A
        remote client sending it directly could hand us a fresh address per
        request and guess the PIN forever, so the header is honoured only when
        the connection itself comes from loopback (i.e. a proxy on this machine).
        """
        peer = self.client_address[0] if self.client_address else "?"
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded and _is_loopback(peer):
            return forwarded.split(",")[0].strip()
        return peer

    def user_agent(self) -> str:
        return self.headers.get("User-Agent", "")

    def is_api(self) -> bool:
        return self.path.startswith("/api/")

    def wants_json(self) -> bool:
        accept = self.headers.get("Accept", "")
        return "/api/" in self.path or "application/json" in accept

    def send_common_headers(self, *, content_type: str, length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        for key, value in NO_CACHE_HEADERS.items():
            self.send_header(key, value)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Access-Control-Allow-Origin", "*")
        if length is not None:
            self.send_header("Content-Length", str(length))

    def send_text(
        self, status: int, body: str | bytes, *, content_type: str = "text/plain; charset=utf-8"
    ) -> None:
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_common_headers(content_type=content_type, length=len(payload))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def send_json(self, payload: dict | list, *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_common_headers(content_type="application/json; charset=utf-8", length=len(body))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error_page(self, status: int, message: str, *, hint: str = "") -> None:
        if self.wants_json():
            self.send_json({"error": message, "hint": hint, "status": status}, status=status)
            return
        template = (self.ctx.assets or {}).get("error.html", ERROR_FALLBACK)
        body = template.replace("{{status}}", str(status)).replace("{{message}}", html.escape(message))
        body = body.replace("{{hint}}", html.escape(hint)).replace("{{brand}}", APP_NAME)
        self.send_text(status, body, content_type="text/html; charset=utf-8")

    def send_html(self, body: str, *, status: int = 200, retry_after: float | None = None) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_common_headers(content_type="text/html; charset=utf-8", length=len(payload))
        if retry_after is not None:
            # RFC 6585: a 429 should say how long to wait. Browsers and scripts
            # both use this, and without it the page is the only clue.
            self.send_header("Retry-After", str(max(1, math.ceil(retry_after))))
        pending = getattr(self, "_pending_cookie", None)
        if pending:
            self.send_header(*pending)
            self._pending_cookie = None
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def redirect(self, location: str, *, status: int = HTTPStatus.FOUND) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        for key, value in NO_CACHE_HEADERS.items():
            self.send_header(key, value)
        # A redirect after a successful login must carry the cookie, or the
        # browser lands on the page it was going to and is challenged again.
        pending = getattr(self, "_pending_cookie", None)
        if pending:
            self.send_header(*pending)
            self._pending_cookie = None
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── routing ────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._dispatch(safe=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(safe=True)

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch(safe=False)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type, X-LFS-PIN")
        for key, value in NO_CACHE_HEADERS.items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, *, safe: bool) -> None:
        try:
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            query = urllib.parse.parse_qs(parsed.query)
            if self.ctx.manifest.expired and not path.startswith(EXPIRY_EXEMPT):
                self.send_error_page(
                    HTTPStatus.GONE,
                    "This share link has expired",
                    hint="Ask the sender for a fresh link — start again with `lfs`.",
                )
                return
            if not self._check_auth(path, query):
                return
            if path in ("/", "/index.html"):
                self.page_index()
            elif path == "/healthz":
                self.send_json({"ok": True, "app": APP_NAME, "version": __version__})
            elif path.startswith("/d/"):
                self.page_browser(path[3:].strip("/"), query)
            elif path.startswith("/s/"):
                self.page_stream(path[3:].strip("/"), query)
            elif path.startswith("/f/"):
                self.serve_file(path[3:].strip("/"))
            elif path == "/download/all.zip":
                self.serve_zip(all_files=True)
            elif path.startswith("/download/"):
                self.handle_download(path[len("/download/") :].strip("/"))
            elif path.startswith("/zip/"):
                self.serve_zip(item_id=path[len("/zip/") :].strip("/"))
            elif path.startswith("/folder/"):
                self.serve_folder_zip(path[len("/folder/") :].strip("/"))
            elif path.startswith("/raw/"):
                self.serve_rel(path[len("/raw/") :])
            elif path == "/api/stats":
                self.send_json(self.ctx.tracker.snapshot())
            elif path == "/api/manifest":
                self.send_json(self.ctx.manifest.as_dict(self.ctx.base_url))
            elif path == "/api/links":
                self.send_json(
                    {
                        "base_url": self.ctx.base_url,
                        "one_liners": self.ctx.one_liners or {},
                        "files": [
                            {
                                "name": f.rel.split("/")[-1],
                                "url": f"{self.ctx.base_url}/f/{f.file_id}",
                                "size": f.size,
                            }
                            for f in self.ctx.manifest.iter_files()
                        ],
                    }
                )
            elif path == "/api/expiry":
                self.send_json({"expires_at": self.ctx.manifest.expires_at, "ttl": self.ctx.manifest.ttl})
            elif path == "/api/expired":
                self.send_json({"expired": self.ctx.manifest.expired, "ttl": self.ctx.manifest.ttl})
            elif path == "/api/upload" and self.command == "POST":
                self.handle_upload(query)
            elif path.startswith("/static/"):
                self.serve_static(path[len("/static/") :])
            elif path == "/api/qr":
                self.serve_qr(query)
            elif path == "/announce":
                self.handle_announce(query)
            elif path == "/scrape":
                self.handle_scrape(query)
            elif path.startswith("/webseed/") or path.startswith("/torrents/"):
                self.handle_webseed(path, query)
            elif path == "/torrent":
                self.handle_torrent_file()
            elif path == "/favicon.ico":
                self.serve_favicon()
            elif path == "/robots.txt":
                self.send_text(200, "User-agent: *\nDisallow: /\n")
            elif path == "/api/stop" and self.command == "POST":
                self.send_json({"ok": True})
                if self.ctx.stop_callback:
                    self.ctx.stop_callback()
            else:
                self.send_error_page(
                    HTTPStatus.NOT_FOUND,
                    f"No such page: {path}",
                    hint="Open the main page and pick a file from the list.",
                )
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - a share must never crash
            self.ctx.tracker.note_error()
            try:
                self.send_error_page(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "Something went wrong while serving this request",
                    hint=f"{type(exc).__name__}: {exc}",
                )
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.close_connection = True

    # ── auth ───────────────────────────────────────────────────────────────
    def _check_auth(self, path: str, query: dict[str, list[str]]) -> bool:
        if not self.ctx.pin:
            return True
        if path in ("/healthz", "/favicon.ico", "/robots.txt"):
            return True
        token = self._cookie_token()
        if token_valid(self.ctx.pin, token):
            return True
        client = self.client_ip()
        wait = self.ctx.locked_for(client)
        if wait > 0:
            # Before this check the page just said "Wrong PIN", so a correct PIN
            # looked broken for as long as the lockout lasted.
            self.send_html(
                self._login_page(wrong=bool(self._submitted_pin(query)), wait=wait),
                status=HTTPStatus.TOO_MANY_REQUESTS,
                retry_after=wait,
            )
            return False
        header_pin = self.headers.get("X-LFS-PIN", "")
        if header_pin and self.ctx.auth_ok(header_pin):
            self.ctx.note_success(client)
            # Scripted clients (curl -H, aria2c) authenticate every request and
            # have no cookie jar; hand them one anyway so a follow-up GET works.
            self._pending_cookie = self._auth_cookie_header()
            return True
        provided = (query.get("pin") or [""])[0]
        if provided and self.ctx.auth_ok(provided):
            self.ctx.note_success(client)
            self._pending_cookie = self._auth_cookie_header()
            if self.command == "GET" and path not in ("/",):
                rest = "&".join(f"{k}={v[0]}" for k, v in query.items() if k != "pin")
                self.redirect(f"{path}?{rest}" if rest else path)
                return False
            return True
        if self.command == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            fields = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
            candidate = (fields.get("pin") or [""])[0]
            if candidate and self.ctx.auth_ok(candidate):
                self.ctx.note_success(client)
                self._pending_cookie = self._auth_cookie_header()
                self.redirect("/")
                return False
        if provided or header_pin or self.command == "POST":
            self.ctx.note_failure(client)
        self.send_html(self._login_page(wrong=bool(provided or header_pin)), status=HTTPStatus.UNAUTHORIZED)
        return False

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:  # noqa: BLE001 - malformed cookies are not fatal
            return ""
        morsel = cookie.get(self.ctx.cookie_name)
        return morsel.value if morsel else ""

    def _auth_cookie_header(self) -> tuple[str, str]:
        """``("Set-Cookie", …)`` for the current PIN, sent with the page."""
        token = issue_token(self.ctx.pin)
        return "Set-Cookie", f"{self.ctx.cookie_name}={token}; Path=/; HttpOnly; SameSite=Lax"

    def _submitted_pin(self, query: dict[str, list[str]]) -> str:
        """The PIN from the query string or a POST body, without consuming POSTs."""
        provided = (query.get("pin") or [""])[0]
        if provided or self.command != "POST":
            return provided
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        fields = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
        return (fields.get("pin") or [""])[0]

    def _login_page(self, *, wrong: bool = False, wait: float = 0.0) -> str:
        template = (self.ctx.assets or {}).get("login.html", LOGIN_FALLBACK)
        if wait > 0:
            message = f"Too many attempts — try again in {math.ceil(wait)}s."
        elif wrong:
            message = "Wrong PIN — try again."
        else:
            message = ""
        return (
            template.replace("{{brand}}", APP_NAME)
            .replace("{{error}}", message)
            .replace("{{hidden}}", "is-visible" if message else "")
        )

    # ── pages ──────────────────────────────────────────────────────────────
    def page_index(self) -> None:
        """Route the landing page according to the share mode."""
        mode = self.ctx.manifest.mode
        if mode == "serve":
            self.page_server_index()
            return
        if mode == "stream":
            media = [f for f in self.ctx.manifest.iter_files() if f.kind in ("video", "audio")]
            if media:
                self.redirect(f"/s/{media[0].file_id}")
                return
        tracker = self.ctx.tracker
        tracker.register_request(self.client_ip(), self.user_agent(), path="/")
        template = (self.ctx.assets or {}).get("downloader.html") or (self.ctx.assets or {}).get("index.html")
        if not template:
            self.send_error_page(HTTPStatus.INTERNAL_SERVER_ERROR, "UI assets are missing")
            return
        # Folder-first: a share of ~/Photos should show "Photos", not 400 JPEGs.
        # Cards are grouped by the top-level name under the shared root, so each
        # folder gets one entry with its own download button (a folder zip).
        cards = _entry_cards(self.ctx.manifest)
        torrent = self.ctx.torrent
        torrent_links = ""
        if torrent is not None:
            torrent_links = (
                f'<a class="btn ghost" href="/torrent" download>🌱 .torrent</a>'
                f'<button class="btn ghost" data-copy="{html.escape(torrent.magnet)}">🧲 magnet link</button>'  # type: ignore[attr-defined]
            )
        body = (
            template.replace("{{title}}", html.escape(self.ctx.manifest.title))
            .replace("{{files}}", "\n".join(cards))
            .replace("{{torrent_links}}", torrent_links)
            .replace("{{file_count}}", str(self.ctx.manifest.file_count))
            .replace("{{total_size}}", human_size(self.ctx.manifest.total_bytes))
            .replace("{{mode}}", self.ctx.manifest.mode)
            .replace("{{expiry}}", self.ctx.manifest.expiry_text())
            .replace("{{base_url}}", html.escape(self.ctx.base_url))
            .replace("{{zip_url}}", "/download/all.zip")
            .replace("{{parallel}}", "16")
            .replace("{{brand}}", APP_NAME)
            .replace("{{version}}", __version__)
            .replace("{{peer}}", html.escape(self.ctx.peer_name or "this device"))
            .replace("{{can_upload}}", "true" if self.ctx.allow_upload else "false")
            .replace("{{device_count}}", str(len(tracker.active_devices())))
            .replace("{{upload_section}}", self._upload_section())
        )
        self.send_html(body)

    def page_server_index(self) -> None:
        """Serve mode: one browser page listing every shared item."""
        self.ctx.tracker.register_request(self.client_ip(), self.user_agent(), path="/")
        template = (self.ctx.assets or {}).get("server.html")
        if not template:
            self.send_error_page(HTTPStatus.INTERNAL_SERVER_ERROR, "UI assets are missing")
            return
        folders = [item for item in self.ctx.manifest.items if item.is_dir]
        if len(self.ctx.manifest.items) == 1 and folders:
            self.page_browser(folders[0].item_id, {})
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        search = (query.get("q") or [""])[0]
        entries = self.ctx.manifest.iter_files()
        if search:
            lowered = search.lower()
            entries = [f for f in entries if lowered in f.rel.lower()]
        rows = "".join(self._browser_row(f, base="/") for f in entries)
        for item in folders:
            rows = (
                f'<a class="row" href="/d/{item.item_id}"><span class="row-icon">📁</span>'
                f'<span class="row-name">{html.escape(item.name)}/</span>'
                f'<span class="row-size">{item.size_text}</span>'
                f'<span class="row-get">→</span></a>'
            ) + rows
        body = (
            template.replace("{{title}}", html.escape(self.ctx.manifest.title))
            .replace("{{breadcrumbs}}", '<a href="/">🏠 all files</a>')
            .replace("{{rows}}", rows)
            .replace("{{file_count}}", str(self.ctx.manifest.file_count))
            .replace("{{folder_size}}", human_size(self.ctx.manifest.total_bytes))
            .replace("{{zip_url}}", "/download/all.zip")
            .replace("{{total_size}}", human_size(self.ctx.manifest.total_bytes))
            .replace("{{manifest_url}}", "/api/manifest")
            .replace("{{base_url}}", html.escape(self.ctx.base_url))
            .replace("{{brand}}", APP_NAME)
            .replace("{{version}}", __version__)
            .replace("{{can_upload}}", "true" if self.ctx.allow_upload else "false")
            .replace("{{search}}", html.escape(search))
            .replace("{{mode}}", self.ctx.manifest.mode)
            .replace("{{empty_class}}", "" if rows else "hidden")
            .replace("{{upload_section}}", self._upload_section())
        )
        self.send_html(body)

    def page_browser(self, item_id: str, query: dict[str, list[str]]) -> None:
        item = self.ctx.manifest.get_item(item_id)
        if item is None or not item.is_dir:
            self.send_error_page(HTTPStatus.NOT_FOUND, "That folder is not part of this share")
            return
        sub = (query.get("p") or [""])[0].strip("/")
        template = (self.ctx.assets or {}).get("server.html") or (self.ctx.assets or {}).get("index.html")
        if not template:
            self.send_error_page(HTTPStatus.INTERNAL_SERVER_ERROR, "UI assets are missing")
            return
        patterns = query.get("q") or []
        search = patterns[0] if patterns else ""
        entries = [f for f in item.files if not sub or f.rel.startswith(f"{item.name}/{sub}")]
        if search:
            lowered = search.lower()
            entries = [f for f in entries if lowered in f.rel.lower()]
        rows = "\n".join(self._browser_row(f, base=f"/d/{item_id}") for f in entries)
        breadcrumbs = self._breadcrumbs(item, sub)
        body = (
            template.replace("{{title}}", html.escape(item.name))
            .replace("{{breadcrumbs}}", breadcrumbs)
            .replace("{{rows}}", rows)
            .replace("{{file_count}}", str(len(item.files)))
            .replace("{{folder_size}}", item.size_text)
            .replace("{{zip_url}}", f"/zip/{item_id}")
            .replace("{{total_size}}", human_size(self.ctx.manifest.total_bytes))
            .replace("{{manifest_url}}", "/api/manifest")
            .replace("{{base_url}}", html.escape(self.ctx.base_url))
            .replace("{{brand}}", APP_NAME)
            .replace("{{version}}", __version__)
            .replace("{{can_upload}}", "true" if self.ctx.allow_upload else "false")
            .replace("{{search}}", html.escape(search))
            .replace("{{mode}}", self.ctx.manifest.mode)
            .replace("{{empty_class}}", "" if entries else "hidden")
            .replace("{{upload_section}}", self._upload_section())
        )
        self.send_html(body)

    def _breadcrumbs(self, item, sub: str) -> str:  # noqa: ANN001
        parts = [item.name, *[p for p in sub.split("/") if p]]
        out = [f'<a href="/d/{item.item_id}">🏠 {html.escape(item.name)}</a>']
        trail = ""
        for part in parts[1:]:
            trail = f"{trail}/{part}" if trail else part
            out.append(
                f'<span class="sep">/</span><a href="/d/{item.item_id}?p={urllib.parse.quote(trail)}">'
                f"{html.escape(part)}</a>"
            )
        return "".join(out)

    def _browser_row(self, entry: FileEntry, *, base: str) -> str:
        kind = entry.kind
        size = entry.size_text
        icon = _icon_for(entry)
        return (
            f'<a class="row" href="/f/{entry.file_id}" data-kind="{kind}">'
            f'<span class="row-icon">{icon}</span>'
            f'<span class="row-name" title="{html.escape(entry.rel)}">{html.escape(entry.rel.split("/")[-1])}</span>'
            f'<span class="row-size">{size}</span>'
            f'<span class="row-get">↓</span>'
            f"</a>"
        )

    def page_stream(self, file_id: str, query: dict[str, list[str]]) -> None:
        entry = self.ctx.manifest.get_file(file_id)
        if entry is None:
            self.send_error_page(HTTPStatus.NOT_FOUND, "That file is not part of this share")
            return
        template = (self.ctx.assets or {}).get("player.html")
        if template is None:
            self.redirect(f"/f/{file_id}")
            return
        siblings = [f for f in self.ctx.manifest.iter_files() if f.kind in ("video", "audio")]
        playlist = "\n".join(
            f'<li data-id="{f.file_id}" class="{"active" if f.file_id == entry.file_id else ""}">'
            f'<a href="/s/{f.file_id}">{html.escape(f.rel.split("/")[-1])}</a>'
            f"<span>{f.size_text}</span></li>"
            for f in siblings
        )
        body = (
            template.replace("{{file_name}}", html.escape(entry.rel.split("/")[-1]))
            .replace("{{file_id}}", entry.file_id)
            .replace("{{mime}}", entry.mime)
            .replace("{{size}}", entry.size_text)
            .replace("{{playlist}}", playlist)
            .replace("{{poster}}", "")
            .replace("{{brand}}", APP_NAME)
            .replace("{{version}}", __version__)
            .replace("{{download_url}}", f"/f/{entry.file_id}")
            .replace("{{base_url}}", html.escape(self.ctx.base_url))
            .replace("{{playlist_count}}", str(len(siblings)))
        )
        self.send_html(body)

    def serve_favicon(self) -> None:
        svg_icon = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect width="64" height="64" rx="14" fill="#0b1220"/>'
            '<path d="M20 34h24M32 20v28" stroke="#ffffff" stroke-width="6" stroke-linecap="round"/>'
            "</svg>"
        )
        payload = svg_icon.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    # ── torrent support ────────────────────────────────────────────────────
    def handle_announce(self, query: dict[str, list[str]]) -> None:
        """Minimal BEP-3 HTTP tracker: ``/announce?info_hash=…&peer_id=…``."""
        registry = self.ctx.tracker_registry
        if registry is None and self.ctx.torrent is not None:
            # A torrent without a tracker is a dead magnet: create the registry
            # rather than answer "tracker disabled" to a real client.
            from local_file_sharer.transfer.torrent import TrackerRegistry  # noqa: PLC0415

            registry = self.ctx.tracker_registry = TrackerRegistry()
        if registry is None:
            self.send_text(HTTPStatus.NOT_FOUND, "tracker disabled")
            return
        raw = urllib.parse.urlsplit(self.path).query
        params = urllib.parse.parse_qs(raw, encoding="latin-1", keep_blank_values=True)
        info_hash = _binary_param(params, "info_hash")
        peer_id = _binary_param(params, "peer_id") or b"-LFS0000-000000000000"
        if not info_hash:
            self.send_text(HTTPStatus.BAD_REQUEST, bencode({"failure reason": "missing info_hash"}))
            return
        port = _int_param(params, "port", 6881)
        left = _int_param(params, "left", 0)
        event = (_text_param(params, "event") or "").lower()
        numwant = _int_param(params, "numwant", 50)
        peer_ip = self.client_ip()
        peers = registry.announce(
            info_hash,
            peer_id=peer_id,
            ip=peer_ip,
            port=port,
            left=left,
            uploaded=_int_param(params, "uploaded", 0),
            downloaded=_int_param(params, "downloaded", 0),
            event=event,
            numwant=numwant,
        )
        compact = (_text_param(params, "compact") or "1") != "0"
        payload: dict = {
            "interval": TRACKER_INTERVAL,
            "min interval": 15,
            "tracker id": self.ctx.manifest.token,
        }
        if compact:
            payload["peers"] = b"".join(p.compact() for p in peers)
        else:
            payload["peers"] = [{"peer id": p.peer_id, "ip": p.ip, "port": p.port} for p in peers]
        payload.update(registry.stats(info_hash))
        self.send_text(HTTPStatus.OK, bencode(payload), content_type="text/plain")
        return

    def handle_scrape(self, query: dict[str, list[str]]) -> None:
        registry = self.ctx.tracker_registry
        torrent = self.ctx.torrent
        if registry is None or torrent is None:
            self.send_text(HTTPStatus.NOT_FOUND, bencode({"failure reason": "tracker disabled"}))
            return
        stats = registry.stats(torrent.info_hash)  # type: ignore[attr-defined]
        payload = {"files": {torrent.info_hash: stats}}  # type: ignore[attr-defined]
        self.send_text(HTTPStatus.OK, bencode(payload))

    def handle_torrent_file(self) -> None:
        torrent = self.ctx.torrent
        if torrent is None:
            self.send_error_page(HTTPStatus.NOT_FOUND, "torrent mode is not enabled for this share")
            return
        payload = torrent.content  # type: ignore[attr-defined]
        name = f"{torrent.name}.torrent"  # type: ignore[attr-defined]
        self.send_response(HTTPStatus.OK)
        self.send_common_headers(content_type="application/x-bittorrent", length=len(payload))
        self.send_header("Content-Disposition", _content_disposition(name))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def handle_webseed(self, path: str, query: dict[str, list[str]]) -> None:
        """BEP-19 webseed: ``/webseed/<torrent name>/<relative path>``."""
        torrent = self.ctx.torrent
        if torrent is None:
            self.send_error_page(HTTPStatus.NOT_FOUND, "torrent mode is not enabled")
            return
        rel = urllib.parse.unquote(path[len("/webseed/") :])
        name = torrent.name  # type: ignore[attr-defined]
        if rel.startswith(f"{name}/"):
            rel = rel[len(name) + 1 :]
        # Strip an optional torrents/<name> prefix used by some clients.
        rel = rel.split("?", 1)[0]
        entry = self.ctx.manifest.get_file_by_rel(rel)
        if entry is None:
            # Fall back to matching by suffix (client may re-root the path).
            candidates = [f for f in self.ctx.manifest.iter_files() if f.rel.endswith(rel)]
            entry = candidates[0] if candidates else None
        if entry is None:
            self.send_error_page(HTTPStatus.NOT_FOUND, f"webseed: no such file {rel}")
            return
        self._send_path(entry.path, rel=entry.rel, download_name=entry.name)

    def serve_static(self, rel: str) -> None:
        """Serve bundled CSS/JS assets (no caching, so updates land instantly)."""
        safe_rel = rel.replace("\\", "/").strip("/")
        if not safe_rel or ".." in safe_rel.split("/"):
            self.send_error_page(HTTPStatus.NOT_FOUND, "Asset not found")
            return
        base = Path(__file__).resolve().parent.parent / "web" / "assets" / "static"
        target = base / safe_rel
        try:
            payload = target.read_bytes()
        except OSError:
            self.send_error_page(HTTPStatus.NOT_FOUND, "Asset not found")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_common_headers(content_type=content_type, length=len(payload))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def serve_qr(self, query: dict[str, list[str]]) -> None:
        """``/api/qr?data=…`` returns a scannable SVG, rendered server-side."""
        from .. import qr as qr_mod  # noqa: PLC0415 - keep import cost off startup

        data = (query.get("data") or [self.ctx.base_url or "/"])[0][:1024]
        payload = qr_mod.svg(data).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_common_headers(content_type="image/svg+xml; charset=utf-8", length=len(payload))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _upload_section(self) -> str:
        if not self.ctx.allow_upload:
            return ""
        return (
            '<div class="dropzone" id="dropzone">'
            "📤 <b>Drop files here</b> to send them back to the host machine "
            '(or <label style="text-decoration:underline;cursor:pointer">choose files'
            '<input id="file-input" type="file" multiple hidden></label>)</div>'
            "<script>(()=>{const z=document.getElementById('dropzone'),i=document.getElementById('file-input');"
            "const send=async(fs)=>{for(const f of fs){const r=await fetch('/api/upload?name='+"
            "encodeURIComponent(f.name),{method:'POST',body:f,headers:{'Content-Type':'application/octet-stream'}});"
            "const j=await r.json().catch(()=>({}));z.textContent=(r.ok?'✅ sent '+f.name:'⚠️ '+(j.error||'failed'));}}};"
            "z.addEventListener('dragover',e=>{e.preventDefault();z.classList.add('drag')});"
            "z.addEventListener('dragleave',()=>z.classList.remove('drag'));"
            "z.addEventListener('drop',e=>{e.preventDefault();z.classList.remove('drag');send(e.dataTransfer.files)});"
            "i.addEventListener('change',e=>send(e.target.files));})();</script>"
        )

    # ── file serving ───────────────────────────────────────────────────────
    def serve_file(self, file_id: str) -> None:
        entry = self.ctx.manifest.get_file(file_id)
        if entry is None:
            self.send_error_page(HTTPStatus.NOT_FOUND, "File not found in this share")
            return
        self._send_path(entry.path, rel=entry.rel, download_name=entry.rel.split("/")[-1])

    def serve_rel(self, rel: str) -> None:
        entry = self.ctx.manifest.get_file_by_rel(urllib.parse.unquote(rel))
        if entry is None:
            self.send_error_page(HTTPStatus.NOT_FOUND, "File not found in this share")
            return
        self._send_path(entry.path, rel=entry.rel, download_name=entry.rel.split("/")[-1])

    def _send_path(self, path: Path, *, rel: str, download_name: str) -> None:
        try:
            stat = path.stat()
        except OSError:
            self.send_error_page(HTTPStatus.NOT_FOUND, "File is no longer on disk")
            return
        size = stat.st_size
        mtime = stat.st_mtime
        etag = _etag_for(stat, path)
        content_type = guess_mime(path)
        tracker = self.ctx.tracker
        device = tracker.register_request(self.client_ip(), self.user_agent(), path=download_name)
        tracker.start_file(rel, rel.split("/")[-1], size)

        if self._if_none_match(etag):
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_common_headers(content_type=content_type)
            self.end_headers()
            return

        range_header = self.headers.get("Range", "")
        if_range = self.headers.get("If-Range", "")
        if size == 0:
            self.send_response(HTTPStatus.OK)
            self.send_common_headers(content_type=content_type, length=0)
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return
        try:
            ranges = parse_range(range_header, size) if range_header else []
        except ValueError:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if if_range and not self._if_range_matches(if_range, etag, mtime):
            ranges = []  # RFC 9110: a failed If-Range means "send the whole thing"

        if len(ranges) > 1:
            self._send_multipart(path, ranges, size, content_type, etag, mtime, device, rel)
            return
        start, end = ranges[0] if ranges else (0, size - 1)
        partial = bool(ranges)
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_common_headers(content_type=content_type, length=length)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", self.date_time_string(mtime))
        self.send_header(
            "Content-Disposition", _content_disposition(download_name, inline=self._inline(content_type))
        )
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        self._stream_region(path, start, length, device=device, file_key=rel)

    def _stream_region(self, path: Path, start: int, length: int, *, device, file_key: str) -> None:  # noqa: ANN001
        tracker = self.ctx.tracker
        try:
            with open(path, "rb") as handle:
                if length <= 0:
                    return
                delta_box = {"bytes": 0, "last": time.time()}

                def progress(count: int) -> None:
                    delta_box["bytes"] += count
                    now = time.time()
                    if now - delta_box["last"] >= 0.15:
                        tracker.add_bytes(delta_box["bytes"], device=device, file_id=file_key)
                        delta_box["bytes"] = 0
                        delta_box["last"] = now

                try:
                    sent = platforms.sendfile(self.wfile, handle, start, length, progress=progress)
                finally:
                    if delta_box["bytes"]:
                        tracker.add_bytes(delta_box["bytes"], device=device, file_id=file_key)
                if sent < length:
                    # Client went away mid-transfer: nothing else to do.
                    tracker.note_error()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except OSError:
            tracker.note_error()
            self.close_connection = True
        finally:
            tracker.finish_file(file_key)

    def _send_multipart(
        self,
        path: Path,
        ranges: Sequence[tuple[int, int]],
        size: int,
        content_type: str,
        etag: str,
        mtime: float,
        device,
        rel: str,
    ) -> None:  # noqa: ANN001
        boundary = hashlib.sha1(f"{etag}{time.time()}".encode()).hexdigest()[:24]
        parts: list[bytes] = []
        for start, end in ranges:
            parts.append(
                f"--{boundary}\r\nContent-Type: {content_type}\r\n"
                f"Content-Range: bytes {start}-{end}/{size}\r\n\r\n".encode()
            )
        # Every part is followed by one CRLF, and the closing delimiter adds
        # four more bytes plus the boundary. Forgetting the per-part CRLFs made
        # Content-Length short by 2 * len(ranges), so a client stopped reading
        # mid-boundary and dropped the tail of the last range (RFC 2046 §5.1.1).
        closing = f"--{boundary}--\r\n".encode()
        total = (
            sum(len(p) for p in parts) + sum(e - s + 1 for s, e in ranges) + 2 * len(ranges) + len(closing)
        )
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_common_headers(content_type=f"multipart/byteranges; boundary={boundary}", length=total)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.end_headers()
        if self.command == "HEAD":
            return
        for index, (start, end) in enumerate(ranges):
            self.wfile.write(parts[index])
            self._stream_region(path, start, end - start + 1, device=device, file_key=rel)
            self.wfile.write(b"\r\n")
        self.wfile.write(closing)

    def _inline(self, content_type: str) -> bool:
        return content_type.startswith(("video/", "audio/", "image/", "text/plain", "application/pdf"))

    def _if_none_match(self, etag: str) -> bool:
        header = self.headers.get("If-None-Match", "")
        return bool(header) and (header.strip() == etag or header.strip() == "*")

    def _if_range_matches(self, header: str, etag: str, mtime: float) -> bool:
        """RFC 9110 §13.1.5: the range applies only if the validator still matches."""
        value = header.strip()
        if not value:
            return False
        if value.startswith('"') or value.startswith("W/"):
            return value == etag
        matched = self._if_range_date(value, mtime)
        # A date we cannot parse must not silently enable a resume.
        return bool(matched)

    def _if_range_date(self, header: str, mtime: float) -> bool | None:
        if header.startswith('"') or header.startswith("W/"):
            return None
        try:
            from email.utils import parsedate_to_datetime  # noqa: PLC0415

            parsed = parsedate_to_datetime(header)
            return parsed.timestamp() >= int(mtime)
        except (TypeError, ValueError, OverflowError):
            return None

    # ── downloads / zip ────────────────────────────────────────────────────
    def handle_download(self, file_id: str) -> None:
        entry = self.ctx.manifest.get_file(file_id)
        if entry is None:
            self.send_error_page(HTTPStatus.NOT_FOUND, "File not found in this share")
            return
        tracker = self.ctx.tracker
        tracker.register_request(self.client_ip(), self.user_agent(), path=entry.name)
        if track := getattr(self.ctx, "track_download", None):
            track(entry.rel)
        self.redirect(f"/f/{file_id}")

    def serve_folder_zip(self, rel: str) -> None:
        """Download one folder inside a share as its own zip.

        A share of ``~/Pictures`` is a single manifest item, so ``/zip/<id>``
        returns the *whole* share. Clicking "Photos" on the page must not
        download the 400 other files, so the folder is addressed by its path
        relative to the shared root and zipped on its own.
        """
        wanted = urllib.parse.unquote(rel).strip("/")
        if not wanted:
            self.send_error_page(HTTPStatus.NOT_FOUND, "No folder named")
            return
        prefix = f"{wanted}/"
        members = []
        for f in self.ctx.manifest.iter_files():
            inner = f.rel.split("/", 1)[-1] if "/" in f.rel else f.rel
            if inner.startswith(prefix):
                members.append((f, inner))
        if not members:
            self.send_error_page(HTTPStatus.NOT_FOUND, "That folder is not empty or not in this share")
            return
        # strip the folder's own name so the zip opens straight onto its contents
        items = [
            ZipItem(arcname=inner[len(prefix) :] or f.name, path=f.path, size=f.size, mtime=f.mtime)
            for f, inner in members
        ]
        name = f"{wanted.split('/')[-1]}.zip"
        self._send_zip_stream(ZipStream(items, compression=1), name)

    def serve_zip(self, *, all_files: bool = False, item_id: str | None = None) -> None:
        if all_files:
            items = [
                ZipItem(arcname=f.rel, path=f.path, size=f.size, mtime=f.mtime)
                for f in self.ctx.manifest.iter_files()
            ]
            name = "share.zip"
        else:
            item = self.ctx.manifest.get_item(item_id or "")
            if item is None:
                self.send_error_page(HTTPStatus.NOT_FOUND, "Folder not found in this share")
                return
            items = [ZipItem(arcname=f.rel, path=f.path, size=f.size, mtime=f.mtime) for f in item.files]
            name = f"{item.name}.zip"
        if not items:
            self.send_error_page(HTTPStatus.NOT_FOUND, "Nothing to zip here")
            return
        self._send_zip_stream(ZipStream(items, compression=1), name)

    def _send_zip_stream(self, stream: ZipStream, name: str) -> None:
        size = stream.total_size
        device = self.ctx.tracker.register_request(self.client_ip(), self.user_agent(), path=name)
        self.ctx.tracker.start_file(f"zip:{name}", name, size)
        etag = f'"zip-{len(stream.items)}-{size}"'
        range_header = self.headers.get("Range")
        try:
            ranges = parse_range(range_header, size) if range_header else []
        except ValueError:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = ranges[0] if ranges else (0, size - 1)
        partial = bool(ranges)
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_common_headers(content_type="application/zip", length=length)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("Content-Disposition", _content_disposition(name))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            total = 0
            for chunk in stream.iter_stream(start, end + 1):
                if not chunk:
                    continue
                self.wfile.write(chunk)
                total += len(chunk)
                self.ctx.tracker.add_bytes(len(chunk), device=device, file_id=f"zip:{name}")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        finally:
            self.ctx.tracker.finish_file(f"zip:{name}")

    # ── uploads ────────────────────────────────────────────────────────────
    def handle_upload(self, query: dict[str, list[str]]) -> None:
        if not self.ctx.allow_upload:
            self.send_json({"error": "uploads are disabled for this share"}, status=403)
            return
        name = (query.get("name") or [""])[0]
        if not name:
            self.send_json({"error": "missing ?name="}, status=400)
            return
        safe = platforms.safe_filename(Path(name).name)
        target_dir = self.ctx.upload_dir or platforms.downloads_dir() / "local-file-sharer"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_upload_path(target_dir, safe)
        length = int(self.headers.get("Content-Length") or 0)
        received = 0
        try:
            with open(target, "wb") as handle:
                while received < length:
                    chunk = self.rfile.read(min(CHUNK, length - received))
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    self.ctx.tracker.add_received(len(chunk))
        except OSError as exc:
            # Do not leave a half-written file behind under a name the sender
            # was told about: the whole point is that the file is complete.
            with contextlib.suppress(OSError):
                target.unlink()
            self.send_json({"error": f"upload failed: {exc}"}, status=500)
            return
        if received < length:
            # The client hung up mid-upload. Reporting success here would hand
            # back a truncated file with a clean name.
            with contextlib.suppress(OSError):
                target.unlink()
            self.send_json(
                {"error": f"upload incomplete: {received} of {length} bytes", "bytes": received},
                status=400,
            )
            return
        self.send_json({"ok": True, "name": target.name, "bytes": received, "path": str(target)})

    # ── helpers ────────────────────────────────────────────────────────────
    def guess_type(self, path) -> str:  # noqa: ANN001
        return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


# ── Pure helpers (unit-tested) ─────────────────────────────────────────────
def parse_range(header: str, size: int) -> list[tuple[int, int]]:
    """Parse a ``Range: bytes=…`` header into inclusive ``(start, end)`` pairs.

    Supports ``bytes=0-99``, ``bytes=100-``, ``bytes=-500`` and comma lists.
    Raises :class:`ValueError` when the header is unsatisfiable.
    """
    if not header:
        return []
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return []
    spec = header.split("=", 1)[1].strip()
    ranges: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            raise ValueError("malformed range")
        start_text, _, end_text = part.partition("-")
        start_text = start_text.strip()
        end_text = end_text.strip()
        if not start_text:
            # suffix range: last N bytes
            try:
                suffix = int(end_text)
            except ValueError as exc:
                raise ValueError("malformed suffix range") from exc
            if suffix <= 0:
                raise ValueError("empty suffix range")
            start = max(0, size - suffix)
            end = size - 1
        else:
            try:
                start = int(start_text)
            except ValueError as exc:
                raise ValueError("malformed range start") from exc
            end = size - 1 if not end_text else min(int(end_text), size - 1)
        if start > end or start >= size:
            raise ValueError("unsatisfiable range")
        ranges.append((start, end))
        if len(ranges) >= MULTI_RANGE_LIMIT:
            break
    if not ranges:
        raise ValueError("empty range")
    return ranges


def _content_disposition(filename: str, *, inline: bool = False) -> str:
    quoted = urllib.parse.quote(filename)
    kind = "inline" if inline else "attachment"
    ascii_name = re.sub(r"[^\x20-\x7e]", "_", filename).replace('"', "'")
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


def _top_level_group(entry: FileEntry) -> str:
    """The sub-folder of the share a file sits in, or ``""`` for the root.

    ``rel`` is the path inside the share and keeps the shared folder's name
    (``Photos/2024/a.jpg``), so a file one level down is a loose file of the
    share (``Photos/b.jpg``) while anything deeper belongs to the sub-folder
    named by the second segment (``2024``).
    """
    parts = entry.rel.split("/")
    return parts[1] if len(parts) > 2 else ""


def _entry_cards(manifest) -> list[str]:  # noqa: ANN001 - Manifest, imported lazily
    """One card per sub-folder of the share, then one card per file in it.

    A share is usually one folder full of sub-folders, so its sub-folders are
    what the page shows: each is a single card with a file count and a zip
    button. Files sitting directly in the shared folder are listed one by one.
    """
    loose: list[FileEntry] = []
    groups: dict[str, list[FileEntry]] = {}
    for entry in manifest.iter_files():
        group = _top_level_group(entry)
        if group:
            groups.setdefault(group, []).append(entry)
        else:
            loose.append(entry)

    cards: list[str] = []
    for name in sorted(groups, key=str.lower):
        members = groups[name]
        total = sum(entry.size for entry in members)
        count = len(members)
        label = f"{count} file{'' if count == 1 else 's'}"
        cards.append(
            f'<article class="card folder" data-kind="folder" data-name="{html.escape(name.lower())}">'
            f'<div class="card-icon">📁</div>'
            f'<div class="card-main"><h3 title="{html.escape(name)}">{html.escape(name)}</h3>'
            f"<p>{label} · {human_size(total)}</p></div>"
            f'<div class="card-actions">'
            f'<a class="btn" href="/folder/{urllib.parse.quote(name)}" download>Download zip</a>'
            f"</div></article>"
        )
    for entry in sorted(loose, key=lambda e: e.rel.lower()):
        # the shared folder's own name is noise on every card; the rest is not
        label = entry.rel.split("/", 1)[-1] if "/" in entry.rel else entry.name
        cards.append(
            f'<article class="card file" data-kind="file" data-id="{entry.file_id}" '
            f'data-name="{html.escape(label.lower())}">'
            f'<div class="card-icon">{_icon_for(entry)}</div>'
            f'<div class="card-main"><h3 title="{html.escape(label)}">'
            f"{html.escape(label)}</h3>"
            f"<p>{entry.size_text} · {html.escape(kind_of(entry.path))}</p></div>"
            f'<div class="card-actions">'
            f'<a class="btn btn-ghost" href="/f/{entry.file_id}" download>Download</a>'
            f'<a class="btn btn-ghost" href="/s/{entry.file_id}">Stream</a>'
            f"</div></article>"
        )
    return cards


def _icon_for(entry: FileEntry) -> str:
    kind = kind_of(entry.path)
    return {"video": "🎬", "audio": "🎵", "image": "🖼️", "pdf": "📕", "text": "📄"}.get(kind, "📦")


ERROR_FALLBACK = """<!doctype html><html><head><meta charset="utf-8">
<title>{{status}} · {{brand}}</title><link rel="stylesheet" href="/static/app.css"></head>
<body class="error-page"><main><h1>{{status}}</h1><p>{{message}}</p><p class="hint">{{hint}}</p>
<a href="/">← back to {{brand}}</a></main></body></html>"""

LOGIN_FALLBACK = """<!doctype html><html><head><meta charset="utf-8"><title>PIN · {{brand}}</title>
<style>body{font-family:system-ui;background:#0b1220;color:#e2e8f0;display:grid;place-items:center;height:100vh}
form{background:#111a2b;padding:2rem;border-radius:16px;display:grid;gap:1rem;min-width:280px}
input{padding:.7rem;border-radius:8px;border:1px solid #24314a;background:#0b1220;color:#fff}
button{padding:.7rem 1rem;border-radius:8px;border:0;background:#fff;color:#000;font-weight:600}</style>
</head><body><form method="post"><h1>{{brand}}</h1><p>Enter the PIN to continue</p>
<input name="pin" type="password" autofocus inputmode="numeric"><p class="{{hidden}}">{{error}}</p>
<button type="submit">Unlock</button></form></body></html>"""


# ── binary query helpers (torrent announce) ────────────────────────────────
def _binary_param(params: dict[str, list[str]], key: str) -> bytes:
    values = params.get(key) or []
    if not values:
        return b""
    raw = values[0]
    if isinstance(raw, bytes):
        return raw
    # ``parse_qs`` decoded percent escapes with latin-1 above; re-encode.
    return raw.encode("latin-1", "ignore")


def _text_param(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    if not values:
        return ""
    raw = values[0]
    return raw.decode("latin-1", "ignore") if isinstance(raw, bytes) else raw


def _int_param(params: dict[str, list[str]], key: str, default: int) -> int:
    text = _text_param(params, key)
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default
