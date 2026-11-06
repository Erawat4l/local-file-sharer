"""`lfs uninstall`: remove the command, the copies, the folder and the data.

Installing was made to end with a working `lfs`; this is the other half, and it
has to be just as honest. The hard parts are not deletion but *order* and
*scope*:

* The running executable cannot always be deleted while it runs. On Linux a
  running script's file may be removed safely, but its ``.venv`` cannot always
  be torn down from inside it. So every path that must go is *reported* when it
  could not be removed, and the command exits non-zero so a script can tell.

* Uninstalling must not silently keep four other copies of `lfs` alive. The
  same shadowing problem that made "I upgraded and nothing changed" possible
  will otherwise make "I uninstalled and it is still there" possible.

* The user's *files* are never touched. Only what this app created: its own
  launchers, its checkout, its config and its cache.

Nothing here deletes a directory that does not look like a local-file-sharer
install, and nothing recursive runs until the path has been checked to be one.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import platforms
from .updater import find_duplicates, is_pipx_install


@dataclass
class Step:
    """One thing to remove: what it is, where, and how it went."""

    what: str
    path: str
    removed: bool = False
    detail: str = ""


@dataclass
class Plan:
    """Everything an uninstall will do, decided before anything is touched."""

    steps: list[Step] = field(default_factory=list)
    #: pipx/pip copies we cannot delete by hand; the user runs these instead.
    commands: list[str] = field(default_factory=list)
    #: things that will be kept on purpose, said out loud
    kept: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every step that was attempted succeeded."""
        return all(step.removed for step in self.steps)


def _is_checkout(path: Path) -> bool:
    """Is *path* a local-file-sharer checkout we created, or someone's folder?

    Deleting a directory tree is the one genuinely dangerous thing here, so
    the test is the *package*, not the file names: a `pyproject.toml` that
    names this project, plus the src directory that actually holds it. "A
    folder containing a file called run" is not evidence -- plenty of projects
    have one -- and treating it as evidence would delete somebody's work.
    """
    if not path.is_dir():
        return False
    pyproject = path / "pyproject.toml"
    package = path / "src" / "local_file_sharer"
    if not pyproject.is_file() or not package.is_dir():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "local-file-sharer" in text or "local_file_sharer" in text


def running_from(path: Path) -> bool:
    """Is *path* inside the tree this process is executing from?

    A checkout cannot reliably delete its own virtualenv while running from it,
    so it is reported instead of half-removed.
    """
    try:
        here = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else Path.cwd()
        base = Path(sys.prefix).resolve()
    except OSError:
        return False
    target = path.resolve()
    return any(root == target or root.is_relative_to(target) for root in (here, base))


def _path_entries(name: str) -> list[str]:
    """Every *name* on PATH, whether or not it is the one that would run."""
    import os  # noqa: PLC0415

    found: list[str] = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / name
        if candidate.is_symlink() or candidate.exists():
            found.append(str(candidate))
    return found


def _own_tree() -> Path | None:
    """The checkout this process is running from, when it is a checkout."""
    try:
        prefix = Path(sys.prefix).resolve()
    except OSError:
        return None
    if prefix.name != ".venv":
        return None
    candidate = prefix.parent
    return candidate if _is_checkout(candidate) else None


def make_plan(*, purge_data: bool = False, keep_checkout: bool = False) -> Plan:
    """Decide everything that should go, without deleting anything yet."""
    plan = Plan()
    tree = _own_tree()
    own = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None

    # 1. every launcher on PATH, with the flavour that knows how to remove it
    seen: set[str] = set()
    for item in find_duplicates():
        path = item["path"]
        if path in seen:
            continue
        seen.add(path)
        if item["kind"] == "pipx":
            # pipx owns a venv behind that shim; deleting the shim by hand
            # leaves a broken half-install, so it is delegated.
            command = "pipx uninstall local-file-sharer"
            if command not in plan.commands:
                plan.commands.append(command)
            continue
        plan.steps.append(Step("launcher", path))

    # The symlink the shell actually runs is skipped by find_duplicates(): it
    # resolves to this very binary, so it looks like "the running copy". It is
    # not -- it is a separate file on PATH that outlives everything else and
    # would be left dangling. Every symlink pointing into our own tree goes.
    if tree is not None:
        for entry in _path_entries("lfs") + _path_entries("local-file-sharer"):
            path = Path(entry)
            if str(path) in seen or not path.is_symlink():
                continue
            try:
                target = path.resolve()
            except OSError:
                continue
            if target == own or target.is_relative_to(tree):
                seen.add(str(path))
                plan.steps.append(Step("launcher (on PATH)", str(path)))

    # the copy that is running right now, removed last
    if own is not None and own.exists():
        plan.steps.append(Step("running executable", str(own)))

    # 2. the checkout, if this is a directory install rather than site-packages
    if tree is not None:
        if keep_checkout:
            plan.kept.append(f"checkout {tree} (--keep-checkout)")
        else:
            # Never rmtree the tree we are standing in: pip's own imports come
            # from that .venv, so deleting it mid-run crashes the command with
            # a ModuleNotFoundError traceback -- after it has already removed
            # half of itself. A detached shell removes it once we are gone.
            plan.steps.append(Step("checkout (after this exits)", str(tree)))

    # 3. config and cache -- a reinstall should not inherit a stale PIN
    plan.steps.append(Step("config", str(platforms.config_dir())))
    if purge_data:
        plan.steps.append(Step("cache and history", str(platforms.cache_dir())))
    else:
        plan.kept.append(f"{platforms.cache_dir()} (history and recents; --purge-data)")
    return plan


