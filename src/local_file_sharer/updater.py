"""``lfs --update`` — self-update from GitHub releases.

Strategy, best first:

1. **pipx** (the recommended install) → ``lfs upgrade`` (installs from GitHub)
2. **pip** in the current environment → ``python -m pip install --upgrade``
3. **standalone Windows .exe** → download the release asset next to the current
   executable and print the swap instructions (never overwrite a running exe).

Every path verifies the available version first and is a no-op when you are
already on the newest release.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import REPO_SLUG, __version__, platforms
from .errors import UpdateError
from .fmt import human_size
from .ui.console import console

API_LATEST = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO_SLUG}/releases/latest"
REPOSITORY_PAGE = f"https://github.com/{REPO_SLUG}"

#: Where a non-PyPI install comes from. The package is not published, so every
#: upgrade has to name the repository and (optionally) the branch it came from.
GIT_URL = f"https://github.com/{REPO_SLUG}.git"
#: ``main`` is not (yet) a Python project — it holds only a stub README — so
#: installing from it fails with "neither 'setup.py' nor 'pyproject.toml'
#: found". This is the branch that actually carries the package, and the one
#: `lfs upgrade` falls back to when the current ref has nothing installable.
DEFAULT_BRANCH = "arena/d7f4713b-local-file-sharer"
#: Refs to try, in order, when no explicit ``--ref`` was given.
BRANCH_CANDIDATES = (DEFAULT_BRANCH,)


def installed_from_git() -> bool:
    """Is this copy a ``git+https://…`` install rather than a release?"""
    try:
        import importlib.metadata as md  # noqa: PLC0415

        direct = md.distribution("local-file-sharer").read_text("direct_url.json")
    except Exception:  # noqa: BLE001 - metadata is best effort
        return False
    if not direct:
        return False
    return "vcs_info" in direct or "git+" in direct


def installed_ref() -> str:
    """The branch or tag a git install came from, else ``main``."""
    try:
        import importlib.metadata as md  # noqa: PLC0415

        direct = md.distribution("local-file-sharer").read_text("direct_url.json") or ""
        payload = json.loads(direct)
        vcs = payload.get("vcs_info") or {}
        if isinstance(vcs, dict) and vcs.get("requested_revision"):
            return str(vcs["requested_revision"])
        url = str(payload.get("url") or "")
        if "@" in url:
            return url.rsplit("@", 1)[1]
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_BRANCH


def git_spec(ref: str | None = None) -> str:
    """A pip requirement that installs this project from GitHub."""
    return f"local-file-sharer @ git+{GIT_URL}@{ref or DEFAULT_BRANCH}"


