"""Copy-paste one-liners for every client we support.

Generates commands for curl, wget, aria2c (16 connections by default),
PowerShell (both ``Invoke-WebRequest`` and ``Start-BitsTransfer``) and the
universal browser URL. All commands target the *direct LAN* URL first, which is
what makes them fast; public/Tailscale/Cloudflare variants are produced on
demand with :func:`one_liners_for`.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from . import platforms

SUPPORTED = ("curl", "wget", "aria2c", "powershell", "bits", "browser")


def _q(url: str, *, windows: bool = False) -> str:
    """Quote a URL for a shell (single quotes POSIX, double quotes Windows)."""
    if windows:
        return f'"{url}"'
    return shlex.quote(url)


def _name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1] or "download"


@dataclass
class OneLiner:
    key: str
    label: str
    command: str
    note: str = ""

    def render(self) -> str:
        return f"{self.command}"


def curl(url: str, *, resume: bool = True) -> str:
    flags = "-L --fail --progress-bar"
    if resume:
        flags += " -C -"
    return f"curl {flags} -O {_q(url)}"


def wget(url: str, *, resume: bool = True) -> str:
    flags = "-c --show-progress" if resume else ""
    return f"wget {flags} {_q(url)}".replace("  ", " ")


def aria2c(url: str, *, connections: int = 16) -> str:
    return f"aria2c -x{connections} -s{connections} -k1M --file-allocation=none {_q(url)}"


def powershell(url: str) -> str:
    return (
        "Invoke-WebRequest -Uri "
        f"{_q(url, windows=True)} -OutFile (Split-Path {_q(url, windows=True)} -Leaf) "
        "-UseBasicParsing"
    )


def bits(url: str) -> str:
    return (
        "Start-BitsTransfer -Source "
        f"{_q(url, windows=True)} -Destination $env:USERPROFILE\\Downloads\\{_name(url)} "
        "-Priority Foreground"
    )


def aria2_multi(urls: list[str], *, connections: int = 16) -> str:
    """aria2c input-file form for many files in one command."""
    lines = "\n".join(urls)
    return f"# save as files.txt, then:\naria2c -x{connections} -s{connections} -i files.txt\n\n{lines}"


def zip_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/download/all.zip"


def build_all(
    base_url: str,
    *,
    connections: int = 16,
    target: str = "",
    enabled: tuple[str, ...] = SUPPORTED,
    windows: bool | None = None,
) -> list[OneLiner]:
    """Every supported one-liner for *base_url* (a file, folder-zip or share)."""
    url = target or zip_url(base_url)
    is_windows = platforms.WINDOWS if windows is None else windows
    out: list[OneLiner] = []
    if "curl" in enabled:
        out.append(OneLiner("curl", "curl (resumable)", curl(url)))
    if "wget" in enabled:
        out.append(OneLiner("wget", "wget (resumable)", wget(url)))
    if "aria2c" in enabled:
        out.append(
            OneLiner("aria2c", f"aria2c · {connections} connections", aria2c(url, connections=connections))
        )
    if "powershell" in enabled and (is_windows or windows is None):
        out.append(OneLiner("powershell", "PowerShell", powershell(url)))
    if "bits" in enabled and (is_windows or windows is None):
        out.append(OneLiner("bits", "BITS (background, resumable)", bits(url)))
    if "browser" in enabled:
        out.append(OneLiner("browser", "Browser", url))
    return out


def one_liners_for(
    base_url: str,
    *,
    target: str = "",
    connections: int = 16,
    kinds: tuple[str, ...] = ("curl", "wget", "aria2c", "powershell"),
) -> dict[str, str]:
    return {
        item.key: item.command
        for item in build_all(base_url, connections=connections, target=target, enabled=kinds)
    }


def file_url(base_url: str, file_id: str) -> str:
    return base_url.rstrip("/") + f"/f/{file_id}"


def parallel_hint(connections: int = 16) -> str:
    return (
        f"Tip: aria2c -x{connections} -s{connections} splits the file into {connections} "
        "byte ranges — the server answers each with os.sendfile, so the CPU stays idle "
        "and the Wi-Fi link saturates."
    )


def clipboard_payload(links: dict[str, str], header: str = "local-file-sharer") -> str:
    """A tidy multi-line block for pasting into a chat to your other device."""
    lines = [f"# {header}"]
    for label, url in links.items():
        lines.append(f"{label}: {url}")
    return "\n".join(lines)


def copy(text: str) -> bool:
    return platforms.copy_to_clipboard(text)


def shell_quote(path: Path | str) -> str:
    return shlex.quote(str(path))


def encode_filename(name: str) -> str:
    return quote(name, safe="")


def windows_download_hint() -> str:
    return (
        "On Windows, `curl.exe` is preinstalled on Windows 10+; "
        "aria2c gives the best throughput: winget install aria2.aria2"
    )


def termux_hint() -> str:
    return "On Termux: pkg install aria2 curl — aria2c -x16 downloads at full Wi-Fi speed."
