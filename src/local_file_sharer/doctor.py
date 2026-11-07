"""``lfs doctor`` — dependency, firewall and network diagnostics.

Every check returns a :class:`Check` with a status, a one-line finding and an
actionable fix. Nothing here raises: a broken environment should produce a
readable report, not a traceback.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rich.console import Group
from rich.table import Table
from rich.text import Text

from . import APP_NAME, __version__, platforms
from .config import Config, config_path, load_config, secrets_path
from .fmt import human_size
from .network import (
    firewall_hint,
    guess_wifi_band,
    local_endpoints,
    os_name,
    public_ip,
    tailscale_status,
)
from .ui.components import divider, info_panel
from .ui.console import console, current_theme, screen_width

OK = "ok"
WARN = "warn"
FAIL = "fail"
INFO = "info"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""
    group: str = "general"

    @property
    def icon(self) -> str:
        return {OK: "✔", WARN: "▲", FAIL: "✖", INFO: "•"}.get(self.status, "•")


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]

    @property
    def healthy(self) -> bool:
        return not self.failures

    def score(self) -> int:
        if not self.checks:
            return 0
        weights = {OK: 1.0, INFO: 1.0, WARN: 0.6, FAIL: 0.0}
        return int(sum(weights.get(c.status, 0.5) for c in self.checks) / len(self.checks) * 100)


# ── individual checks ───────────────────────────────────────────────────────
def check_python() -> Check:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    # ``requires-python`` already guarantees 3.9+, but a user may run the source
    # tree with an older interpreter; report it instead of failing obscurely.
    if sys.version_info < (3, 9):  # noqa: UP036
        return Check(
            "python",
            FAIL,
            f"{version} (needs 3.9+)",
            "Install Python 3.11+: pkg install python / winget install Python.Python.3.12",
            "runtime",
        )
    impl = platform_impl()
    return Check("python", OK, f"{version} ({impl})", "", "runtime")


def platform_impl() -> str:
    if platforms.TERMUX:
        return "Termux/Android"
    if platforms.WINDOWS:
        return "Windows"
    if platforms.MACOS:
        return "macOS"
    return "CPython/Linux"


def check_dependencies() -> list[Check]:
    checks: list[Check] = []
    for module, package, required in (
        ("rich", "rich", True),
        ("qrcode", "qrcode", True),
    ):
        try:
            __import__(module)
        except ImportError:
            checks.append(
                Check(
                    f"{module}",
                    FAIL if required else WARN,
                    "not installed",
                    f"pip install {package}   (or: pipx install local-file-sharer)",
                    "dependencies",
                )
            )
        else:
            checks.append(Check(module, OK, "installed", "", "dependencies"))
    try:  # Python 3.11+ has tomllib in the stdlib
        import tomllib  # noqa: F401,PLC0415
    except ImportError:
        try:
            import tomli  # noqa: F401,PLC0415
        except ImportError:
            checks.append(
                Check(
                    "toml",
                    WARN,
                    "no TOML parser (Python < 3.11)",
                    "pip install tomli — the config file will otherwise be ignored",
                    "dependencies",
                )
            )
    try:
        import psutil  # noqa: F401,PLC0415
    except ImportError:
        checks.append(
            Check(
                "psutil",
                INFO,
                "optional — nicer interface detection",
                "pip install psutil for Wi-Fi/Ethernet naming",
                "dependencies",
            )
        )
    return checks


def check_tools() -> list[Check]:
    checks: list[Check] = []
    optional = {
        "tailscale": ("Tailscale links", False),
        "cloudflared": ("Cloudflare Tunnel", False),
        "aria2c": ("16-connection downloads", False),
        "zip": ("zip/unzip helpers", False),
        "curl": ("one-liner downloads", False),
        "wget": ("one-liner downloads", False),
        # Not used by the server yet: the web player cannot switch audio or
        # embedded subtitles (see docs/MULTI-TRACK-AUDIO-SUBS.md). Listing it as
        # "media remuxing" told people a feature existed that does not.
        "ffmpeg": ("media remuxing (not used yet)", False),
    }
    for tool, (purpose, required) in optional.items():
        path = platforms.find_tool(tool)
        if path:
            checks.append(Check(tool, OK, path, "", "tools"))
        else:
            checks.append(
                Check(
                    tool,
                    FAIL if required else INFO,
                    f"missing — {purpose}",
                    platforms.install_hint_for(tool),
                    "tools",
                )
            )
    return checks


def check_sendfile() -> Check:
    if platforms.HAS_SENDFILE:
        return Check("sendfile", OK, "os.sendfile available (zero-copy)", "", "performance")
    if platforms.WINDOWS:
        return Check("sendfile", OK, "socket.sendfile fallback on Windows", "", "performance")
    return Check("sendfile", WARN, "no sendfile — using 512 KiB chunks", "Upgrade Python 3.8+", "performance")


def check_socket_buffer() -> Check:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        platforms.tune_socket(probe)
        size = probe.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
    except OSError as exc:
        return Check(
            "socket buffer", WARN, f"cannot set buffers: {exc}", "Run as a normal user", "performance"
        )
    finally:
        probe.close()
    if size < 256 * 1024:
        return Check(
            "socket buffer",
            WARN,
            f"{human_size(size)} (kernel capped)",
            "Linux: sysctl -w net.core.wmem_max=16777216",
            "performance",
        )
    return Check("socket buffer", OK, f"{human_size(size)} send buffer", "", "performance")


def check_disk(path: Path | None = None) -> Check:
    target = path or platforms.home_dir()
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return Check("disk space", WARN, f"cannot read {target}: {exc}", "", "runtime")
    if usage.free < 256 * 1024 * 1024:
        return Check(
            "disk space",
            WARN,
            f"{human_size(usage.free)} free",
            "Free some space for uploads/zips",
            "runtime",
        )
    return Check("disk space", OK, f"{human_size(usage.free)} free on {target}", "", "runtime")


def check_network() -> list[Check]:
    checks: list[Check] = []
    endpoints = local_endpoints()
    if endpoints:
        detail = ", ".join(f"{e.ip} ({e.kind})" for e in endpoints[:3])
        checks.append(Check("LAN address", OK, detail, "", "network"))
        band = guess_wifi_band()
        if band:
            checks.append(Check("wifi band", INFO, band, "", "network"))
    else:
        checks.append(
            Check(
                "LAN address",
                FAIL,
                "no private IPv4 found",
                "Connect to the same Wi-Fi as your other device, or use Tailscale/Cloudflare",
                "network",
            )
        )
    ip = public_ip(timeout=2.0)
    if ip:
        checks.append(
            Check("public IP", OK, f"{ip} (needs router port-forwarding to be reachable)", "", "network")
        )
    else:
        checks.append(
            Check("public IP", INFO, "offline or blocked (fine on a LAN-only setup)", "", "network")
        )
    return checks


def check_ports(config: Config) -> list[Check]:
    checks: list[Check] = []
    host = config.network.host
    port = config.network.port
    if platforms.port_is_free(port, host):
        checks.append(Check("bind port", OK, f"{host}:{port} is free", "", "network"))
    else:
        alt = None
        for candidate in range(port + 1, port + 20):
            if platforms.port_is_free(candidate, host):
                alt = candidate
                break
        checks.append(
            Check(
                "bind port",
                WARN,
                f"{port} is in use" + (f" (will use {alt})" if alt else ""),
                f"`lfs --port {alt or 9100}` or stop the other program",
                "network",
            )
        )
    hint = firewall_hint(port)
    checks.append(Check("firewall", INFO, hint, "", "network"))
    return checks


def check_tailscale() -> Check:
    status = tailscale_status()
    if not status.installed:
        return Check("tailscale", INFO, "not installed", platforms.install_hint_for("tailscale"), "tunnels")
    if not status.logged_in:
        return Check(
            "tailscale",
            WARN,
            f"installed ({status.version or 'unknown'}) but not connected",
            "Run: tailscale up",
            "tunnels",
        )
    return Check("tailscale", OK, f"{status.ipv4} · {len(status.peers)} peer(s)", "", "tunnels")


def check_cloudflared() -> Check:
    path = platforms.find_tool("cloudflared")
    if not path:
        return Check(
            "cloudflared", INFO, "not installed", platforms.install_hint_for("cloudflared"), "tunnels"
        )
    version = platforms.tool_version(path, "--version")
    return Check("cloudflared", OK, version or path, "", "tunnels")


def check_config() -> list[Check]:
    checks: list[Check] = []
    target = config_path()
    if target.is_file():
        try:
            load_config()
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("config", FAIL, str(exc), f"Fix or delete {target}", "config"))
        else:
            checks.append(Check("config", OK, str(target), "", "config"))
    else:
        checks.append(
            Check(
                "config",
                INFO,
                "no config file yet (defaults are fine)",
                f"lfs config init  →  {target}",
                "config",
            )
        )
    secrets = secrets_path()
    checks.append(
        Check(
            "secrets file",
            OK if secrets.is_file() else INFO,
            str(secrets) if secrets.is_file() else "not present (device 2 SSH details live here, gitignored)",
            "",
            "config",
        )
    )
    return checks


def check_environment() -> list[Check]:
    checks: list[Check] = []
    term = os.environ.get("TERM", "unknown")
    color = "NO_COLOR" in os.environ
    checks.append(
        Check(
            "terminal",
            OK if term not in ("", "dumb") else WARN,
            f"{term} · {console().width} cols · {'no colour' if color else 'colour'}",
            "Use a true-color terminal for the full effect",
            "runtime",
        )
    )
    clip = platforms.clipboard_tool_name()
    checks.append(
        Check(
            "clipboard",
            OK if clip else INFO,
            clip or "no clipboard tool found",
            "Install wl-clipboard / xclip (Linux) or pbcopy (macOS)",
            "runtime",
        )
    )
    checks.append(Check("os", OK, f"{os_name()} · {platforms.hostname()}", "", "runtime"))
    if platforms.TERMUX:
        # Android sleeps the CPU a minute after the screen goes off, which stops
        # a running transfer dead. Say whether the phone will stay awake, and
        # what to install when it will not.
        from . import wakelock  # noqa: PLC0415 - only needed on a phone

        state = wakelock.describe()
        checks.append(
            Check(
                "wake lock",
                OK if wakelock.is_held() else WARN,
                state,
                "Install the Termux:API app, then `pkg install termux-api`",
                "runtime",
            )
        )
    return checks


# ── report ──────────────────────────────────────────────────────────────────
def check_install_copies() -> list[Check]:
    """More than one `lfs` on PATH, and which of them wins.

    This is the single most confusing failure this app has: you upgrade, and
    nothing changes, because a different copy answered the command. It is
    invisible from inside the running process -- nothing the user sees says
    "four lfs binaries are installed and the oldest is first". Report the
    winner and the exact command that removes it.
    """
    from .updater import _first_on_path, find_duplicates  # noqa: PLC0415

    try:
        duplicates = find_duplicates()
    except Exception:  # noqa: BLE001 - a diagnostic must never fail the report
        return []
    if not duplicates:
        return [Check("copy", OK, "one lfs on PATH", "", "install")]

    winner = _first_on_path()
    checks: list[Check] = []
    for item in duplicates:
        runs_first = item["path"] == winner
        how = "pipx uninstall local-file-sharer" if item["kind"] == "pipx" else f"rm {item['path']}"
        checks.append(
            Check(
                name="copy" if not runs_first else "copy wins",
                status=WARN if runs_first else INFO,
                detail=f"{item['path']}  ({item['kind']}, " + ("runs first)" if runs_first else "shadowed)"),
                fix=how if runs_first else "",
                group="install",
            )
        )
    return checks


def run_checks(
    *, config: Config | None = None, skip_tools: bool = False, skip_network: bool = False
) -> Report:
    config = config or load_config()
    report = Report()
    report.add(check_python())
    for check in check_dependencies():
        report.add(check)
    if not skip_tools:
        for check in check_tools():
            report.add(check)
    report.add(check_sendfile())
    report.add(check_socket_buffer())
    report.add(check_disk(platforms.downloads_dir()))
    if not skip_network:
        for check in check_network():
            report.add(check)
        for check in check_ports(config):
            report.add(check)
        report.add(check_tailscale())
        report.add(check_cloudflared())
    for check in check_config():
        report.add(check)
    for check in check_environment():
        report.add(check)
    for check in check_install_copies():
        report.add(check)
    return report


def render_report(report: Report) -> Group:
    theme = current_theme()
    width = screen_width()
    groups: dict[str, list[Check]] = {}
    for check in report.checks:
        groups.setdefault(check.group, []).append(check)

    blocks: list = []
    head = Table.grid(padding=(0, 1))
    head.add_column()
    head.add_column()
    head.add_row(
        Text.assemble((" LFS ", f"bold black on {theme.accent}"), (" doctor", theme.primary)),
        Text(f"{APP_NAME} {__version__} · {os_name()}", style=theme.faint),
    )
    blocks.append(head)
    blocks.append(divider(width=min(width - 2, 72)))

    for name, checks in groups.items():
        table = Table.grid(padding=(0, 2))
        table.add_column(width=2, no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(overflow="fold")
        table.add_column(overflow="fold", style=theme.faint)
        table.add_row("", Text(name.upper(), style=theme.info), "", "")
        for check in checks:
            colour = {OK: theme.success, WARN: theme.warning, FAIL: theme.danger, INFO: theme.muted}[
                check.status
            ]
            table.add_row(
                Text(check.icon, style=colour),
                Text(check.name, style=theme.secondary),
                Text(check.detail, style=theme.secondary if check.status != FAIL else theme.danger),
                Text(check.fix, style=theme.faint) if check.fix else Text(""),
            )
        blocks.append(table)
        blocks.append(Text(""))

    score = report.score()
    verdict_colour = theme.success if score >= 90 else theme.warning if score >= 70 else theme.danger
    blocks.append(divider(width=min(width - 2, 72)))
    blocks.append(
        Text.assemble(
            (" health ", theme.faint),
            (f"{score}%", f"bold {verdict_colour}"),
            (
                f"  ·  {len(report.failures)} blocking issue(s), {len(report.warnings)} warning(s)",
                theme.muted,
            ),
        )
    )
    if report.failures:
        blocks.append(Text(" Fix the ✖ rows above and re-run `lfs doctor`.", style=theme.warning))
    return Group(*blocks)


def quick_status(config: Config | None = None) -> str:
    """A one-line summary used by the main menu header."""
    config = config or load_config()
    endpoint = local_endpoints()
    ip = endpoint[0].ip if endpoint else "no LAN IP"
    return f"{ip} · {os_name()}"


def hints_for_error(exc: BaseException) -> list[str]:
    """Map common exceptions to friendly fixes (used by the CLI error handler)."""
    message = str(exc).lower()
    hints: list[str] = []
    if "address already in use" in message or "errno 98" in message or "10048" in message:
        hints.append("Another program is using the port — pass `--port 9000`")
    if "permission denied" in message or "errno 13" in message:
        hints.append("Termux: run `termux-setup-storage`; Linux: check the file permissions")
    if "no space left" in message:
        hints.append("Free some disk space (uploading or zipping needs room)")
    if "connection refused" in message or "timed out" in message:
        hints.append("Check that both devices are on the same Wi-Fi")
    if "importerror" in type(exc).__name__.lower() or isinstance(exc, ImportError):
        hints.append("Install the missing module: pip install rich qrcode")
    return hints


def export_markdown(report: Report) -> str:
    lines = [f"# {APP_NAME} doctor report", ""]
    for check in report.checks:
        lines.append(
            f"- {check.icon} **{check.name}** — {check.detail}" + (f" _({check.fix})_" if check.fix else "")
        )
    lines.append("")
    lines.append(f"Health: {report.score()}%")
    return "\n".join(lines)


def run_doctor(*, as_json: bool = False, fast: bool = False) -> int:
    """Entry point used by ``lfs doctor``."""
    cfg = load_config()
    with console().status("running diagnostics…"):
        report = run_checks(config=cfg, skip_tools=fast, skip_network=fast)
    if as_json:
        payload = [
            {"name": c.name, "status": c.status, "detail": c.detail, "fix": c.fix, "group": c.group}
            for c in report.checks
        ]
        console().print_json(
            data={
                "checks": payload,
                "health": report.score(),
                "healthy": report.healthy,
                "blocking": len(report.failures),
                "warnings": len(report.warnings),
                "blocking_checks": [c.name for c in report.failures],
                "fixes": [c.fix for c in report.checks if c.fix],
            }
        )
    else:
        console().print(render_report(report))
        console().print(
            info_panel(
                [("docs", "https://github.com/Erawat4l/local-file-sharer#troubleshooting")],
                title="need help?",
            )
        )
    return 0 if report.healthy else 1


_ = (Callable, Iterable, Config)