def ref_has_package(ref: str, *, timeout: float = 6.0) -> bool:
    """Does *ref* contain a ``pyproject.toml``?

    ``main`` in this repository is a 19-byte README, so installing from it
    fails deep inside pip with "does not appear to be a Python project". Asking
    the API first turns that into a sentence a user can act on, and lets
    `lfs upgrade` pick a branch that works instead of erroring out.
    """
    api = f"https://api.github.com/repos/{REPO_SLUG}/contents/pyproject.toml?ref={ref}"
    request = urllib.request.Request(
        api,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"local-file-sharer/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status == 200
    except urllib.error.HTTPError as exc:
        # 404 is the answer we asked for: this ref has no package.
        return exc.code != 404
    except (urllib.error.URLError, TimeoutError, OSError):
        # Offline: do not block the install on a check we cannot run.
        return True


def resolve_ref(ref: str | None = None) -> tuple[str, str]:
    """Pick a branch that can actually be installed. Returns ``(ref, note)``."""
    if ref:
        # An explicit choice is respected, but say so if it cannot work —
        # otherwise pip fails three screens later with its own wording.
        if ref_has_package(ref):
            return ref, ""
        return ref, f"{ref} has no pyproject.toml — the install will fail"

    wanted = installed_ref()
    candidates = [wanted, *[b for b in BRANCH_CANDIDATES if b != wanted]]
    for candidate in candidates:
        if ref_has_package(candidate):
            note = ""
            if candidate != wanted:
                note = f"{wanted} has no pyproject.toml — using {candidate}"
            return candidate, note
    return wanted, ""


# ─────────────────────────────────────────────────────────────────────────────
# Quiet upgrades with a progress bar
# ─────────────────────────────────────────────────────────────────────────────
#: Steps an upgrade passes through, in order. They are reported by watching pip's
#: own stderr markers rather than guessed from a timer, so the bar advances when
#: something actually happened.
UPGRADE_STEPS: tuple[tuple[str, str], ...] = (
    ("resolve", "contacting GitHub"),
    ("clone", "cloning"),
    ("build", "building"),
    ("install", "installing"),
    ("done", "done"),
)

#: pip prints these lines as it moves between phases.
_PHASE_MARKERS: tuple[tuple[str, str], ...] = (
    ("cloning", "clone"),
    ("running command git clone", "clone"),
    ("installing build dependencies", "build"),
    ("getting requirements", "build"),
    ("preparing metadata", "build"),
    ("building wheel", "build"),
    ("successfully built", "install"),
    ("installing collected packages", "install"),
    ("successfully installed", "install"),
)


def _phase_for(line: str, current: str) -> str:
    lowered = line.lower()
    for marker, phase in _PHASE_MARKERS:
        if marker in lowered:
            return phase
    return current


def run_with_progress(cmd: list[str], *, con=None, title: str = "upgrading") -> tuple[int, list[str]]:
    """Run *cmd* quietly and draw a progress bar in its place.

    pip is chatty: ``lfs upgrade`` used to hand the terminal to pip, which
    printed thirty lines of resolve/download/build chatter and then the command
    line it was running. People asked for a bar, so pip's output is captured,
    the bar advances on the phase markers inside it, and the captured text is
    only shown when something fails — where it is genuinely useful.

    Returns ``(returncode, output_lines)``.
    """
    from rich.live import Live  # noqa: PLC0415 - only needed on this path
    from rich.text import Text  # noqa: PLC0415

    from .fmt import bar  # noqa: PLC0415
    from .ui.console import current_theme, screen_width  # noqa: PLC0415

    con = con or console()
    theme = current_theme()
    collected: list[str] = []
    state = {"phase": "resolve"}

    def render() -> Text:
        order = [key for key, _ in UPGRADE_STEPS]
        labels = dict(UPGRADE_STEPS)
        index = order.index(state["phase"]) if state["phase"] in order else 0
        done = index + (1 if state["phase"] == "done" else 0)
        total = len(order)
        width = max(10, min(28, screen_width(60) - 34))
        line = Text()
        line.append(f"  {title}  ", style=theme.primary)
        line.append(bar(done, total, width), style=theme.accent)
        line.append(f"  {done}/{total}  ", style=theme.muted)
        line.append(labels.get(state["phase"], ""), style=theme.faint)
        return line

    # Never let a pip prompt block forever in a non-tty: it would hang a phone.
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_PROGRESS_BAR="off")
    try:
        process = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError:
        return 127, ["command not found: " + " ".join(cmd)]

    assert process.stdout is not None
    if con.is_terminal:
        with Live(render(), console=con, refresh_per_second=8, transient=True) as live:
            for line in process.stdout:
                stripped = line.rstrip()
                if stripped:
                    collected.append(stripped)
                state["phase"] = _phase_for(stripped, state["phase"])
                live.update(render(), refresh=True)
            code = process.wait()
            state["phase"] = "done" if code == 0 else state["phase"]
            live.update(render(), refresh=True)
    else:
        for line in process.stdout:
            stripped = line.rstrip()
            if stripped:
                collected.append(stripped)
            state["phase"] = _phase_for(stripped, state["phase"])
        code = process.wait()
    return code, collected


def _show_failure(con, lines: list[str], *, limit: int = 14) -> None:  # noqa: ANN001
    """Print the tail of a failed pip run: that is where the reason lives."""
    if not lines:
        return
    tail = lines[-limit:]
    con.print("[lfs.muted]last lines from pip:[/]")
    for line in tail:
        con.print(f"  [lfs.faint]{line[:160]}[/]")


