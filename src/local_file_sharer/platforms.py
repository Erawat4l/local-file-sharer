"""Cross-platform compatibility layer.

Everything OS-specific lives here so the rest of the package can stay pure
Python and behave identically on Termux/Android, Linux, macOS and Windows.

Highlights
----------
* :func:`sendfile` — ``os.sendfile`` with a ``socket.sendfile`` fallback on
  Windows and a portable chunked-copy fallback everywhere else.
* :func:`list_drives` — ``C:\\``, ``D:\\`` … on Windows, mounts on POSIX.
* :func:`find_tool` — resolves ``tailscale``, ``cloudflared``, ``aria2c``,
  ``ffmpeg`` … across PATH, Windows ``.exe``/``.cmd`` shims and Termux
  directories.
* :func:`copy_to_clipboard` — pbcopy / wl-copy / xclip / clip.exe / termux.
* :func:`open_in_browser` — xdg-open / open / ``os.startfile`` / termux-open.
"""

from __future__ import annotations

import errno
import ipaddress
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")


def _is_termux() -> bool:
    if not LINUX:
        return False
    if os.environ.get("TERMUX_VERSION"):
        return True
    prefix = os.environ.get("PREFIX", "")
    return "com.termux" in prefix or Path("/data/data/com.termux/files/usr").exists()


TERMUX = _is_termux()
ANDROID = TERMUX or (LINUX and "ANDROID_ROOT" in os.environ)

#: Seconds to wait for short-lived helper commands (tailscale, cloudflared …).
QUICK_TIMEOUT = 4.0


# ─────────────────────────────────────────────────────────────────────────────
# Filesystem
# ─────────────────────────────────────────────────────────────────────────────
def home_dir() -> Path:
    try:
        return Path.home()
    except RuntimeError:  # pragma: no cover - exotic environments
        return Path(os.environ.get("USERPROFILE") or os.getcwd())


def downloads_dir() -> Path:
    """Where shared downloads land, and where Download mode starts.

    On Android this must be ``/sdcard/Download`` — the folder the system Files
    app shows. The shell's own home (/data/data/com.termux/files/home) is
    invisible to every other app on the phone, so a file saved there looks lost
    even though it exists. The old candidate list only contained Termux-home
    paths, so Android never matched.
    """
    candidates: list[Path] = []
    shared = sdcard_dir()
    if shared is not None:
        # Android spells it "Download" (singular); accept both spellings.
        candidates += [shared / "Download", shared / "Downloads"]
    candidates += [
        home_dir() / "Download",
        home_dir() / "Downloads",
        home_dir() / "storage" / "downloads",  # Termux default symlink
    ]
    for candidate in candidates:
        if _readable(candidate):
            return candidate
    shared = sdcard_dir()
    return shared if shared is not None else home_dir()


def _readable(path: Path) -> bool:
    """Can the picker actually list *path*? ``is_dir()`` is not enough."""
    try:
        with os.scandir(path):
            return True
    except OSError:
        return False


#: `/sdcard` is what every Android file manager calls internal storage, and
#: what people type. It is a symlink to /storage/emulated/0 on stock Android but
#: is not guaranteed to exist (some builds hide it, and it is missing entirely
#: off Android), so it is tried as an alias, never assumed.
SDCARD_ALIASES = (Path("/sdcard"), Path("/storage/emulated/0"))


def sdcard_dir() -> Path | None:
    """Internal shared storage, if this device exposes it. ``None`` off Android."""
    for candidate in (*SDCARD_ALIASES, home_dir() / "storage"):
        if _readable(candidate):
            return candidate
    return None


