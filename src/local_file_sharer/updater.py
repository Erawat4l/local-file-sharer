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
import threading
import time
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
#: The release branch, and the ref `lfs upgrade` falls back to when the current
#: one has nothing installable.
DEFAULT_BRANCH = "main"
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

    A branch pushed without the package makes pip fail deep inside with "does
    not appear to be a Python project". Asking the API first turns that into a
    sentence a user can act on, and lets `lfs upgrade` fall back to
    ``DEFAULT_BRANCH`` instead of erroring out.
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


def installed_commit() -> str:
    """The git commit pip actually installed, or ``""`` when unknown.

    ``direct_url.json`` records ``commit_id`` for a VCS install, which is the
    only way to tell one commit from another: this package reports the same
    version number from every commit, so a version check cannot.
    """
    try:
        import importlib.metadata as md  # noqa: PLC0415

        direct = md.distribution("local-file-sharer").read_text("direct_url.json") or ""
        payload = json.loads(direct)
        vcs = payload.get("vcs_info") or {}
        if isinstance(vcs, dict):
            return str(vcs.get("commit_id") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def remote_commit(ref: str, *, timeout: float = 6.0) -> str:
    """The commit a branch points at on GitHub, or ``""`` when offline."""
    url = f"https://api.github.com/repos/{REPO_SLUG}/commits/{ref}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"local-file-sharer/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8", "replace"))
        return str(payload.get("sha") or "")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return ""


def installed_stamp() -> tuple[str, str, str]:
    """``(commit, build, version)`` for the copy that is installed.

    The commit is the honest identity when it exists, but it only exists for a
    ``pip install git+...``. A pipx git install, an editable checkout and an
    unpacked ``install-lfs.py`` tree all leave it blank, which is exactly the
    case where an update silently does nothing and is then reported as a
    success. The build stamp changes on every UI change, so it is the fallback
    that still moves.
    """
    commit = installed_commit()
    build = ""
    try:
        import importlib.metadata as md  # noqa: PLC0415

        build = md.version("local-file-sharer")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import BUILD_STAMP  # noqa: PLC0415

        build = f"{build}+{BUILD_STAMP}" if build else BUILD_STAMP
    except Exception:  # noqa: BLE001
        pass
    return commit, build, __version__


def upgrade_result(before: str, ref: str) -> tuple[bool, str, str]:
    """Did the install really change? Returns ``(changed, was, now)``.

    pip can exit zero without installing anything (it did, for every upgrade
    after the first, because the version never changes). Comparing the recorded
    commit before and after is the only honest check -- but "no commit" is not
    evidence of success, which is what this used to assume. When there is no
    commit to compare, the build stamp decides; when neither is available the
    answer is "cannot tell", and the caller says so instead of claiming an
    upgrade happened.
    """
    now = installed_commit()
    if now:
        return (now != before), before, now
    # No commit: fall back to the build stamp, which changes whenever the
    # shipped UI changes even though the version number does not.
    _commit, build, _version = installed_stamp()
    if before and build:
        return (build != before), before, build
    return False, before, build


def is_throwaway_ref(ref: str) -> bool:
    """A ref that belongs to one session and will never move again.

    Installs made from a feature branch pin that branch forever: `lfs upgrade`
    realises the copy came from ``arena/abc123`` and reinstalls *that*, so the
    device is frozen on whatever that branch held the day it was abandoned.
    Every later fix is invisible, and the update keeps reporting success. That
    is not a branch a user chose; it is a leftover of how they installed once.
    """
    lowered = (ref or "").strip().lower()
    if not lowered:
        return False
    if lowered in ("main", "master", "trunk", DEFAULT_BRANCH):
        return False
    return lowered.startswith(("arena/", "feature/", "fix/", "tmp/", "temp/", "wip/"))


def resolve_ref(ref: str | None = None) -> tuple[str, str]:
    """Pick a branch that can actually be installed. Returns ``(ref, note)``."""
    if ref:
        # An explicit choice is respected, but say so if it cannot work —
        # otherwise pip fails three screens later with its own wording.
        if ref_has_package(ref):
            return ref, ""
        return ref, f"{ref} has no pyproject.toml — the install will fail"

    wanted = installed_ref()
    # An install from a throwaway branch must not pin itself to it for ever.
    if is_throwaway_ref(wanted):
        if ref_has_package(DEFAULT_BRANCH):
            return DEFAULT_BRANCH, (
                f"this copy came from {wanted}, which is a finished branch — "
                f"following {DEFAULT_BRANCH} from now on"
            )
        return DEFAULT_BRANCH, ""

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
    # Any output at all means the first stage is over, even if pip's wording
    # changes. A 0/5 bar that never moves is indistinguishable from a hang, and
    # that is exactly what a user reported while git was slowly cloning.
    if lowered.strip() and current == "resolve":
        return "clone"
    return current


#: How long pip may go completely silent before the update is abandoned.
#: Generous on purpose: a phone starting a clone can be quiet for a while, but
#: four minutes of nothing is a dead connection, not a slow one.
STALL_TIMEOUT = 240.0


def run_with_progress(
    cmd: list[str], *, con=None, title: str = "upgrading", stall_timeout: float | None = None
) -> tuple[int, list[str]]:
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
    begun = time.monotonic()
    stall_timeout = STALL_TIMEOUT if stall_timeout is None else stall_timeout

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
        # A frozen 0/5 gives no way to tell a slow network from a hung clone.
        # The elapsed seconds make the difference obvious and prove the app is
        # alive, which is the actual complaint: "stuck".
        elapsed = max(0, int(time.monotonic() - begun))
        if elapsed >= 3 and state["phase"] != "done":
            line.append(f"  {elapsed}s", style=theme.faint)
        return line

    # A prompt must never wait forever: `lfs upgrade` on a phone looked frozen
    # at "0/5 contacting GitHub" because pip handed the terminal to git, git
    # asked for a username and password, and nothing was reading stdin to
    # answer. stdin is closed (prompts read EOF and fail fast) and both pip and
    # git are told not to ask at all.
    #
    # PYTHONPATH is stripped because shipping the source tree to pip can make it
    # resolve the old copy that is being replaced.
    env = dict(
        os.environ,
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PIP_PROGRESS_BAR="off",
        PIP_NO_INPUT="1",
        GIT_TERMINAL_PROMPT="0",
    )
    env.pop("PYTHONPATH", None)
    # git shells out to ssh/askpass helpers that ignore GIT_TERMINAL_PROMPT.
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""
    try:
        process = subprocess.Popen(  # noqa: S603
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError:
        return 127, ["command not found: " + " ".join(cmd)]

    assert process.stdout is not None

    # pip's output is read on a worker thread rather than inline. Iterating
    # stdout blocks, so a clone that stops sending bytes left the bar at
    # "0/5 contacting GitHub" with no way out: no deadline, no timeout, no
    # message, and Ctrl+C the only exit. Reading in the background lets the
    # loop below notice that nothing has arrived and give up with a reason.
    def pump() -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            stripped = raw.rstrip()
            if stripped:
                collected.append(stripped)
            state["phase"] = _phase_for(stripped, state["phase"])
            state["last"] = time.monotonic()

    state["last"] = time.monotonic()
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    def wait_or_kill(live=None) -> tuple[bool, bool]:
        """Poll to completion. Returns ``(stalled, cancelled)``."""
        while True:
            if process.poll() is not None:
                return False, False
            quiet = time.monotonic() - float(state.get("last", begun))
            if quiet > stall_timeout:
                return True, False
            if live is not None:
                live.update(render(), refresh=True)
            time.sleep(0.2)

    stalled = False
    if con.is_terminal:
        with Live(render(), console=con, refresh_per_second=8, transient=True) as live:
            try:
                stalled, _ = wait_or_kill(live)
            except KeyboardInterrupt:
                process.kill()
                process.wait()
                live.stop()
                raise
            if stalled:
                process.kill()
                process.wait()
                collected.append(
                    f"no output for {int(stall_timeout)}s — the download stopped answering"
                )
            code = process.poll()
            if code is None:
                code = process.wait()
            state["phase"] = "done" if code == 0 and not stalled else state["phase"]
            live.update(render(), refresh=True)
    else:
        try:
            stalled, _ = wait_or_kill()
        except KeyboardInterrupt:
            process.kill()
            process.wait()
            raise
        if stalled:
            process.kill()
            process.wait()
            collected.append(f"no output for {int(stall_timeout)}s — the download stopped answering")
        code = process.poll()
        if code is None:
            code = process.wait()
    reader.join(timeout=2)
    if stalled:
        return 124, collected
    return code, collected


# ── why a pip git install failed, in words a person can act on ──────────────
# pip's own message for a private repository is "Repository not found", which
# reads exactly like a mistyped URL. The difference matters: one is a typo, the
# other needs a login. These tests look for the fingerprints of each case.
_CAUSE_TESTS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        (
            "repository not found",
            "could not read username",
            "terminal prompts disabled",
            "authentication failed",
        ),
        "GitHub says this repository does not exist — or it is private and this device is not logged in.",
        'A private repo answers "not found" instead of "denied", so a typo and a missing login look identical.',
    ),
    (
        ("could not find a version", "no matching distribution"),
        "pip could not find the package.",
        "Check the name and the branch.",
    ),
    (
        ("permission denied", "read-only file system", "errno 30"),
        "This device does not allow writing to that location.",
        "Try the single-file installer, which installs into an app-private directory.",
    ),
    (
        ("no space left", "errno 28"),
        "The device ran out of storage.",
        "Free some space and try again.",
    ),
    (
        ("network is unreachable", "temporary failure in name resolution", "connection refused", "timed out"),
        "The network dropped during the upgrade.",
        "Check the connection and run it again — nothing was half-installed.",
    ),
)