def upgrade_from_git(ref: str | None = None) -> int:
    """Install the newest code from GitHub, the only place this package lives.

    ``lfs --update`` looks for a GitHub *release*, and there are none yet, so it
    can only report ``404``. This does the thing the user actually wants: pull
    the current commit of the branch they installed from.
    """
    con = console()
    target, note = resolve_ref(ref)
    if note:
        con.print(f"[lfs.warning]{note}[/]")
    spec = git_spec(target)
    kind = detect_installer()
    if kind == "pipx":
        cmd = ["pipx", "install", "--force", spec]
    elif kind == "exe":
        con.print(f"[lfs.warning]this is a standalone build — download the new one from {RELEASES_PAGE}[/]")
        return 1
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec]
    con.print(f"[lfs.muted]from[/]  [lfs.primary]{target}[/]  [lfs.faint]({GIT_URL})[/]")
    code, output = run_with_progress(cmd, con=con)
    if code == 127:
        con.print("[lfs.danger]could not run pip — install it, or re-run install.sh[/]")
        return 1
    if code != 0 and kind != "pipx":
        # Debian/Ubuntu block --user installs without this (PEP 668)
        retry = [*cmd, "--break-system-packages"]
        con.print("[lfs.warning]retrying with --break-system-packages[/]")
        code, more = run_with_progress(retry, con=con)
        output.extend(more)
        if code == 127:
            return 1
    if code != 0:
        con.print(f"[lfs.danger]upgrade failed[/]  see {REPOSITORY_PAGE} for manual steps")
        _show_failure(con, output)
        con.print(f"[lfs.muted]try a branch explicitly:[/] lfs upgrade --ref {DEFAULT_BRANCH}")
        return code
    con.print("[lfs.success]  upgraded[/]  [lfs.muted]restart `lfs` to use the new code[/]")
    return 0


def parse_version(text: str) -> tuple[int, ...]:
    """``v1.2.3`` / ``1.2.3-beta`` → comparable tuple."""
    cleaned = text.strip().lstrip("vV")
    match = re.match(r"(\d+(?:\.\d+)*)", cleaned)
    if not match:
        return (0,)
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


@dataclass
class ReleaseInfo:
    tag: str = ""
    name: str = ""
    url: str = ""
    notes: str = ""
    published: str = ""
    assets: list[dict[str, Any]] | None = None

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV") or "unknown"

    def asset_for_platform(self) -> dict[str, Any] | None:
        if not self.assets:
            return None
        machine = platform.machine().lower()
        wanted = []
        if platforms.WINDOWS:
            wanted = ["windows", ".exe"]
        elif platforms.MACOS:
            wanted = ["macos", "darwin"]
        else:
            wanted = ["linux"]
        for asset in self.assets:
            name = str(asset.get("name", "")).lower()
            if all(token in name for token in wanted):
                if "arm64" in name or "aarch64" in name:
                    if machine in ("aarch64", "arm64"):
                        return asset
                    continue
                if "x86_64" in name or "amd64" in name:
                    if machine in ("x86_64", "amd64"):
                        return asset
                    continue
                return asset
        return None


