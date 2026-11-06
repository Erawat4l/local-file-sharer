"""The share orchestrator: pick → prepare → start → publish → wait.

This module is the bridge between the CLI/UI and the transfer engine. It owns

* mode selection (download / serve / stream / torrent),
* network outputs in priority order (LAN → public → Tailscale → Cloudflare),
* the QR code, the clipboard one-liners and the copy menu,
* the live dashboard and the graceful shutdown / expiry handling.
"""

from __future__ import annotations

import contextlib
import secrets
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from rich.console import Group
from rich.prompt import Confirm, Prompt
from rich.text import Text

from . import APP_NAME, __version__, links, platforms
from .config import Config
from .errors import ShareError, SharerError
from .fmt import human_size
from .network import (
    CloudflareTunnel,
    TailscaleStatus,
    local_endpoints,
    public_ip_async,
    resolve_public_ip,
    start_cloudflare_tunnel,
    tailscale_serve,
    tailscale_status,
)
from .picker import SelectionSummary, analyze, common_root
from .qr import choose_error_correction, print_qr
from .state import State
from .transfer.manifest import Manifest
from .transfer.server import ShareServer, load_assets, suggest_port
from .transfer.torrent import (
    SeedStats,
    TorrentBuild,
    TrackerRegistry,
    build_torrent,
    files_from_entries,
    torrent_hint,
)
from .ui import banner
from .ui.components import error_panel, info_panel, mode_badge
from .ui.console import console, current_theme, panel, screen_width, wrap_command
from .ui.dashboard import Dashboard, DashboardOptions, render_summary

MODES = ("download", "serve", "stream", "torrent")
MODE_TAGLINES = {
    "download": "forced download · resumable · folders auto-zip",
    "serve": "web file browser with search and multi-select",
    "stream": "media player with instant seeking",
    "torrent": ".torrent + magnet · built-in tracker · HTTP webseed",
}


@dataclass
class ShareRequest:
    paths: list[Path]
    mode: str = "download"
    port: int = 8080
    pin: str = ""
    expire_minutes: int = 0
    allow_upload: bool = False
    use_tailscale: bool | None = None  # None = auto-detect and ask
    use_cloudflare: bool | None = None  # None = ask
    funnel: bool = False
    open_browser: bool = False
    no_qr: bool = False
    copy_links: bool = True
    torrent: TorrentBuild | None = None
    interface: str = ""


@dataclass
class ShareResult:
    url: str = ""
    lan_url: str = ""
    public_ip: str = ""
    tailscale_url: str = ""
    cloudflare_url: str = ""
    torrent: TorrentBuild | None = None
    magnet: str = ""
    bytes_sent: int = 0
    duration: float = 0.0
    cancelled: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Preparation
# ─────────────────────────────────────────────────────────────────────────────
def prepare_manifest(paths: Sequence[Path], *, mode: str, expire_minutes: int, title: str = "") -> Manifest:
    paths = [p for p in paths if Path(p).exists()]
    if not paths:
        raise ShareError(
            "Nothing to share",
            hints=[
                "Pick at least one file or folder",
                "Paths are case-sensitive on Linux/Termux",
                "Use `lfs /sdcard/Download` to share a folder directly",
            ],
        )
    expires_at = time.time() + expire_minutes * 60 if expire_minutes > 0 else None
    if title == "":
        first = paths[0]
        title = first.name if len(paths) == 1 else f"{len(paths)} items from {platforms.hostname()}"
    manifest = Manifest.build(paths, mode=mode, expires_at=expires_at, title=title)
    if manifest.empty:
        raise ShareError(
            "The selection contains no readable files",
            hints=[
                "Folders are shared recursively — make sure they are not empty",
                "Termux: run `termux-setup-storage` to allow access to shared storage",
                "Check permissions with `ls -l`",
            ],
        )
    return manifest


def upload_folder(manifest: Manifest) -> Path:
    """Where to put files received from the browser.

    The parent of the first shared item: if you shared ``~/Photos``, an upload
    arrives in ``~/Photos`` itself when the share is a folder, and beside the
    file when a single file was shared.
    """
    first = manifest.items[0]
    path = Path(first.path)
    return path if path.is_dir() else path.parent