# ── duplicate installs ──────────────────────────────────────────────────────
def is_pipx_install(entry: str) -> bool:
    """Is *entry* part of a pipx install?

    Two different files answer to this and only one of them mentions pipx:

    * ``~/.local/bin/local-file-sharer`` — a pipx shim, whose text says pipx;
    * ``~/.local/share/pipx/venvs/local-file-sharer/bin/lfs`` — the console
      script inside the venv, with an ordinary Python shebang.

    Only the first used to be recognised, so the copy that pipx actually owns
    was reported as a plain ``pip`` install and the advice was to run
    ``pip uninstall`` on it — which does nothing, because pip did not make it.
    The path is therefore checked as well as the contents.
    """
    lowered = str(entry).lower().replace("\\", "/")
    if "/pipx/venvs/" in lowered or "/pipx/bin/" in lowered:
        return True
    # A shim in ~/.local/bin is often a symlink into the venv: follow it
    # before reading anything, or the path check above never sees pipx.
    try:
        resolved = str(Path(entry).resolve()).lower().replace("\\", "/")
    except OSError:
        resolved = ""
    if "/pipx/venvs/" in resolved or "/pipx/bin/" in resolved:
        return True
    try:
        with open(entry, "rb") as handle:  # noqa: PTH123 - need bytes, not text
            head = handle.read(600)
    except OSError:
        return False
    # The shim's own text names the venv it runs the app from, so this catches
    # the case where the file is a real script rather than a symlink.
    blob = head.lower()
    return b"pipx" in blob or b"pipx/venvs" in blob


