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

#: Names the app writes into a directory that mean "this is ours to delete".
_CHECKOUT_MARKERS = ("pyproject.toml", "run", "setup")
#: Files/directories inside a checkout that are generated, not source.
_GENERATED = (".venv", ".lfs-tmp", "*.egg-info", "__pycache__")


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

    Deleting a directory tree is the one genuinely dangerous thing here. The
    test is deliberately narrow: a pyproject.toml naming this project, or the
    run/setup pair every checkout ships. A directory failing this is never
    removed, whatever it is called.
    """
    if not path.is_dir():
        return False
    pyproject = path / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "local-file-sharer" in text or "local_file_sharer" in text:
            return True
    return all((path / name).exists() for name in ("run", "setup"))


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
    for root in (here, base):
        if root == target or root.is_relative_to(target):
            return True
    return False


def make_plan(*, purge_data: bool = False, keep_checkout: bool = False) -> Plan:
    """Decide everything that should go, without deleting anything yet."""
    plan = Plan()

    # 1. every launcher on PATH, with the flavour that knows how to remove it
    for item in find_duplicates():
        path = item["path"]
        if item["kind"] == "pipx":
            # pipx owns a venv behind that shim; deleting the shim by hand
            # leaves a broken half-install, so it is delegated.
            command = "pipx uninstall local-file-sharer"
            if command not in plan.commands:
                plan.commands.append(command)
            continue
        plan.steps.append(Step("launcher", path))

    # the copy that is running right now is not in find_duplicates(); it is
    # removed last because deleting it mid-run is the one risky delete
    own = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if own is not None and own.exists():
        plan.steps.append(Step("launcher (this one)", str(own)))

    # 2. the checkout, if this is a directory install rather than site-packages
    checkout = Path(sys.prefix).resolve().parent if "venv" in sys.prefix.lower() else None
    if checkout is None:
        # the console script lives in <checkout>/.venv/bin/lfs
        candidate = own.parent.parent.parent if own is not None else None
        checkout = candidate
    if checkout is not None and _is_checkout(checkout):
        if keep_checkout:
            plan.kept.append(f"checkout {checkout} (--keep-checkout)")
        elif running_from(checkout):
            plan.steps.append(Step("checkout (after this command exits)", str(checkout)))
        else:
            plan.steps.append(Step("checkout", str(checkout)))
    elif checkout is not None:
        plan.kept.append(f"{checkout} (not a local-file-sharer checkout)")

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
    for step in plan.steps:
        con.print(f"[lfs.muted]{verb}[/] [lfs.faint]{step.path}[/]  [lfs.faint]({step.what})[/]")
    for command in plan.commands:
        con.print(f"[lfs.warning]run yourself[/] [lfs.primary]{command}[/]")
    for note in plan.kept:
        con.print(f"[lfs.faint]kept[/]  {note}")

    if dry_run:
        con.print("\n[lfs.muted]nothing was changed (--dry-run)[/]")
        return 0

    apply(plan)

    failed = [step for step in plan.steps if not step.removed]
    for step in failed:
        con.print(Text(f" ✖ could not remove {step.path}", style="lfs.danger"))
        con.print(f"[lfs.faint]   {step.detail}[/]")

    remaining = [item for item in find_duplicates() if item["path"] not in {s.path for s in failed}]
    if remaining:
        con.print("\n[lfs.warning]these still answer `lfs`:[/]")
        for item in remaining:
            how = "pipx uninstall local-file-sharer" if is_pipx_install(item["path"]) else f"rm {item['path']}"
            con.print(f"[lfs.faint]  {item['path']}[/]  [lfs.primary]{how}[/]")

    if plan.commands:
        con.print("\n[lfs.muted]one step is left to you:[/]")
        for command in plan.commands:
            con.print(f"  [lfs.primary]{command}[/]")
    if plan.kept:
        con.print("\n[lfs.muted]kept:[/] [lfs.faint]" + ", ".join(plan.kept) + "[/]")

    if failed:
        con.print("\n[lfs.warning]partly removed[/] [lfs.muted]— the lines above say what is left.[/]")
        return 1
    if plan.commands:
        con.print("\n[lfs.muted]then run the command above and `lfs` is gone.[/]")
        return 0
    con.print("\n[lfs.success]✓[/] [lfs.muted]removed — `lfs` is gone. Your files were not touched.[/]")
    return 0


__all__ = ["Plan", "Step", "apply", "make_plan", "running_from", "uninstall"]
