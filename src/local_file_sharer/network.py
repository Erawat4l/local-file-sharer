"""Network discovery and tunnels.

Provides the *network outputs* the UI shows, in priority order:

1. **Direct LAN/Wi-Fi** — the main QR code and one-liners target this.
2. **Public IP** — best effort, with a short timeout and offline tolerance.
3. **Tailscale** — auto-detected; can be added (``tailscale serve``/``funnel``)
   or skipped.
4. **Cloudflare Tunnel** — quick tunnel generation with a slow-speed warning.

Nothing here raises for a missing tool or an offline machine: the UI degrades
with hints instead, which keeps ``start doctor`` honest.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import platforms
from .errors import NetworkError, ToolMissingError
from .platforms import LocalAddress, iter_local_ipv4, run_quiet

PUBLIC_IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


# ─────────────────────────────────────────────────────────────────────────────
# Direct local addresses
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LocalEndpoint:
    ip: str
    interface: str = ""
    kind: str = "wifi"  # wifi | ethernet | other
    label: str = ""

    @property
    def icon(self) -> str:
        return {"wifi": "📶", "ethernet": "🔗"}.get(self.kind, "🌐")

    @property
    def pretty(self) -> str:
        name = self.label or self.interface or self.kind
        return f"{self.icon} {self.ip} ({name})"


def classify(addr: LocalAddress) -> str:
    if addr.is_wifi:
        return "wifi"
    if addr.interface and re.match(r"^(eth|en|eno|ens|enp|em|rndis|usb)", addr.interface, re.I):
        return "ethernet"
    return "other"


def local_endpoints(*, prefer_interface: str = "") -> list[LocalEndpoint]:
    """All usable local endpoints, best first (Wi-Fi, then Ethernet)."""
    endpoints: list[LocalEndpoint] = []
    for addr in iter_local_ipv4():
        kind = classify(addr)
        if prefer_interface and addr.interface == prefer_interface:
            kind = "wifi"
        endpoints.append(LocalEndpoint(ip=addr.ip, interface=addr.interface, kind=kind))
    order = {"wifi": 0, "ethernet": 1, "other": 2}
    endpoints.sort(key=lambda e: (order.get(e.kind, 3), not e.ip.startswith(("192.168.", "10.", "172."))))
    return endpoints


def primary_endpoint(*, prefer_interface: str = "") -> LocalEndpoint | None:
    endpoints = local_endpoints(prefer_interface=prefer_interface)
    return endpoints[0] if endpoints else None


def primary_ip(*, prefer_interface: str = "") -> str | None:
    endpoint = primary_endpoint(prefer_interface=prefer_interface)
    return endpoint.ip if endpoint else None


# ─────────────────────────────────────────────────────────────────────────────
# Public IP
# ─────────────────────────────────────────────────────────────────────────────
def public_ip(timeout: float = 2.5) -> str | None:
    """Ask a couple of tiny services for the public IPv4. Offline-safe."""
    for url in PUBLIC_IP_ENDPOINTS:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "local-file-sharer"})
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                text = response.read(64).decode("utf-8", "replace").strip()
        except (urllib.error.URLError, OSError, ssl.SSLError, ValueError):
            continue
        if _IPV4_RE.match(text):
            return text
    return None


def public_ip_async() -> threading.Thread:  # type: ignore[name-defined]  # noqa: F821
    import threading  # noqa: PLC0415

    result: dict[str, str | None] = {"ip": None}

    def _worker() -> None:
        result["ip"] = public_ip()

    thread = threading.Thread(target=_worker, name="public-ip", daemon=True)
    thread.result = result  # type: ignore[attr-defined]
    thread.start()
    return thread


def resolve_public_ip(thread, *, wait: float = 2.0) -> str | None:  # noqa: ANN001
    thread.join(timeout=wait)
    return thread.result.get("ip")  # type: ignore[attr-defined]


def reverse_dns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, socket.herror):
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Tailscale
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TailscaleStatus:
    installed: bool = False
    logged_in: bool = False
    running: bool = False
    ipv4: str = ""
    hostname: str = ""
    dns_name: str = ""
    version: str = ""
    error: str = ""
    peers: list[str] = field(default_factory=list)

    @property
    def shareable(self) -> bool:
        return bool(self.installed and self.logged_in and self.ipv4)

    @property
    def best_host(self) -> str:
        return self.dns_name or self.hostname or self.ipv4

    def summary(self) -> str:
        if not self.installed:
            return "not installed"
        if not self.logged_in:
            return "installed, not logged in"
        return f"{self.ipv4} · {self.best_host}"


def tailscale_status() -> TailscaleStatus:
    """Parse ``tailscale status --json``; never raises."""
    exe = platforms.find_tool("tailscale")
    status = TailscaleStatus(installed=bool(exe))
    if not exe:
        status.error = "tailscale not found"
        return status
    code, out, err = run_quiet([exe, "version"], timeout=2)
    if code == 0 and out:
        status.version = out.splitlines()[0].strip()
    code, out, err = run_quiet([exe, "status", "--json"], timeout=3.5)
    if code != 0 or not out.strip():
        # Windows service not running is the common case.
        message = (err or out or "").strip().splitlines()
        status.error = message[0][:160] if message else "tailscale status failed"
        status.running = "not running" not in status.error.lower()
        # Fall back to `tailscale ip -4` (works when the daemon is up).
        code2, out2, _ = run_quiet([exe, "ip", "-4"], timeout=2)
        if code2 == 0 and out2.strip():
            status.ipv4 = out2.strip().splitlines()[0]
            status.logged_in = True
        return status
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        status.error = "could not parse tailscale status"
        return status
    status.running = True
    backend = str(payload.get("BackendState", ""))
    status.logged_in = backend.lower() == "running" and bool(payload.get("Self"))
    self_info = payload.get("Self") or {}
    addresses = self_info.get("TailscaleIPs") or []
    status.ipv4 = next((a for a in addresses if _IPV4_RE.match(str(a))), "")
    status.hostname = str(self_info.get("HostName", "") or payload.get("Self", {}).get("DNSName", ""))
    dns = str(self_info.get("DNSName", "")).rstrip(".")
    status.dns_name = dns
    peers = payload.get("Peer") or {}
    for peer in peers.values():
        name = peer.get("HostName") or peer.get("DNSName") or ""
        ip = next((a for a in peer.get("TailscaleIPs") or [] if _IPV4_RE.match(str(a))), "")
        if name:
            status.peers.append(f"{name} · {ip}" if ip else name)
    if backend.lower() != "running":
        status.error = f"backend is {backend or 'stopped'}"
    return status


def tailscale_funnel_available(status: TailscaleStatus) -> bool:
    if not status.installed:
        return False
    code, out, _ = run_quiet([platforms.find_tool("tailscale") or "tailscale", "funnel", "--help"], timeout=3)
    return code == 0 and "funnel" in (out or "").lower()


def tailscale_serve(port: int, *, funnel: bool = False) -> tuple[bool, str]:
    """Expose the share on the tailnet via ``tailscale serve`` (or funnel)."""
    exe = platforms.find_tool("tailscale")
    if not exe:
        raise ToolMissingError(
            "tailscale is not installed",
            hints=[platforms.install_hint_for("tailscale"), "Or skip Tailscale in the menu"],
        )
    target = f"http://127.0.0.1:{port}"
    cmd = [exe, "funnel" if funnel else "serve", "--bg", target]
    code, out, err = run_quiet(cmd, timeout=20)
    if code != 0:
        # Older versions use a different syntax.
        alt = [exe, "funnel" if funnel else "serve", target]
        code, _out2, err2 = run_quiet(alt, timeout=20)
        if code != 0:
            raise NetworkError(
                f"tailscale {'funnel' if funnel else 'serve'} failed: {(err or err2).strip()[:200]}",
                hints=[
                    "Check `tailscale status` shows you are logged in",
                    "Run `tailscale up` once to authenticate this device",
                    "Tailscale Funnel requires HTTPS certificates enabled in the admin console",
                ],
            )
    url = ""
    for line in (out + "\n" + err).splitlines():
        match = re.search(r"https?://[\w.\-:/]+", line)
        if match:
            url = match.group(0)
            break
    if not url:
        status = tailscale_status()
        scheme = "https" if funnel else "http"
        url = f"{scheme}://{status.best_host}" if status.best_host else f"http://{port}"
    return True, url


def tailscale_disable() -> None:
    exe = platforms.find_tool("tailscale")
    if exe:
        run_quiet([exe, "serve", "reset"], timeout=4)
        run_quiet([exe, "funnel", "reset"], timeout=4)


# ─────────────────────────────────────────────────────────────────────────────
# Cloudflare Tunnel
# ─────────────────────────────────────────────────────────────────────────────
_TRYCLOUDFLARE_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


@dataclass
class CloudflareTunnel:
    url: str = ""
    process: subprocess.Popen | None = None
    started_at: float = 0.0
    error: str = ""

    @property
    def active(self) -> bool:
        return bool(self.url) and self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=4)
            except (OSError, subprocess.SubprocessError):
                try:
                    self.process.kill()
                except OSError:
                    pass
        self.process = None

    def warn(self) -> str:
        return (
            "Cloudflare quick tunnels are relayed through Cloudflare's edge — "
            "expect roughly 1–8 MB/s, far slower than direct Wi-Fi. "
            "Use it only when the devices are not on the same network."
        )


def cloudflared_available() -> bool:
    return platforms.find_tool("cloudflared") is not None


def start_cloudflare_tunnel(port: int, *, timeout: float = 25.0) -> CloudflareTunnel:
    """Start ``cloudflared tunnel --url http://127.0.0.1:<port>`` and read the URL."""
    exe = platforms.find_tool("cloudflared")
    if not exe:
        raise ToolMissingError(
            "cloudflared is not installed",
            hints=[
                platforms.install_hint_for("cloudflared"),
                "Cloudflare Tunnel is optional — direct Wi-Fi and Tailscale still work",
            ],
        )
    tunnel = CloudflareTunnel(started_at=time.time())
    try:
        process = platforms.spawn([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"])
    except OSError as exc:
        tunnel.error = str(exc)
        raise NetworkError(f"Could not start cloudflared: {exc}") from exc
    tunnel.process = process
    deadline = time.time() + timeout
    buffer: list[str] = []
    assert process.stdout is not None
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            time.sleep(0.05)
            continue
        buffer.append(line.rstrip())
        match = _TRYCLOUDFLARE_RE.search(line)
        if match:
            tunnel.url = match.group(0)
            return tunnel
    # Timed out or exited — include the tail of the log in the error.
    tail = " | ".join(buffer[-3:])[:400]
    tunnel.stop()
    tunnel.error = tail or "cloudflared did not report a URL"
    raise NetworkError(
        "Cloudflare Tunnel did not come up in time",
        hints=[
            "Check your internet connection (cloudflared needs to reach Cloudflare)",
            f"Run it manually: cloudflared tunnel --url http://127.0.0.1:{port}",
            "Skip Cloudflare and use direct Wi-Fi instead — it is much faster",
        ],
        detail=tail,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Reachability / firewall hints
# ─────────────────────────────────────────────────────────────────────────────
def can_connect(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def port_reachable_from_self(port: int, *, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    return can_connect(host, port, timeout)


def firewall_hint(port: int) -> str:
    if platforms.WINDOWS:
        return (
            "If other devices cannot connect, allow Python through Windows Defender Firewall:\n"
            f'  netsh advfirewall firewall add rule name="local-file-sharer {port}" '
            f"dir=in action=allow protocol=TCP localport={port}"
        )
    if platforms.TERMUX:
        return "If Android blocks the port, keep the Termux notification visible and disable battery optimisation for Termux."
    return (
        f"If other devices cannot connect, open the port in your firewall:\n"
        f"  sudo ufw allow {port}/tcp          # Ubuntu/Debian\n"
        f"  sudo firewall-cmd --add-port={port}/tcp   # Fedora"
    )


def tcp_score(host: str, port: int, *, timeout: float = 1.0) -> float | None:
    """Round-trip latency in ms to *host:port* — used to rank endpoints."""
    start = time.perf_counter()
    if can_connect(host, port, timeout=timeout):
        return (time.perf_counter() - start) * 1000.0
    return None


def guess_wifi_band() -> str:
    """Best-effort Wi-Fi band detection for the speed hint."""
    if platforms.TERMUX:
        code, out, _ = run_quiet(["termux-wifi-connectioninfo"], timeout=2)
        if code == 0:
            try:
                payload = json.loads(out)
                freq = int(payload.get("frequency_mhz") or payload.get("frequency") or 0)
            except (json.JSONDecodeError, TypeError, ValueError):
                freq = 0
            if freq >= 5000:
                return f"5 GHz ({freq} MHz) — expect 40–90 MB/s"
            if freq:
                return f"2.4 GHz ({freq} MHz) — expect 8–20 MB/s"
    if platforms.LINUX:
        exe = platforms.find_tool("iw")
        if exe:
            code, out, _ = run_quiet([exe, "dev"], timeout=2)
            iface = ""
            for line in out.splitlines():
                match = re.match(r"\s+Interface\s+(\S+)", line)
                if match:
                    iface = match.group(1)
                    break
            if iface:
                code, out, _ = run_quiet([exe, "dev", iface, "link"], timeout=2)
                match = re.search(r"freq:\s*(\d+)", out)
                if match:
                    freq = int(match.group(1))
                    return f"{'5 GHz' if freq >= 5000 else '2.4 GHz'} ({freq} MHz) on {iface}"
    return ""


def hostname_fqdn() -> str:
    try:
        return socket.getfqdn() or socket.gethostname()
    except OSError:
        return ""


def describe_interfaces() -> list[tuple[str, str]]:
    """``[(interface, ip), …]`` — used by doctor output."""
    seen: list[tuple[str, str]] = []
    for addr in iter_local_ipv4():
        seen.append((addr.interface or "?", addr.ip))
    return seen


def os_name() -> str:
    if platforms.TERMUX:
        return "Termux (Android)"
    if platforms.WINDOWS:
        return f"Windows {os.environ.get('OS', '')}".strip()
    if platforms.MACOS:
        return "macOS"
    try:
        pretty = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "Linux"
    for line in pretty.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return "Linux"


def summarize_endpoints(endpoints: Iterable[LocalEndpoint]) -> str:
    lines = [e.pretty for e in endpoints]
    return ", ".join(lines) if lines else "no LAN address found"
