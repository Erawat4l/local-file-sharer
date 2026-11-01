"""Threaded HTTP server lifecycle for a share.

``ShareServer`` owns the socket, the :class:`ShareContext` and the background
thread. The CLI starts it, prints links, then polls :attr:`ShareServer.url`
and the tracker to render the live dashboard.
"""

from __future__ import annotations

import socket
import socketserver
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .. import platforms
from ..errors import ShareError
from .http_handler import ShareContext, ShareRequestHandler
from .manifest import Manifest
from .stats import TransferTracker

WEB_ASSETS = Path(__file__).resolve().parent.parent / "web" / "assets"


def load_assets() -> dict[str, str]:
    """Read the bundled web UI templates (never fails for the CLI)."""
    assets: dict[str, str] = {}
    for name in ("downloader.html", "server.html", "player.html", "login.html", "error.html"):
        path = WEB_ASSETS / name
        try:
            assets[name] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    assets["index.html"] = assets.get("downloader.html", "")
    return assets


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not platforms.WINDOWS
    request_queue_size = 128

    def __init__(self, address, handler, *, context: ShareContext) -> None:  # noqa: ANN001
        self.context = context
        super().__init__(address, handler)

    def server_bind(self) -> None:
        if platforms.WINDOWS and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        # Keep stray tracebacks off the dashboard; the tracker counts errors.
        self.context.tracker.note_error()

    def verify_request(self, request, client_address) -> bool:  # noqa: ANN001
        try:
            platforms.tune_socket(request)
        except OSError:
            pass
        return True


class Handler(ShareRequestHandler):
    @property
    def ctx(self) -> ShareContext:
        return self.server.context  # type: ignore[attr-defined]


class ShareServer:
    """A running share: HTTP server + tracker + urls."""

    def __init__(
        self,
        manifest: Manifest,
        *,
        host: str = "0.0.0.0",
        port: int = 8080,
        pin: str = "",
        allow_upload: bool = False,
        upload_dir: Path | None = None,
        one_liners: dict[str, str] | None = None,
        theme: str = "dark",
        auto_port: bool = True,
        on_download: Callable[[str], None] | None = None,
    ) -> None:
        self.manifest = manifest
        self.requested_host = host
        self.requested_port = port
        self.tracker = TransferTracker()
        self.assets = load_assets()
        self.context = ShareContext(
            manifest=manifest,
            tracker=self.tracker,
            pin=pin,
            allow_upload=allow_upload,
            upload_dir=upload_dir,
            assets=self.assets,
            one_liners=one_liners or {},
            theme=theme,
        )
        self.on_download = on_download
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self.host = host
        self.started_at = 0.0
        self._auto_port = auto_port
        self._log: list[str] = []

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self, *, block: bool = False) -> ShareServer:
        if self._server is not None:
            return self
        port = self.requested_port
        attempts = 0
        last_error: OSError | None = None
        while attempts < 40:
            try:
                server = _Server((self.host, port), Handler, context=self.context)
            except OSError as exc:
                last_error = exc
                if not self._auto_port:
                    break
                port += 1
                attempts += 1
                continue
            self._server = server
            self.port = server.server_address[1]
            break
        if self._server is None:
            raise ShareError(
                f"Could not bind to {self.host}:{self.requested_port}",
                hints=[
                    "Another program is using that port — pass `--port 9000`",
                    "Windows: allow Python through Defender Firewall when prompted",
                    "Try `start doctor` to see which ports are free",
                ],
                detail=str(last_error) if last_error else None,
            )
        self.started_at = time.time()
        if block:
            self._server.serve_forever(poll_interval=0.2)
        else:
            self._thread = threading.Thread(
                target=self._server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True, name="lfs-http"
            )
            self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except OSError:
                pass
            try:
                self._server.server_close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def set_base_url(self, url: str) -> None:
        """Set the public origin; a trailing slash would double up in every URL."""
        self.context.base_url = url.rstrip("/")

    def track(self, name: str) -> None:
        if self.on_download:
            try:
                self.on_download(name)
            except Exception:  # noqa: BLE001 - never break a transfer
                pass

    # ── introspection ──────────────────────────────────────────────────────
    @property
    def stats(self) -> dict:
        return self.tracker.snapshot()

    @property
    def url(self) -> str:
        """Most useful URL for another device on the same network."""
        if self.context.base_url:
            return self.context.base_url
        endpoint = platforms.primary_local_ip() or "127.0.0.1"
        return f"http://{endpoint}:{self.port}/"

    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def url_for(self, item) -> str:  # noqa: ANN001
        base = self.url.rstrip("/")
        return f"{base}{item.url_path}"

    def reachable(self, *, host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
        import urllib.request  # noqa: PLC0415

        url = f"http://{host}:{self.port}/healthz"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                return response.status == HTTPStatus.OK
        except OSError:
            return False

    def wait_until_ready(self, *, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.reachable():
                return True
            time.sleep(0.05)
        return False


class ShareSession:
    """Convenience: build a manifest, start the server, tear both down."""

    def __init__(self, manifest: Manifest, **kwargs) -> None:
        self.manifest = manifest
        self.server = ShareServer(manifest, **kwargs)

    def __enter__(self) -> ShareServer:
        return self.server.start()

    def __exit__(self, *exc_info) -> None:
        self.server.stop()


def quick_start(paths, *, port: int = 8080, host: str = "0.0.0.0", pin: str = "", **kwargs) -> ShareServer:
    manifest = Manifest.build(paths, mode=kwargs.pop("mode", "download"))
    server = ShareServer(manifest, port=port, host=host, pin=pin, **kwargs)
    return server.start()


def port_free(port: int, host: str = "0.0.0.0") -> bool:
    return platforms.port_is_free(port, host)


def suggest_port(preferred: int, host: str = "0.0.0.0") -> int:
    try:
        return platforms.find_free_port(preferred, host=host)
    except OSError as exc:
        raise ShareError(
            f"No free port found near {preferred}",
            hints=["Pass an explicit port with `--port 9000`", "Close other servers"],
        ) from exc


_ = socketserver  # re-exported for tests that subclass the handler