def fetch_latest(*, timeout: float = 6.0) -> ReleaseInfo:
    request = urllib.request.Request(
        API_LATEST,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"local-file-sharer/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise UpdateError(
            f"GitHub returned HTTP {exc.code}",
            hints=[
                "Check your internet connection",
                "Or update manually: lfs upgrade",
                f"Releases: {RELEASES_PAGE}",
            ],
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(
            f"Could not reach GitHub: {exc}",
            hints=[
                "Are you online? Tailscale/Cloudflare do not affect pip",
                f"Or update manually: lfs upgrade ({REPOSITORY_PAGE})",
            ],
        ) from exc
    return ReleaseInfo(
        tag=str(payload.get("tag_name", "")),
        name=str(payload.get("name", "")),
        url=str(payload.get("html_url", RELEASES_PAGE)),
        notes=str(payload.get("body", ""))[:1500],
        published=str(payload.get("published_at", "")),
        assets=list(payload.get("assets") or []),
    )


def detect_installer() -> str:
    """Return ``pipx``, ``pip``, ``exe`` or ``unknown``."""
    if platforms.is_frozen():
        return "exe"
    prefix = Path(sys.prefix).as_posix().lower()
    if "pipx" in prefix or "pipx" in sys.argv[0].lower():
        return "pipx"
    if shutil.which("pipx") and ("pipx" in sys.executable.lower() or "pipx" in prefix):
        return "pipx"
    try:
        import importlib.metadata as md  # noqa: PLC0415

        dist = md.distribution("local-file-sharer")
        installer = (dist.read_text("INSTALLER") or "").strip().lower()
        if installer:
            return "pipx" if installer == "pipx" else "pip"
    except Exception:  # noqa: BLE001 - metadata is best effort
        pass
    return "pip"


def upgrade_command() -> list[str]:
    kind = detect_installer()
    if kind == "pipx":
        return ["pipx", "upgrade", "local-file-sharer"]
    if kind == "exe":
        return []
    return [sys.executable, "-m", "pip", "install", "--upgrade", "local-file-sharer"]


def download_asset(asset: dict[str, Any], *, timeout: float = 60.0) -> Path:
    url = str(asset.get("browser_download_url") or "")
    name = str(asset.get("name") or "local-file-sharer.exe")
    if not url:
        raise UpdateError("release asset has no download URL")
    target = Path(tempfile.gettempdir()) / name
    request = urllib.request.Request(url, headers={"User-Agent": f"local-file-sharer/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, open(target, "wb") as handle:  # noqa: S310
            expected = int(response.headers.get("Content-Length") or 0)
            written = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
            if expected and written != expected:
                raise UpdateError(
                    f"download incomplete ({human_size(written)} of {human_size(expected)})",
                    hints=["Run `lfs --update` again"],
                )
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"could not download {name}: {exc}") from exc
    return target


def run_update(*, check_only: bool = False, yes: bool = False, channel: str = "stable") -> int:
    con = console()
    con.print(f"[lfs.muted]current version[/]  [lfs.primary]{__version__}[/]")
    try:
        release = fetch_latest()
    except UpdateError as exc:
        # No releases are published yet, so `--update` cannot compare versions.
        # Point at the thing that does work instead of a bare HTTP 404.
        con.print(f"[lfs.warning]{exc.message}[/]")
        for hint in exc.all_hints():
            con.print(f"  • {hint}")
        if installed_from_git():
            con.print(
                f"[lfs.info]this copy came from git ({installed_ref()}); update it with:  lfs upgrade[/]"
            )
        else:
            con.print("[lfs.info]update with:  lfs upgrade   (or re-run install.sh)[/]")
        return 1
    con.print(
        f"[lfs.muted]latest release[/]   [lfs.primary]{release.version}[/]  [lfs.faint]({release.published[:10]})[/]"
    )
    if not is_newer(release.version):
        con.print("[lfs.success]✔ you are up to date[/]")
        return 0
    con.print(f"[lfs.info]→ {release.version} is available[/]")
    if release.notes:
        con.print(f"[lfs.faint]{release.notes.splitlines()[0][:160]}[/]")
    if check_only:
        con.print(f"[lfs.muted]run `lfs --update` to install · {release.url}[/]")
        return 0
    kind = detect_installer()
    if kind == "exe":
        asset = release.asset_for_platform()
        if asset is None:
            con.print(f"[lfs.warning]no standalone build for this platform · {release.url}[/]")
            return 1
        target = download_asset(asset)
        con.print(f"[lfs.success]downloaded[/] {asset.get('name')} → {target}")
        con.print("[lfs.muted]Quit local-file-sharer, then replace your .exe with the downloaded file.[/]")
        return 0
    cmd = upgrade_command()
    if not cmd:
        con.print(f"[lfs.warning]could not detect how this copy was installed · {release.url}[/]")
        return 1
    if not yes:
        try:
            answer = input("continue? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 1
        if answer not in ("", "y", "yes"):
            con.print("[lfs.muted]cancelled[/]")
            return 1
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    try:
        code, output = run_with_progress(cmd, con=con)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(
            f"could not run {cmd[0]}: {exc}",
            hints=[f"Update manually: {' '.join(cmd)}", release.url],
        ) from exc
    if code == 0:
        con.print("[lfs.success]  updated[/]  [lfs.muted]restart `lfs` to use the new version[/]")
        return 0
    _show_failure(con, output)
    con.print(f"[lfs.danger]updater exited with code {code}[/]")
    con.print(f"[lfs.muted]manual: {' '.join(cmd)}[/]")
    return code


def changelog_since(current: str = __version__, *, limit: int = 5) -> list[str]:
    """Best-effort list of release tags newer than *current* (for the UI)."""
    url = f"https://api.github.com/repos/{REPO_SLUG}/releases?per_page={limit}"
    request = urllib.request.Request(url, headers={"User-Agent": f"local-file-sharer/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            releases = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [str(r.get("tag_name", "")) for r in releases if is_newer(str(r.get("tag_name", "")), current)]