def build_torrent_for(
    manifest: Manifest, *, base_url: str, tracker_url: str, piece_length: int = 0
) -> TorrentBuild:
    name = manifest.items[0].name if len(manifest.items) == 1 else f"{APP_NAME}-{int(time.time())}"
    files = files_from_entries(manifest.iter_files(), root_name=name)
    with console().status("hashing pieces for the torrent…"):
        return build_torrent(
            files,
            name=name,
            tracker_url=tracker_url,
            webseed_url=f"{base_url.rstrip('/')}/webseed/",
            piece_length=piece_length,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Sharing
# ─────────────────────────────────────────────────────────────────────────────
class ShareRunner:
    """Owns one share session end-to-end."""

    def __init__(self, request: ShareRequest, *, config: Config, state: State) -> None:
        self.request = request
        self.config = config
        self.state = state
        self.manifest: Manifest | None = None
        self.server: ShareServer | None = None
        self.tunnel: CloudflareTunnel | None = None
        self.tailscale: TailscaleStatus | None = None
        self.torrent: TorrentBuild | None = None
        self.tracker_registry = TrackerRegistry()
        self.torrent_stats = SeedStats()
        self._stop = threading.Event()

    # ── public API ──────────────────────────────────────────────────────────
    def run(self, *, wait: bool = True) -> ShareResult:
        manifest = prepare_manifest(
            self.request.paths,
            mode=self.request.mode,
            expire_minutes=self.request.expire_minutes,
        )
        self.manifest = manifest
        port = suggest_port(self.request.port, self.config.network.host)
        self.server = ShareServer(
            manifest,
            host=self.config.network.host,
            port=port,
            pin=self.request.pin or self.config.share.pin,
            allow_upload=self.request.allow_upload or self.config.share.allow_upload,
            # Uploads go next to the thing being shared, so the sender can find
            # them; without this they landed in ~/local-file-sharer and were
            # invisible to the person who had just invited the upload.
            upload_dir=upload_folder(manifest),
            theme=self.config.theme,
            on_download=self._on_download,
        )
        self.server.context.tracker_registry = self.tracker_registry
        self.server.context.torrent_stats = self.torrent_stats
        self.server.context.stop_callback = self.stop

        endpoints = local_endpoints(
            prefer_interface=self.request.interface or self.config.network.prefer_interface
        )
        lan = endpoints[0].ip if endpoints else (platforms.primary_local_ip() or "127.0.0.1")
        one_liners = links.one_liners_for(
            f"http://{lan}:{port}",
            connections=self.config.speed.parallel_connections,
            kinds=tuple(self.config.share.one_liners),
        )
        self.server.context.one_liners = one_liners

        self.server.start()
        base_url = f"http://{lan}:{self.server.port}"
        self.server.set_base_url(base_url)
        self.server.context.peer_name = platforms.hostname()
        if self.request.mode == "torrent":
            self.torrent = build_torrent_for(
                manifest,
                base_url=base_url,
                tracker_url=f"{base_url}/announce",
                piece_length=self.config.torrent.piece_length,
            )
            self.server.context.torrent = self.torrent
            self.torrent_path = self.torrent.save(platforms.downloads_dir() / f"{self.torrent.name}.torrent")
        if not self.server.wait_until_ready(timeout=5):
            raise ShareError(
                f"The server started on port {self.server.port} but is not answering",
                hints=[
                    "Another program may be intercepting the port — pass `--port 9000`",
                    "Run `lfs doctor` to check the firewall and local connectivity",
                ],
            )

        self.state.add_share()
        self.state.remember_dir(manifest.items[0].path.parent)
        for item in manifest.items:
            self.state.add_recent(item.path, limit=self.config.ui.recent_limit)

        result = ShareResult(
            url=base_url + "/",
            lan_url=base_url + "/",
            torrent=self.torrent,
            magnet=self.torrent.magnet if self.torrent else "",
        )
        self._publish(result, endpoints)
        if wait:
            self._wait_loop(result)
        return result

    def stop(self) -> None:
        self._stop.set()

    # ── publication ─────────────────────────────────────────────────────────
    def _publish(self, result: ShareResult, endpoints) -> None:  # noqa: ANN001
        con = console()
        theme = current_theme()
        manifest = self.manifest
        assert manifest is not None and self.server is not None

        banner.animate(theme, frames=6) if self.config.animate_banner else banner.static(theme)

        summary_rows = [
            ("mode", str(mode_badge(manifest.mode))),
            (
                "files",
                f"{manifest.file_count} file{'s' if manifest.file_count != 1 else ''} · "
                f"{manifest.folder_count} folder{'s' if manifest.folder_count != 1 else ''}",
            ),
            ("size", human_size(manifest.total_bytes)),
            ("expires", manifest.expiry_text()),
        ]
        if self.server.context.pin:
            summary_rows.append(("PIN", self.server.context.pin))
        con.print(info_panel(summary_rows, title="share ready"))

        # ── LAN (primary) ───────────────────────────────────────────────────
        lan_lines: list[tuple[str, str]] = []
        main = endpoints[0] if endpoints else None
        if main:
            lan_lines.append((f"{main.icon} {main.kind}", f"{result.lan_url}  ({main.ip})"))
        lan_lines.append(("local", f"http://127.0.0.1:{self.server.port}/  (this device only)"))
        for extra in endpoints[1:4]:
            lan_lines.append((f"{extra.icon} {extra.kind}", f"http://{extra.ip}:{self.server.port}/"))
        con.print(info_panel(lan_lines, title="direct (fastest — same Wi-Fi)", border=theme.accent))

        if self.config.share.show_qr and not self.request.no_qr:
            self._show_qr(result.lan_url)

        # ── public IP (background) ──────────────────────────────────────────
        if self.config.network.public_ip_lookup:
            thread = public_ip_async()
            result.public_ip = resolve_public_ip(thread, wait=self.config.network.connect_timeout * 1.5) or ""
            if result.public_ip:
                con.print(
                    info_panel(
                        [("public IP", result.public_ip)],
                        title="public (needs port-forwarding on your router)",
                        border=theme.border_soft,
                    )
                )

        # ── Tailscale ───────────────────────────────────────────────────────
        result.tailscale_url = self._maybe_tailscale()
        result.cloudflare_url = self._maybe_cloudflare(result)

        # ── one-liners ──────────────────────────────────────────────────────
        self._show_one_liners(result)

        if self.request.mode == "torrent" and self.torrent is not None:
            self._show_torrent(self.torrent)

        if self.request.open_browser:
            platforms.open_in_browser(result.lan_url)

        if self.config.share.copy_links and self.request.copy_links:
            payload = links.clipboard_payload(
                {
                    "link": result.lan_url,
                    **({"tailscale": result.tailscale_url} if result.tailscale_url else {}),
                    **({"cloudflare": result.cloudflare_url} if result.cloudflare_url else {}),
                    **({"magnet": result.magnet} if result.magnet else {}),
                }
            )
            if platforms.copy_to_clipboard(payload):
                con.print(
                    Text(
                        f" ✓ copied all links to the clipboard ({platforms.clipboard_tool_name()})",
                        style=theme.success,
                    )
                )
            else:
                con.print(Text(" (no clipboard tool found — press `c` later to retry)", style=theme.faint))

    def _show_qr(self, url: str) -> None:
        theme = current_theme()
        con = console()
        ecc = choose_error_correction(url, width=screen_width())
        con.print(Text("scan to open", style=theme.muted))
        print_qr(url, theme=theme, compact=True, error_correction=ecc, caption=url)

    def _maybe_tailscale(self) -> str:
        request = self.request
        status = tailscale_status()
        self.tailscale = status
        if request.use_tailscale is False:
            return ""
        theme = current_theme()
        con = console()
        if not status.installed:
            if request.use_tailscale:
                con.print(
                    error_panel(
                        "Tailscale is not installed on this device",
                        hints=[
                            platforms.install_hint_for("tailscale"),
                            "Or skip it — direct Wi-Fi already works",
                        ],
                        title="Tailscale unavailable",
                    )
                )
            return ""
        if not status.shareable:
            con.print(
                Text(f" Tailscale found but not ready ({status.summary()}) — skipping", style=theme.warning)
            )
            return ""
        con.print(
            info_panel(
                [("tailnet", status.best_host), ("ip", status.ipv4), ("peers", str(len(status.peers)))],
                title="tailscale detected",
                border=theme.info,
            )
        )
        ask = request.use_tailscale
        if ask is None:
            ask = Confirm.ask(
                Text("publish this share on your tailnet with `tailscale serve`?", style=theme.accent),
                default=False,
                console=con,
            )
        if not ask:
            con.print(Text(" skipped Tailscale", style=theme.faint))
            return ""
        try:
            with con.status("starting tailscale serve…"):
                _, url = tailscale_serve(
                    self.server.port if self.server else self.request.port, funnel=request.funnel
                )
        except SharerError as exc:
            con.print(
                error_panel(exc.message, hints=exc.all_hints(), detail=exc.detail or "", title=exc.title)
            )
            return ""
        con.print(info_panel([("tailscale", url)], title="reachable on your tailnet", border=theme.info))
        return url

    def _maybe_cloudflare(self, result: ShareResult) -> str:
        del result  # kept for signature symmetry with the other publishers
        request = self.request
        theme = current_theme()
        con = console()
        if request.use_cloudflare is False:
            return ""
        if not platforms.find_tool("cloudflared"):
            if request.use_cloudflare:
                con.print(
                    error_panel(
                        "cloudflared is not installed",
                        hints=[platforms.install_hint_for("cloudflared"), "Direct Wi-Fi is faster anyway"],
                        title="Cloudflare Tunnel unavailable",
                    )
                )
            return ""
        ask = request.use_cloudflare
        if ask is None:
            con.print(
                panel(
                    Text(
                        "Cloudflare Tunnel works anywhere in the world, but traffic is relayed "
                        "through Cloudflare's edge: expect roughly 1–8 MB/s instead of LAN speed.",
                        style=theme.warning,
                    ),
                    title="! slow-speed warning",
                    border=theme.warning,
                )
            )
            ask = Confirm.ask(
                Text("generate a temporary public Cloudflare link?", style=theme.accent),
                default=False,
                console=con,
            )
        if not ask:
            return ""
        try:
            with con.status("starting cloudflared quick tunnel (this can take ~10s)…"):
                tunnel = start_cloudflare_tunnel(self.server.port if self.server else self.request.port)
            self.tunnel = tunnel
        except SharerError as exc:
            con.print(
                error_panel(exc.message, hints=exc.all_hints(), detail=exc.detail or "", title=exc.title)
            )
            return ""
        con.print(
            info_panel(
                [("cloudflare", tunnel.url)],
                title="public link (slower, works anywhere)",
                border=theme.warning,
            )
        )
        return tunnel.url

    def _show_one_liners(self, result: ShareResult) -> None:
        theme = current_theme()
        con = console()
        target = result.lan_url
        if self.manifest and len(self.manifest.items) == 1 and not self.manifest.items[0].is_dir:
            target = links.file_url(result.lan_url.rstrip("/"), self.manifest.items[0].files[0].file_id)
        commands = links.build_all(
            result.lan_url,
            connections=self.config.speed.parallel_connections,
            target=target,
            enabled=tuple(self.config.share.one_liners),
        )
        rows: list[Text] = []
        for command in commands:
            rows.append(Text(f" {command.label}", style=theme.muted))
            for raw in command.command.splitlines():
                # Wrap on argument boundaries so a copied line never splits a flag.
                for line in wrap_command(raw):
                    rows.append(Text(line, style=theme.secondary))
        con.print(panel(Group(*rows), title="copy-paste one-liners (LAN)", border=theme.border))

    def _show_torrent(self, torrent: TorrentBuild) -> None:
        theme = current_theme()
        con = console()
        con.print(
            panel(
                Group(
                    Text(f"🧲 {torrent.magnet}", style=theme.link),
                    Text(""),
                    Text(torrent_hint(torrent), style=theme.muted),
                    Text(f"info-hash {torrent.info_hash_hex}", style=theme.faint),
                ),
                title="torrent",
                border=theme.success,
            )
        )

    # ── main loop ───────────────────────────────────────────────────────────
    def _wait_loop(self, result: ShareResult) -> None:
        theme = current_theme()
        con = console()
        server = self.server
        assert server is not None and self.manifest is not None
        options = DashboardOptions(
            title=f"{self.request.mode} · {human_size(self.manifest.total_bytes)}",
            links=[
                *([("LAN", result.lan_url)] if result.lan_url else []),
                *([("tailscale", result.tailscale_url)] if result.tailscale_url else []),
                *([("cloudflare", result.cloudflare_url)] if result.cloudflare_url else []),
            ],
            footer="press Ctrl+C to stop · `c` copies links · `o` opens the browser · `q` quits",
            refresh=0.5,
        )
        started = time.time()
        dashboard = None
        if self.config.ui.live_dashboard:
            dashboard = Dashboard(server.tracker, options).start()
        try:
            while not self._stop.wait(0.4):
                if self.manifest.expired:
                    con.print(Text(" ⏰ share expired — stopping the server", style=theme.warning))
                    break
                _drain_keys(self)
                if not server.running:
                    break
        except KeyboardInterrupt:
            result.cancelled = True
        finally:
            if dashboard is not None:
                dashboard.stop()
            self._shutdown()
            result.duration = time.time() - started
            result.bytes_sent = server.tracker.bytes_sent
            self.state.add_transfer(bytes_sent=result.bytes_sent, files=self.manifest.file_count)
            if result.bytes_sent:
                con.print(render_summary(server.tracker))
                self.state.add_history(
                    {
                        "mode": self.request.mode,
                        "path": str(self.manifest.items[0].path),
                        "files": self.manifest.file_count,
                        "bytes": result.bytes_sent,
                        "url": result.lan_url,
                    }
                )
            else:
                con.print(Text(" no transfers completed — server stopped", style=theme.faint))

    def _shutdown(self) -> None:
        if self.tunnel is not None:
            self.tunnel.stop()
            self.tunnel = None
        if self.server is not None:
            with contextlib.suppress(Exception):
                self.server.stop()

    # ── callbacks ───────────────────────────────────────────────────────────
    def _on_download(self, name: str) -> None:
        del name  # only used as a signal; the tracker records details
        self.state.add_download(1)
        if self.config.ui.notify_on_done:
            with contextlib.suppress(OSError):
                console().bell()


# ─────────────────────────────────────────────────────────────────────────────
# Interactive helpers while the share is running
# ─────────────────────────────────────────────────────────────────────────────
def _drain_keys(runner: ShareRunner) -> None:
    """Non-blocking key handling for the running share (POSIX only)."""
    if platforms.WINDOWS or not console().is_terminal:
        return
    import select  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import termios  # noqa: PLC0415
    import tty  # noqa: PLC0415

    if not sys.stdin.isatty():
        return
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return
    if not ready:
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        char = sys.stdin.read(1)
    except OSError:
        return
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    theme = current_theme()
    con = console()
    if char in ("q", "\x03", "\x04"):
        runner.stop()
    elif char == "c":
        urls = runner.links_block()
        if platforms.copy_to_clipboard(urls):
            con.print(Text(" ✓ links copied", style=theme.success))
        else:
            con.print(Text(" ✗ no clipboard available", style=theme.danger))
    elif char == "o" and runner.server is not None:
        platforms.open_in_browser(runner.server.url)
    elif char == "p" and runner.manifest is not None:
        print_qr(runner.server.url if runner.server else "", theme=theme, compact=True)


def _share_links_block(runner: ShareRunner) -> str:
    server = runner.server
    if server is None:
        return ""
    return links.clipboard_payload({"link": server.url, "zip": links.zip_url(server.url)})


ShareRunner.links_block = _share_links_block  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# Convenience entry points
# ─────────────────────────────────────────────────────────────────────────────
def run_share(
    paths: Iterable[Path],
    *,
    config: Config,
    state: State,
    mode: str = "download",
    wait: bool = True,
    **kwargs,
) -> ShareResult:
    request = ShareRequest(paths=[Path(p) for p in paths], mode=mode, **kwargs)
    if request.mode not in MODES:
        raise ShareError(
            f"Unknown mode {request.mode!r}",
            hints=[f"Choose one of: {', '.join(MODES)}"],
        )
    return ShareRunner(request, config=config, state=state).run(wait=wait)


def make_pin() -> str:
    """A 6-digit PIN that is easy to type on a phone."""
    return f"{secrets.randbelow(1_000_000):06d}"


def ask_pin(config: Config) -> str:
    theme = current_theme()
    con = console()
    if config.share.pin:
        return config.share.pin
    if not Confirm.ask(
        Text("protect this share with a PIN?", style=theme.accent), default=False, console=con
    ):
        return ""
    pin = Prompt.ask(
        Text("PIN (Enter for a random 6-digit code)", style=theme.accent), default="", console=con
    )
    return pin.strip() or make_pin()


def ask_expiry(config: Config) -> int:
    theme = current_theme()
    con = console()
    default = config.share.auto_expire_minutes
    answer = Prompt.ask(
        Text("auto-expire after minutes (0 = never)", style=theme.accent),
        default=str(default or 0),
        console=con,
    )
    try:
        return max(0, int(answer))
    except ValueError:
        con.print(Text(" not a number — keeping 0 (never)", style=theme.warning))
        return 0


def summarize_selection(paths: Sequence[Path]) -> SelectionSummary:
    return analyze(paths)


def root_of(paths: Sequence[Path]) -> Path:
    return common_root(paths)


_ = (load_assets, error_panel, APP_NAME, __version__)