def start_dir(remembered: Path | None = None) -> Path:
    """Where the picker should open.

    A remembered folder is only useful if it can be listed. ``is_dir()`` says
    ``/`` and ``/data/data/com.termux`` are folders, and they are — they just
    cannot be read, so trusting the memory opened the picker on a locked
    directory and printed ``Permission denied: /`` instead of the user's
    storage. Anything unreadable is skipped in favour of the real root.
    """
    candidates, seen = [], set()
    # storage_root() already returns home when there is no Android storage, so
    # it needs no TERMUX gate here — and gating it meant a device that exposes
    # /sdcard without Termux's env vars was ignored.
    for candidate in (remembered, storage_root(), home_dir()):
        if candidate is None:
            continue
        try:
            resolved = Path(candidate)
        except (TypeError, ValueError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if _readable(resolved):
            candidates.append(resolved)
    return candidates[0] if candidates else home_dir()


def storage_root() -> Path:
    """Best starting folder for the picker.

    On Termux ``/storage/emulated/0`` exists as a directory long before
    ``termux-setup-storage`` has been run — it just cannot be listed, so the
    picker opened on a folder that raised ``Permission denied: /`` the moment
    it tried to read it. Probe by listing instead of by ``is_dir()``.
    """
    # Not gated on TERMUX: sdcard_dir() only reports paths it can actually
    # list, so a plain Linux box with no /sdcard simply falls through.
    shared = sdcard_dir()
    if shared is not None:
        return shared
    if WINDOWS:
        drive = os.environ.get("SYSTEMDRIVE", "C:")
        profile = home_dir()
        if profile.is_dir():
            return profile
        candidate = Path(f"{drive}\\")
        if candidate.is_dir():
            return candidate
    return home_dir()


def is_hidden(path: Path) -> bool:
    name = path.name
    if name.startswith(".") and name not in (".", ".."):
        return True
    if WINDOWS:
        try:
            attrs = path.stat().st_file_attributes  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return False
        return bool(attrs & stat.FILE_ATTRIBUTE_HIDDEN)
    return False


def normalize_path(raw: str | os.PathLike[str]) -> Path:
    """Expand ``~``, env vars and Windows drive aliases into a real Path."""
    text = os.path.expandvars(os.fspath(raw))
    text = os.path.expanduser(text)
    if WINDOWS:
        text = text.replace("/", "\\")
        # ``C:file`` means "file relative to CWD on C:" — make it absolute.
        if re.fullmatch(r"[A-Za-z]:", text):
            text += "\\"
    return Path(text)


@dataclass(frozen=True)
class Drive:
    path: Path
    label: str
    detail: str = ""

    @property
    def icon(self) -> str:
        return "🖴"


#: Where removable media usually appears on Linux/Android/macOS.
POSIX_MOUNT_ROOTS: tuple[str, ...] = ("/storage", "/mnt", "/media", "/run/media", "/Volumes")


def list_drives() -> list[Drive]:
    """List Windows drive letters / POSIX mount points."""
    drives: list[Drive] = []
    if WINDOWS:
        import ctypes  # noqa: PLC0415 - Windows only

        try:
            mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            mask = 0
        letters = [chr(65 + i) for i in range(26) if mask & (1 << i)]
        if not letters:
            letters = ["C"]
        for letter in letters:
            root = Path(f"{letter}:\\")
            label = f"{letter}:"
            detail = ""
            try:
                if sys.version_info >= (3, 12):
                    usage = shutil.disk_usage(root)
                    detail = f"{human_gib(usage.free)} free of {human_gib(usage.total)}"
            except OSError:
                pass
            drives.append(Drive(root, label, detail))
        return drives

    seen: set[str] = set()
    for base in POSIX_MOUNT_ROOTS:
        root = Path(base)
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_dir() or child.name.startswith("."):
                    continue
            except OSError:
                continue
            if os.path.realpath(child) in seen:
                continue
            seen.add(os.path.realpath(child))
            drives.append(Drive(child, child.name, str(child)))
    return drives


def human_gib(num_bytes: int) -> str:
    return f"{num_bytes / (1024**3):.0f} GB"


def folder_size(path: Path, *, limit_files: int = 200_000) -> tuple[int, int, int]:
    """Return ``(bytes, files, folders)`` for a directory tree.

    Never follows symlinks and swallows permission errors so it is safe to run
    on ``/`` or ``C:\\`` (use :func:`scan_tree` for a cancellable scan).
    """
    total_bytes = 0
    files = 0
    folders = 0
    for root, dirs, names in os.walk(path, onerror=lambda _e: None):
        dirs[:] = [d for d in dirs if d not in ("$RECYCLE.BIN", "System Volume Information")]
        folders += len(dirs)
        for name in names:
            files += 1
            try:
                total_bytes += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
            if files >= limit_files:
                return total_bytes, files, folders
    return total_bytes, files, folders


def is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def safe_filename(name: str, *, fallback: str = "file") -> str:
    """Strip path separators and reserved characters from a single name."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if WINDOWS:
        reserved = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if cleaned.upper().split(".")[0] in reserved:
            cleaned = f"_{cleaned}"
    return cleaned or fallback


# ─────────────────────────────────────────────────────────────────────────────
# Sockets / high-performance transfer
# ─────────────────────────────────────────────────────────────────────────────
SOCKET_BUFFER = 4 * 1024 * 1024  # 4 MiB — big enough for 50 MB/s+ on Wi-Fi
CHUNK = 512 * 1024  # fallback copy chunk
HAS_SENDFILE = hasattr(os, "sendfile") and not WINDOWS


def tune_socket(sock: socket.socket) -> None:
    """Ask for large kernel buffers; silently ignore unsupported platforms."""
    for level, name, val in (
        (socket.SOL_SOCKET, "SO_SNDBUF", SOCKET_BUFFER),
        (socket.SOL_SOCKET, "SO_RCVBUF", SOCKET_BUFFER),
    ):
        if not hasattr(socket, name):
            continue
        try:
            sock.setsockopt(level, getattr(socket, name), val)
        except OSError:
            continue
    if hasattr(socket, "TCP_NODELAY"):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
    if LINUX and hasattr(socket, "TCP_WINDOW_CLAMP"):
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_WINDOW_CLAMP, SOCKET_BUFFER)
        except OSError:
            pass


def _write_all(out, data: bytes) -> None:
    """Send *data* to a socket or a buffered writer (socketserver wraps one)."""
    sendall = getattr(out, "sendall", None)
    if sendall is not None:
        sendall(data)
        return
    write = getattr(out, "write", None)
    if write is not None:
        write(data)
        return
    raise TypeError("sendfile() needs a socket or a writable file object")


def sendfile(
    out_sock,
    file_obj,
    offset: int,
    count: int,
    *,
    progress=None,
) -> int:
    """Send *count* bytes from *file_obj* starting at *offset*.

    * Linux/Android/Termux: ``os.sendfile`` (zero-copy, kernel-side).
    * Windows: ``socket.sendfile`` (which loops internally).
    * Anything else: a 512 KiB chunked copy.

    ``out_sock`` may be a real :class:`socket.socket` or the buffered writer
    that :mod:`http.server` exposes (``handler.wfile``).
    ``progress(delta)`` is called after each step so callers can update a
    dashboard without slowing the hot loop down.
    """
    sent_total = 0
    remaining = count
    fd = None
    if HAS_SENDFILE:
        try:
            fd = file_obj.fileno()
            sock_fd = out_sock.fileno()
        except (AttributeError, OSError, ValueError):
            fd = None
        if fd is not None:
            setter = getattr(out_sock, "settimeout", None)
            if setter:
                try:
                    setter(None)
                except OSError:
                    pass
            while remaining > 0:
                try:
                    sent = os.sendfile(sock_fd, fd, offset, min(remaining, 8 * 1024 * 1024))
                except BlockingIOError:
                    continue
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EINTR):
                        continue
                    raise
                if sent == 0:
                    break
                offset += sent
                remaining -= sent
                sent_total += sent
                if progress:
                    progress(sent)
            return sent_total

    if hasattr(out_sock, "sendfile") and not HAS_SENDFILE:
        try:
            file_obj.seek(offset)
            setter = getattr(out_sock, "settimeout", None)
            if setter:
                try:
                    setter(None)
                except OSError:
                    pass
            while remaining > 0:
                chunk = min(remaining, 1024 * 1024)
                sent = out_sock.sendfile(file_obj, offset=None, count=chunk)
                if not sent:
                    break
                remaining -= sent
                sent_total += sent
                if progress:
                    progress(sent)
            return sent_total
        except (AttributeError, OSError):
            remaining = count - sent_total
            offset += sent_total
            try:
                file_obj.seek(offset)
            except OSError:
                pass

    file_obj.seek(offset)
    while remaining > 0:
        chunk = file_obj.read(min(CHUNK, remaining))
        if not chunk:
            break
        _write_all(out_sock, chunk)
        remaining -= len(chunk)
        sent_total += len(chunk)
        if progress:
            progress(len(chunk))
    return sent_total


def readfile(in_sock: socket.socket, file_obj, count: int, *, progress=None) -> int:
    """Receive *count* bytes from *in_sock* into *file_obj* (lenient)."""
    remaining = count
    received = 0
    while remaining > 0:
        data = in_sock.recv(min(CHUNK, remaining))
        if not data:
            break
        file_obj.write(data)
        remaining -= len(data)
        received += len(data)
        if progress:
            progress(len(data))
    return received


# ─────────────────────────────────────────────────────────────────────────────
# Networking helpers
# ─────────────────────────────────────────────────────────────────────────────
_VIRTUAL_IFACE_RE = re.compile(
    r"^(lo|docker|br-|veth|virbr|vmnet|vboxnet|zt|tailscale|tun|tap|utun|awdl|llw|"
    r"anpi|gif|stf|bridge|p2p|dummy|ham)",
    re.IGNORECASE,
)
_WIFI_IFACE_RE = re.compile(r"^(wl|wlan|wifi|en0|ap|swlan)", re.IGNORECASE)
_ETH_IFACE_RE = re.compile(r"^(eth|en|eno|ens|enp|em|rndis|usb)", re.IGNORECASE)


@dataclass(frozen=True)
class LocalAddress:
    ip: str
    interface: str = ""
    label: str = "LAN"

    @property
    def is_wifi(self) -> bool:
        return bool(_WIFI_IFACE_RE.match(self.interface))

    @property
    def is_private(self) -> bool:
        return is_private_ip(self.ip)

    @property
    def icon(self) -> str:
        return "📶" if self.is_wifi else "🔗"


def is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6:
        return addr.is_private or addr.is_link_local
    return addr.is_private or addr.is_link_local or addr.is_loopback


def iter_local_ipv4() -> Iterator[LocalAddress]:
    """Yield every usable IPv4 address of this machine, Wi-Fi first.

    Combines the ``SO_DNSTOOL``-style UDP trick (works without extra
    dependencies on Windows and Termux) with ``psutil`` when it is available.
    """
    found: dict[str, LocalAddress] = {}

    # 1) netifaces/psutil-free primary address via a dummy UDP connect.
    for target in ("10.255.255.255", "192.168.255.255", "8.8.8.8"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.2)
            sock.connect((target, 1))
            ip = sock.getsockname()[0]
        except OSError:
            ip = ""
        finally:
            sock.close()
        if ip and not ip.startswith("127.") and ip not in found:
            found[ip] = LocalAddress(ip, "", "LAN")

    # 2) Enumerate interfaces where the stdlib lets us.
    try:
        import psutil  # type: ignore[import-not-found]  # noqa: PLC0415

        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        candidates: list[LocalAddress] = []
        for iface, entries in addrs.items():
            if _VIRTUAL_IFACE_RE.match(iface):
                continue
            try:
                if not stats.get(iface) or not stats[iface].isup:
                    continue
            except (KeyError, AttributeError):
                pass
            for entry in entries:
                if getattr(entry, "family", None) != socket.AF_INET:
                    continue
                ip = entry.address
                if ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                candidates.append(LocalAddress(ip, iface))
        for addr in candidates:
            found.setdefault(addr.ip, addr)
    except Exception:  # noqa: BLE001 - psutil is optional and can misbehave
        pass

    # 3) ``ip addr`` / ``ifconfig`` parsing as a last resort (Linux/Termux).
    if LINUX and len(found) < 2:
        for iface, ip in _parse_ip_addr():
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            found.setdefault(ip, LocalAddress(ip, iface))

    def sort_key(addr: LocalAddress):
        return (
            0 if addr.is_wifi else 1,
            0 if _ETH_IFACE_RE.match(addr.interface) else 1,
            0 if addr.is_private else 1,
            addr.ip,
        )

    yield from sorted(found.values(), key=sort_key)


def _parse_ip_addr() -> Iterator[tuple[str, str]]:
    exe = shutil.which("ip")
    if not exe:
        return
    try:
        out = subprocess.run(  # noqa: S603
            [exe, "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=QUICK_TIMEOUT,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet":
            yield parts[1], parts[3].split("/")[0]


def primary_local_ip() -> str | None:
    for addr in iter_local_ipv4():
        if addr.is_private:
            return addr.ip
    for addr in iter_local_ipv4():
        return addr.ip
    return None


def hostname() -> str:
    try:
        return socket.gethostname() or "this-device"
    except OSError:  # pragma: no cover
        return "this-device"


def port_is_free(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_free_port(start: int = 8080, *, host: str = "0.0.0.0", limit: int = 200) -> int:
    for port in range(start, start + limit):
        if port_is_free(port, host):
            return port
    raise OSError(f"no free port in range {start}-{start + limit}")


# ─────────────────────────────────────────────────────────────────────────────
# External tools
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ToolInfo:
    name: str
    path: str | None
    version: str = ""
    install_hint: str = ""

    @property
    def available(self) -> bool:
        return self.path is not None

    @property
    def display(self) -> str:
        return self.path or "not found"


TOOL_INSTALL_HINTS: dict[str, dict[str, str]] = {
    "tailscale": {
        "termux": "pkg install tailscale   (or the Tailscale Android app)",
        "linux": "curl -fsSL https://tailscale.com/install.sh | sh",
        "windows": "winget install tailscale.tailscale",
    },
    "cloudflared": {
        "termux": "pkg install cloudflared   (or download the arm64 binary)",
        "linux": "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
        "windows": "winget install Cloudflare.cloudflared",
    },
    "aria2c": {
        "termux": "pkg install aria2",
        "linux": "sudo apt install aria2",
        "windows": "winget install aria2.aria2",
        "macos": "brew install aria2",
    },
    "zip": {
        "termux": "pkg install zip unzip",
        "linux": "sudo apt install zip unzip",
        "windows": "built in (Compress-Archive) or run `lfs doctor`",
    },
    "curl": {
        "termux": "pkg install curl",
        "linux": "sudo apt install curl",
        "windows": "Windows 10+ ships curl.exe",
    },
    "wget": {
        "termux": "pkg install wget",
        "linux": "sudo apt install wget",
        "windows": "winget install GNU.Wget2",
    },
    "ffmpeg": {
        "termux": "pkg install ffmpeg",
        "linux": "sudo apt install ffmpeg",
        "windows": "winget install Gyan.FFmpeg",
    },
    "fish": {
        "termux": "pkg install fish",
        "linux": "sudo apt install fish",
        "windows": "winget install fish-shell.fish",
    },
}


def platform_key() -> str:
    if TERMUX:
        return "termux"
    if WINDOWS:
        return "windows"
    if MACOS:
        return "macos"
    return "linux"


def install_hint_for(tool: str) -> str:
    hints = TOOL_INSTALL_HINTS.get(tool, {})
    return hints.get(platform_key()) or hints.get("linux") or f"install {tool} and add it to PATH"


_EXTRA_TOOL_DIRS: tuple[str, ...] = (
    "/data/data/com.termux/files/usr/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/usr/bin",
    "/bin",
    r"C:\Program Files\Tailscale",
    r"C:\Program Files (x86)\Tailscale",
    r"C:\Program Files (x86)\cloudflared",
    r"C:\Program Files\aria2",
    r"C:\ProgramData\chocolatey\bin",
)


def find_tool(name: str, *extra_names: str) -> str | None:
    """Locate an executable across PATH and well-known install folders."""
    names: Iterable[str] = (name, *extra_names)
    suffixes = ("", ".exe", ".cmd", ".bat", ".ps1") if WINDOWS else ("",)
    for candidate in names:
        for suffix in suffixes:
            found = shutil.which(candidate + suffix)
            if found:
                return found
    for directory in _EXTRA_TOOL_DIRS:
        base = Path(directory)
        if not base.is_dir():
            continue
        for candidate in names:
            for suffix in suffixes:
                path = base / (candidate + suffix)
                try:
                    if path.is_file() and (WINDOWS or os.access(path, os.X_OK)):
                        return str(path)
                except OSError:
                    continue
    # Windows Store Python sometimes cannot see .exe via which(); probe PATHEXT.
    if WINDOWS:
        pathext = os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";")
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            for ext in pathext:
                path = Path(directory) / (name + ext.lower())
                if path.is_file():
                    return str(path)
    return None


def tool_version(path: str, *args: str) -> str:
    try:
        proc = subprocess.run(  # noqa: S603
            [path, *args],
            capture_output=True,
            text=True,
            timeout=QUICK_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    return text[0][:120] if text else ""


def probe_tool(name: str, version_args: tuple[str, ...] = ("--version",)) -> ToolInfo:
    path = find_tool(name)
    if not path:
        return ToolInfo(name, None, "", install_hint_for(name))
    return ToolInfo(name, path, tool_version(path, *version_args), install_hint_for(name))


def run_quiet(cmd: list[str], *, timeout: float = QUICK_TIMEOUT) -> tuple[int, str, str]:
    """Run a command, never raise, return ``(code, stdout, stderr)``."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def spawn(cmd: list[str], *, cwd: str | None = None) -> subprocess.Popen:
    """Start a long-lived background process (cloudflared, taildrop …)."""
    creationflags = 0
    if WINDOWS:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(  # noqa: S603
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        creationflags=creationflags,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Desktop integration
# ─────────────────────────────────────────────────────────────────────────────
_CLIPBOARD_CHAINS: list[list[str]] = (
    [["pbcopy"], ["pbclip"]],
    [["wl-copy"]],
    [["xclip", "-selection", "clipboard"]],
    [["xsel", "--clipboard", "--input"]],
    [["termux-clipboard-set"]],
    [["clip.exe"]],
    [["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"]],
)


def copy_to_clipboard(text: str) -> bool:
    """Copy *text* with the first available clipboard tool. Never raises."""
    if not text:
        return False
    for chain in _CLIPBOARD_CHAINS:
        exe = shutil.which(chain[0][0])
        if not exe:
            continue
        cmd = [exe, *chain[0][1:]]
        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                input=text,
                text=True,
                timeout=QUICK_TIMEOUT,
                check=False,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return True
    return False


def clipboard_tool_name() -> str:
    for chain in _CLIPBOARD_CHAINS:
        if shutil.which(chain[0][0]):
            return Path(chain[0][0]).name
    return ""


def open_in_browser(url: str) -> bool:
    try:
        if WINDOWS:
            os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606
            return True
        if TERMUX:
            code, _, _ = run_quiet(["termux-open-url", url], timeout=3)
            if code == 0:
                return True
        opener = shutil.which("xdg-open") or shutil.which("open")
        if opener:
            subprocess.Popen(  # noqa: S603
                [opener, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return True
    except (OSError, AttributeError, subprocess.SubprocessError):
        return False
    return False


def open_folder(path: Path) -> bool:
    try:
        if WINDOWS:
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
            return True
        opener = shutil.which("xdg-open") or shutil.which("open")
        if opener:
            subprocess.Popen(  # noqa: S603
                [opener, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return True
    except (OSError, AttributeError, subprocess.SubprocessError):
        return False
    return False


def config_dir() -> Path:
    if WINDOWS:
        base = os.environ.get("APPDATA")
        root = Path(base) if base else home_dir() / "AppData" / "Roaming"
        return root / "local-file-sharer"
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else home_dir() / ".config"
    return root / "local-file-sharer"


def cache_dir() -> Path:
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else home_dir() / "AppData" / "Local"
        return root / "local-file-sharer"
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else home_dir() / ".cache"
    return root / "local-file-sharer"


def state_path() -> Path:
    return cache_dir() / "state.json"


def ensure_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def is_frozen() -> bool:
    """True when running from a PyInstaller standalone build."""
    return bool(getattr(sys, "frozen", False))