def _remove(step: Step) -> None:
    path = Path(step.path)
    if not path.exists() and not path.is_symlink():
        step.removed = True
        step.detail = "already gone"
        return
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        step.removed = True
    except OSError as exc:
        step.removed = False
        step.detail = str(exc)


def _defer_removal(path: Path) -> bool:
    """Delete *path* from a detached shell once this process is gone.

    A checkout cannot remove itself: its `.venv` is where pip and rich are
    being imported from, so `shutil.rmtree` unloads the ground the interpreter
    is standing on. The command died with ModuleNotFoundError after deleting
    part of itself. A `sh -c 'sleep 1; rm -rf ...'` started with start_new_session
    survives us and finishes the job.
    """
    import shlex  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    script = f"sleep 1; rm -rf {shlex.quote(str(path))}"
    try:
        subprocess.Popen(  # noqa: S603
            ["/bin/sh", "-c", script],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def apply(plan: Plan, *, dry_run: bool = False) -> Plan:
    """Carry out *plan*, in order. Never raises for a single failed step."""
    if dry_run:
        return plan
    # Launchers before the checkout: removing the tree first would leave a
    # dangling symlink on PATH that reports "no such file" for every command.
    for step in plan.steps:
        if step.what.startswith("checkout (after"):
            continue
        _remove(step)
    for step in plan.steps:
        if step.what.startswith("checkout (after"):
            if _defer_removal(Path(step.path)):
                step.removed = True
                step.detail = "in a moment"
            else:
                _remove(step)
    return plan


def uninstall(*, dry_run: bool = False, purge_data: bool = False, keep_checkout: bool = False) -> int:
    """Run the whole thing. Returns a shell exit code."""
    from rich.text import Text  # noqa: PLC0415

    from .ui.console import console  # noqa: PLC0415

    con = console()
    plan = make_plan(purge_data=purge_data, keep_checkout=keep_checkout)

    if not plan.steps and not plan.commands:
        con.print("[lfs.success]✓[/] [lfs.muted]nothing to remove — this looks like a clean machine[/]")
        return 0

    verb = "would remove" if dry_run else "removing"
    # Print the plan before touching anything, so a crash mid-removal still
    # leaves a record of what was attempted.
    for step in plan.steps:
        con.print(f"[lfs.muted]{verb}[/] [lfs.faint]{step.path}[/]  [lfs.faint]({step.what})[/]")
    for command in plan.commands:
        con.print(f"[lfs.warning]run yourself[/] [lfs.primary]{command}[/]")
    for note in plan.kept:
        con.print(f"[lfs.faint]kept[/]  {note}")

    if dry_run:
        con.print("\n[lfs.muted]nothing was changed (--dry-run)[/]")
        return 0

    # Everything below runs *before* the console is asked to render anything
    # else. Rich loads its character tables lazily from site-packages, so any
    # print after this checkout is deleted can raise ModuleNotFoundError.
    apply(plan)
    kept = list(plan.kept)
    commands = list(plan.commands)
    steps = list(plan.steps)

    failed = [step for step in steps if not step.removed]
    for step in failed:
        con.print(Text(f" ✖ could not remove {step.path}", style="lfs.danger"))
        con.print(f"[lfs.faint]   {step.detail}[/]")

    failed_paths = {s.path for s in failed}
    remaining = [item for item in find_duplicates() if item["path"] not in failed_paths]
    if remaining:
        con.print("\n[lfs.warning]these still answer `lfs`:[/]")
        for item in remaining:
            how = (
                "pipx uninstall local-file-sharer" if is_pipx_install(item["path"]) else f"rm {item['path']}"
            )
            con.print(f"[lfs.faint]  {item['path']}[/]  [lfs.primary]{how}[/]")

    if commands:
        con.print("\n[lfs.muted]one step is left to you:[/]")
        for command in commands:
            con.print(f"  [lfs.primary]{command}[/]")
    if kept:
        con.print("\n[lfs.muted]kept:[/] [lfs.faint]" + ", ".join(kept) + "[/]")

    if failed:
        con.print("\n[lfs.warning]partly removed[/] [lfs.muted]— the lines above say what is left.[/]")
        return 1
    if commands:
        con.print("\n[lfs.muted]then run the command above and `lfs` is gone.[/]")
        return 0
    con.print("\n[lfs.success]✓[/] [lfs.muted]removed — `lfs` is gone. Your files were not touched.[/]")
    return 0


__all__ = ["Plan", "Step", "apply", "make_plan", "running_from", "uninstall"]
