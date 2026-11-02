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
    con.print(f"[lfs.muted]running:[/] {' '.join(cmd)}")
    try:
        completed = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        con.print("[lfs.danger]could not run pip — install it, or re-run install.sh[/]")
        return 1
    if completed.returncode != 0:
        if kind != "pipx":
            # Debian/Ubuntu block --user installs without this (PEP 668)
            retry = [*cmd, "--break-system-packages"]
            con.print("[lfs.warning]retrying with --break-system-packages[/]")
            try:
                completed = subprocess.run(retry, check=False)
            except FileNotFoundError:
                return 1
        if completed.returncode != 0:
            con.print(f"[lfs.danger]upgrade failed[/]  see {REPOSITORY_PAGE} for manual steps")
            con.print(
                f"[lfs.muted]try a branch explicitly:[/] lfs upgrade --ref {DEFAULT_BRANCH}"
            )
            return completed.returncode
    con.print("[lfs.success]updated[/]  restart `lfs` to use the new code")
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
    con.print(f"[lfs.muted]running:[/] {' '.join(cmd)}")
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
        code = subprocess.call(cmd, env=env)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(
            f"could not run {cmd[0]}: {exc}",
            hints=[f"Update manually: {' '.join(cmd)}", release.url],
        ) from exc
    if code == 0:
        con.print("[lfs.success]✔ updated — restart `lfs` to use the new version[/]")
        return 0
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