def find_duplicates() -> list[dict[str, str]]:
    """Every other `lfs` / `local-file-sharer` on PATH, with its flavour.

    ``pipx install --force`` happily installs a second copy beside the one the
    device already has. Whichever comes first on PATH then wins, so an upgrade
    can update a copy that is never run — the "I upgraded and nothing changed"
    report again, from a different direction. The running copy is skipped.
    """
    import shutil as _shutil  # noqa: PLC0415

    found: list[dict[str, str]] = []
    running = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    for name in ("lfs", "local-file-sharer"):
        for entry in _shutil.which_all(name) if hasattr(_shutil, "which_all") else _which_all(name):
            path = Path(entry)
            try:
                real = path.resolve()
            except OSError:
                real = path
            if running is not None and real == running:
                continue
            kind = "pipx" if is_pipx_install(str(path)) else "pip"
            if any(item["path"] == str(path) for item in found):
                continue
            found.append({"name": name, "path": str(path), "kind": kind})
    return found


def remove_shadowing_copies(*, dry_run: bool = False) -> list[tuple[str, str]]:
    """Remove the extra `lfs` binaries that win PATH over the running one.

    Returns ``(path, result)`` pairs. Only copies that are *shadowing* are
    touched: files nothing else points at, that are not the running binary.
    A pipx or site-packages install is left alone -- those are removed with the
    tool that made them, and deleting their launcher would leave a broken
    half-install behind.
    """
    from rich.text import Text  # noqa: PLC0415

    con = console()
    winner = _first_on_path()
    results: list[tuple[str, str]] = []
    duplicates = find_duplicates()
    if not winner and not duplicates:
        con.print("[lfs.success]✓[/] [lfs.muted]the lfs your shell runs is this one[/]")
        return results
    if not winner:
        con.print("[lfs.success]✓[/] [lfs.muted]nothing is shadowing this copy[/]")
        return results

    # A device commonly has three or four of these, not two. Removing only the
    # winner leaves the next one to take its place, and the user is back here
    # tomorrow. Order matters as well as count: the shell finds the first, so
    # list them in PATH order and clear everything that is not this binary.
    ordered = sorted(duplicates, key=lambda item: (item["path"] != winner, item["path"]))
    pipx_seen = False
    for item in ordered:
        path = Path(item["path"])
        if item["kind"] == "pipx":
            # One pipx uninstall covers every file pipx owns.
            if pipx_seen:
                continue
            pipx_seen = True
            what = "pipx uninstall local-file-sharer"
            results.append((str(path), what))
            con.print(f"[lfs.warning]needs pipx[/]  [lfs.faint]{path}[/]")
            con.print(f"[lfs.muted]  run[/] [lfs.primary]{what}[/]")
            continue
        if dry_run:
            results.append((str(path), "would remove"))
            con.print(f"[lfs.muted]would remove[/]  [lfs.faint]{path}[/]")
            continue
        try:
            path.unlink()
            results.append((str(path), "removed"))
            con.print(Text(f" ✔ removed {path}", style="lfs.success"))
        except OSError as exc:
            results.append((str(path), f"failed: {exc}"))
            con.print(f"[lfs.danger]✖ could not remove[/] [lfs.faint]{path}[/]  [lfs.muted]{exc}[/]")
    if any(result == "removed" for _p, result in results):
        con.print("[lfs.muted]open a new shell (or run[/] [lfs.primary]hash -r[/][lfs.muted]) and try again[/]")
    return results


def _first_on_path() -> str:
    """The `lfs` that a bare `lfs` in the shell would run."""
    mine = find_duplicates()
    for entry in _which_all("lfs"):
        if not any(item["path"] == entry for item in mine):
            return ""  # this is the copy we are running
        return entry
    return ""


def _which_all(name: str) -> list[str]:
    """shutil.which_all landed in 3.12; this covers 3.9+."""
    seen: list[str] = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            text = str(candidate)
            if text not in seen:
                seen.append(text)
    return seen


def explain_failure(lines: list[str]) -> tuple[str, str]:
    """``(reason, hint)`` for a failed upgrade, or ``("", "")`` when unknown."""
    blob = " ".join(lines).lower()
    for needles, reason, hint in _CAUSE_TESTS:
        if any(needle in blob for needle in needles):
            return reason, hint
    return "", ""


def _show_failure(con, lines: list[str], *, limit: int = 14, headline: str = "last lines from pip:") -> None:  # noqa: ANN001
    """Print the tail of a failed pip run: that is where the reason lives."""
    if not lines:
        return
    tail = lines[-limit:]
    if headline:
        con.print(f"[lfs.muted]{headline}[/]")
    for line in tail:
        con.print(f"  [lfs.faint]{line[:160]}[/]")


def checkout_root() -> Path | None:
    """The git checkout this build was imported from, if there is one.

    An editable install runs the source tree directly, so the tree above the
    package *is* the checkout. A clone installed with ``./run install`` is
    exactly that, and for those ``git pull`` is the correct upgrade — it keeps
    the user's own history, and it is the thing ``./run upgrade`` does.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").exists():
            return parent
    return None


def _runs_from(root: Path) -> bool:
    """True when the imported package lives inside *root*."""
    package = Path(__file__).resolve().parent
    try:
        package.relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _pull_checkout(root: Path) -> int:
    """``git pull --ff-only`` inside *root*, then reinstall editable."""
    con = console()
    con.print(f"[lfs.muted]checkout[/]  [lfs.primary]{root}[/]")
    code, lines = run_with_progress(
        ["git", "-C", str(root), "pull", "--ff-only"],
        con=con,
        title="updating the checkout",
    )
    if code != 0:
        con.print("[lfs.danger]git pull failed[/]  [lfs.muted]nothing was changed[/]")
        _show_failure(con, lines, headline="last lines from git:")
        return 1
    con.print("  [lfs.muted]pulled[/]")
    # Python caches compiled modules next to the source. A checkout whose file
    # times did not move forward -- an unpacked archive, a restored backup, a
    # pull that only rewrote some files -- keeps importing the OLD bytecode and
    # the update silently does nothing. Dropping the caches costs milliseconds
    # and removes the whole class of "I upgraded and it is still the old one".
    dropped = 0
    for cache in root.rglob("__pycache__"):
        if ".venv" in cache.parts:
            continue
        shutil.rmtree(cache, ignore_errors=True)
        dropped += 1
    if dropped:
        con.print(f"  [lfs.faint]cleared {dropped} bytecode cache(s)[/]")
    code, lines = run_with_progress(
        [sys.executable, "-m", "pip", "install", "--quiet", "--editable", str(root)],
        con=con,
        title="reinstalling",
    )
    if code != 0:
        con.print("[lfs.danger]reinstall failed[/]  [lfs.muted]the code was pulled but not installed[/]")
        _show_failure(con, lines)
        return 1
    con.print("  [lfs.success]✓[/] [lfs.muted]upgraded — start it again with[/] [lfs.primary]lfs[/]")
    return 0


def upgrade_from_git(ref: str | None = None, *, verbose: bool = False) -> int:
    """Install the newest code from GitHub, the only place this package lives.

    ``lfs --update`` looks for a GitHub *release*, and there are none yet, so it
    can only report ``404``. This does the thing the user actually wants: pull
    the current commit of the branch they installed from.

    A git checkout is handled in place instead: reinstalling over it from
    GitHub would work, but it throws away the local history and takes longer,
    and ``git pull`` is what ``./run upgrade`` does. Asking people to run the
    wrapper script for a normal update was the reason ``./run`` kept being
    confused with the command.
    """
    con = console()
    root = checkout_root()
    # Only when this really is an editable install of that checkout. A test, a
    # frozen build or a pipx copy can sit next to a .git directory without
    # being that tree, and pulling then reinstalling the wrong thing is worse
    # than reinstalling from GitHub.
    if root is not None and ref is None and _runs_from(root):
        return _pull_checkout(root)
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
        # --force-reinstall is not optional here. pip treats a git URL as a
        # candidate version, and this package reports the same version from every
        # commit, so `--upgrade` decides it is "already satisfied" and installs
        # nothing — silently, with no error and no "Successfully installed" line.
        # Every upgrade after the first was therefore a no-op. --no-deps keeps it
        # fast: rich/qrcode are already present and do not need reinstalling.
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-deps",
            spec,
        ]
    con.print(f"[lfs.muted]from[/]  [lfs.primary]{target}[/]  [lfs.faint]({GIT_URL})[/]")
    # Say which copy is being upgraded when there is more than one, or the user
    # runs the other one afterwards and concludes the upgrade did nothing.
    here = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else Path("lfs")
    con.print(f"[lfs.muted]this copy[/]  [lfs.faint]{here}[/]")
    clashes = find_duplicates()
    if clashes:
        first = _first_on_path()
        con.print("[lfs.warning]another copy is installed:[/]")
        for item in clashes:
            mark = "  [lfs.warning](runs first)[/]" if item["path"] == first else ""
            con.print(f"  [lfs.faint]{item['path']}[/]  [lfs.muted]{item['kind']}[/]{mark}")
        # Name the command that removes the copy that actually wins. Telling
        # someone "pip uninstall" while the shadowing copy is a pipx install,
        # or a bare symlink in ~/bin, leaves them exactly where they started.
        winner = next((item for item in clashes if item["path"] == first), None)
        if winner is not None:
            how = (
                "pipx uninstall local-file-sharer"
                if winner["kind"] == "pipx"
                else f"rm {winner['path']}"
            )
            con.print(
                f"[lfs.muted]the one that wins your PATH is[/] "
                f"[lfs.primary]{winner['path']}[/]"
            )
            con.print(f"[lfs.muted]remove it with[/]  [lfs.primary]{how}[/]")
            con.print(
                "[lfs.muted]then[/]  [lfs.primary]hash -r[/]  "
                "[lfs.muted](or open a new session) and run[/] [lfs.primary]lfs[/]"
            )
        else:
            con.print(
                "[lfs.muted]only this copy is upgraded. remove the other with[/] "
                "[lfs.primary]pipx uninstall local-file-sharer[/] "
                "[lfs.muted]or[/] [lfs.primary]pip uninstall local-file-sharer[/]"
            )
    # Record both identities: the commit when the install has one, and the
    # build stamp when it does not. A pipx/editable/unpacked install has no
    # commit, and comparing "nothing" against "nothing" used to report a
    # successful upgrade that never installed anything.
    before = installed_commit()
    stamp_before = installed_stamp()[1]
    wanted = remote_commit(target)
    if verbose:
        # Some failures are only diagnosable from git's own voice, so offer a
        # way to see it live instead of guessing from a bar that does not move.
        con.print(f"[lfs.muted]running[/]  [lfs.faint]{' '.join(cmd)}[/]")
        code = subprocess.call(cmd, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))  # noqa: S603
        output = []
    else:
        code, output = run_with_progress(cmd, con=con)
    if code == 127:
        con.print("[lfs.danger]could not run pip — install it, or re-run install.sh[/]")
        return 1
    if code == 124:
        # Killed by the stall watchdog, not by pip. Say what happened and what
        # to try: an unexplained 124 would look like an app bug.
        con.print("[lfs.danger]the download stopped answering[/]")
        con.print(f"[lfs.muted]no output for {int(STALL_TIMEOUT)}s, so it was abandoned.[/]")
        con.print("[lfs.muted]nothing was changed. a weak connection is the usual cause —[/]")
        con.print("[lfs.muted]try again, or on a slow link use the single file:[/] [lfs.primary]install-lfs.py[/]")
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
        reason, hint = explain_failure(output)
        if reason:
            # Lead with the cause. pip's own words are a wall of "subprocess-
            # exited-with-error" that says "Repository not found" in the middle.
            con.print(f"[lfs.danger]upgrade failed[/]  {reason}")
            if hint:
                con.print(f"[lfs.muted]{hint}[/]")
            if "private" in reason.lower() or "does not exist" in reason:
                con.print(
                    "[lfs.muted]no GitHub login on this device is fine — use the single-file"
                    " installer instead:[/] python3 install-lfs.py"
                )
        else:
            con.print(f"[lfs.danger]upgrade failed[/]  see {REPOSITORY_PAGE} for manual steps")
        _show_failure(con, output, headline="")
        con.print(f"[lfs.muted]try a branch explicitly:[/] lfs upgrade --ref {DEFAULT_BRANCH}")
        return code
    # pip exits 0 even when it installs nothing, so ask the record on disk what
    # actually landed. This is what catches the silent no-op for good.
    changed, was, now = upgrade_result(before or stamp_before, target)
    if not changed:
        # Two very different situations shared this branch: "already newest"
        # and "pip said yes but nothing landed". Saying "already on the newest
        # code" for the second is how a broken update stays invisible.
        after_commit, after_stamp, _v = installed_stamp()
        installed_now = after_commit or after_stamp
        if installed_now and before and after_commit == before:
            con.print("[lfs.warning]  nothing changed[/]  [lfs.muted]already on the newest code[/]")
            con.print(f"[lfs.muted]at {before[:7]}  ({target})[/]")
            return 0
        if installed_now and stamp_before and after_stamp == stamp_before and not after_commit:
            con.print("[lfs.warning]  nothing changed[/]  [lfs.muted]already on the newest code[/]")
            con.print(f"[lfs.muted]build {after_stamp}[/]")
            return 0
        con.print("[lfs.warning]could not confirm the upgrade[/]")
        con.print(
            "[lfs.muted]pip reported success but nothing on disk proves it. Check with[/] "
            "[lfs.primary]lfs --version[/] [lfs.muted]and the build stamp in the player header.[/]"
        )
        return 0
    con.print("[lfs.success]  upgraded[/]  [lfs.muted]restart `lfs` to use the new code[/]")
    if now:
        short_was = was[:7] if was else "unknown"
        short_now = now[:7]
        behind = f"  [lfs.faint](wanted {wanted[:7]})[/]" if wanted and wanted != now else ""
        con.print(f"[lfs.muted]{short_was} → {short_now}[/]{behind}")
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
