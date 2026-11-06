"""Command line entry point: the ``lfs`` command.

```
start                      interactive menu
lfs <path> [...]         share files/folders right away
lfs --mode stream        pick media and open the player
lfs --mode torrent       build a .torrent + magnet with a built-in tracker
lfs doctor               diagnostics
lfs config show|init|…
lfs theme dark|light|mono
lfs completions fish|bash|powershell
lfs --update             self-update from GitHub
```

Design rules
------------
* No tracebacks for expected problems: every error is rendered with a fix.
* Works when piped (plain output), when ``NO_COLOR`` is set, and on a phone.
* ``--debug`` restores the raw traceback when you actually need it.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from . import APP_CMD, APP_NAME, APP_TAGLINE, REPO_URL, __version__, platforms
from .config import (
    Config,
    config_path,
    dump_config,
    get_value,
    init_config,
    load_config,
    set_value,
)
from .errors import SharerError
from .share import MODES, ShareRequest, ShareRunner, ask_expiry
from .state import State

BANNER_LINES = [
    " ██▓     █████▒▒█████   local-file-sharer",
    "▓██▒   ▓██   ▒▒██▒  ██▒ " + APP_TAGLINE,
    "▒██░   ▒████ ░▒██░  ██▒",
    "▒██░   ░▓█▒  ░▒██   ██░",
    "░██████░▒█░   ░ ████▓▒░",
]


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────
# argparse cannot mix a ``nargs="*"`` positional ("the paths to share") with
# subparsers, so dispatch happens explicitly: the first bare word decides whether
# we run a subcommand or share the given paths.
SUBCOMMAND_NAMES = (
    "menu",
    "doctor",
    "config",
    "configure",
    "theme",
    "completions",
    "completion",
    "banner",
    "upgrade",
    "update",
    "uninstall",
    "version",
    "help",
)

_SHELLS = ("fish", "bash", "powershell", "pwsh", "zsh")


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-m", "--mode", choices=MODES, default=None, help="share mode")
    parser.add_argument("-p", "--port", type=int, default=None, help="port to listen on (default 8080)")
    parser.add_argument(
        "--pin", nargs="?", const="", default=None, help="require a PIN (omit the value for a random one)"
    )
    parser.add_argument("--expire", type=int, default=None, metavar="MINUTES", help="auto-expire the share")
    parser.add_argument("--upload", action="store_true", help="allow devices to upload files back to you")
    tunnel = parser.add_mutually_exclusive_group()
    tunnel.add_argument("--tailscale", action="store_true", help="publish on your tailnet without asking")
    tunnel.add_argument("--no-tailscale", action="store_true", help="skip Tailscale")
    cft = parser.add_mutually_exclusive_group()
    cft.add_argument("--cloudflare", action="store_true", help="create a Cloudflare Tunnel without asking")
    cft.add_argument("--no-cloudflare", action="store_true", help="skip Cloudflare")
    parser.add_argument("--funnel", action="store_true", help="use `tailscale funnel` (public internet)")
    parser.add_argument("--open", action="store_true", help="open the share in your browser")
    parser.add_argument("--no-qr", action="store_true", help="do not print the QR code")
    parser.add_argument("--no-copy", action="store_true", help="do not copy links to the clipboard")
    parser.add_argument("--no-dashboard", action="store_true", help="plain log instead of the live dashboard")
    parser.add_argument("--theme", choices=("dark", "light", "mono"), default=None, help="colour theme")
    parser.add_argument("--no-anim", action="store_true", help="static banner (no shimmer)")
    # Bare `lfs` now shows the menu, which is what people expect and what the
    # README shows. These two flags choose the other behaviour explicitly.
    parser.add_argument(
        "--menu",
        dest="no_picker",
        action="store_true",
        help="show the text menu first (the default)",
    )
    parser.add_argument(
        "--picker",
        dest="force_picker",
        action="store_true",
        help="skip the menu and go straight to the arrow-key picker",
    )
    parser.add_argument(
        "--icons", dest="icons", action="store_true", default=None, help="glyph icons in the picker"
    )
    parser.add_argument("--no-icons", dest="icons", action="store_false", help="plain names in the picker")
    parser.add_argument("--no-color", action="store_true", help="disable colour (same as NO_COLOR=1)")
    parser.add_argument("--interface", default="", help="prefer a network interface, e.g. wlan0")
    parser.add_argument("--debug", action="store_true", help="show the full traceback on errors")
    parser.add_argument("--json", action="store_true", help="machine-readable output where supported")
    # handled in main() so the output can say where this copy was installed from
    parser.add_argument("-V", "--version", action="store_true", help="print the version and exit")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lfs",
        description=f"{APP_NAME} — {APP_TAGLINE}",
        epilog=(
            "commands:\n"
            "  lfs                     open the interactive menu\n"
            "  lfs <path> [...]        share files/folders right away\n"
            "  lfs doctor              check dependencies, firewall and network\n"
            "  lfs config show         print the effective configuration\n"
            "  lfs theme mono          switch theme (dark / light / mono)\n"
            "  lfs completions fish    print a shell completion script\n"
            "  lfs --update            self-update from GitHub\n"
            f"\nmore: {REPO_URL}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    parser.add_argument("paths", nargs="*", help="files and folders to share (default: open the menu)")
    add_common_flags(parser)
    parser.add_argument("--update", action="store_true", help="self-update from GitHub and exit")
    parser.add_argument("--check-update", action="store_true", help="only check whether an update exists")
    return parser


def build_command_parsers() -> dict[str, argparse.ArgumentParser]:
    """One small parser per subcommand so flags stay in their own namespace."""
    parsers: dict[str, argparse.ArgumentParser] = {}
    common = argparse.ArgumentParser(add_help=False)
    add_common_flags(common)

    parsers["menu"] = argparse.ArgumentParser(
        prog="lfs menu", parents=[common], description="open the interactive menu"
    )

    doctor = argparse.ArgumentParser(
        prog="lfs doctor", parents=[common], description="check dependencies, firewall and network"
    )
    doctor.add_argument("--fast", action="store_true", help="skip tool and network probes")
    doctor.add_argument(
        "--fix-copies",
        action="store_true",
        help="remove an older lfs that shadows this one on PATH",
    )
    parsers["doctor"] = doctor

    config = argparse.ArgumentParser(
        prog="lfs config", parents=[common], description="manage the configuration file"
    )
    config_sub = config.add_subparsers(dest="config_action", metavar="action")
    config_sub.add_parser("show", help="print the effective configuration")
    config_sub.add_parser("init", help="write a commented default config file")
    config_sub.add_parser("path", help="print the config file location")
    reset = config_sub.add_parser("reset", help="delete the config file (back to defaults)")
    reset.add_argument("--yes", action="store_true", help="skip the confirmation")
    get = config_sub.add_parser("get", help="read one key, e.g. network.port")
    get.add_argument("key")
    set_cmd = config_sub.add_parser("set", help="write one key, e.g. network.port 9000")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    parsers["config"] = config

    configure = argparse.ArgumentParser(
        prog="lfs configure",
        parents=[common],
        description="set port, PIN, theme and download folder, then write the config",
    )
    configure.add_argument("--yes", action="store_true", help="accept every default, ask nothing")
    parsers["configure"] = configure

    theme = argparse.ArgumentParser(
        prog="lfs theme", parents=[common], description="switch or preview the colour theme"
    )
    theme.add_argument("name", nargs="?", choices=("dark", "light", "mono", "list", "next"), default=None)
    theme.add_argument("--preview", action="store_true", help="show a colour preview")
    parsers["theme"] = theme

    upgrade = argparse.ArgumentParser(
        prog="lfs upgrade",
        parents=[common],
        description="install the newest code from GitHub (the package is not on PyPI)",
        epilog="`lfs update` does the same thing.",
    )
    upgrade.add_argument(
        "--ref",
        default=None,
        help="branch or tag to install (default: the one this copy came from)",
    )
    upgrade.add_argument(
        "--verbose",
        action="store_true",
        help="show pip's own output instead of just the progress bar",
    )
    parsers["upgrade"] = upgrade

    uninstall = argparse.ArgumentParser(
        prog="lfs uninstall",
        parents=[common],
        description="remove lfs from this device: the command, the checkout, the config",
        epilog=(
            "Your shared files are never touched — only what lfs itself created. "
            "`lfs uninstall --dry-run` lists it first."
        ),
    )
    uninstall.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be removed, then stop",
    )
    uninstall.add_argument(
        "--purge-data",
        action="store_true",
        help="also remove history, recents and the share counter",
    )
    uninstall.add_argument(
        "--keep-checkout",
        action="store_true",
        help="remove the command but leave the folder (so a reinstall is instant)",
    )
    uninstall.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    parsers["uninstall"] = uninstall
    # `lfs update` is the word people reach for; it shares the same parser.
    parsers["update"] = upgrade

    completions = argparse.ArgumentParser(
        prog="lfs completions", parents=[common], description="print a shell completion script"
    )
    completions.add_argument("shell", nargs="?", choices=_SHELLS, default=None)
    parsers["completions"] = completions
    parsers["completion"] = completions

    banner = argparse.ArgumentParser(
        prog="lfs banner", parents=[common], description="preview the animated banner"
    )
    banner.add_argument("--loop", action="store_true", help="loop until Ctrl+C")
    parsers["banner"] = banner

    parsers["version"] = argparse.ArgumentParser(
        prog="lfs version", parents=[common], description="print the version"
    )
    parsers["help"] = argparse.ArgumentParser(
        prog="lfs help", parents=[common], description="show the main help"
    )
    return parsers


#: Options that consume the following token as their value.
_VALUE_OPTIONS = {"-p", "--port", "-m", "--mode", "--theme", "--expire", "--interface", "--pin"}


#: Short forms people type. Resolved before the prefix search, because ``up``
#: is a prefix of nothing and ``conf`` is a prefix of two different commands.
_COMMAND_ALIASES = {
    "up": "upgrade",
    "upd": "upgrade",
    "conf": "configure",
    "cfg": "configure",
    "doc": "doctor",
    "ver": "version",
    "men": "menu",
    "comp": "completions",
}


def resolve_subcommand(word: str) -> str:
    """The subcommand *word* means, or ``""`` when it is not one.

    ``lfs up`` and ``lfs conf`` used to fall through to the share path and
    answer "Nothing to share", which reads like a bug in the app rather than a
    typo in the command. An unambiguous prefix is now accepted; an ambiguous
    one (``c``, ``com``) is not, so a real file called ``c`` still shares.
    """
    lowered = word.strip().lower()
    if lowered in SUBCOMMAND_NAMES:
        return lowered
    if lowered in _COMMAND_ALIASES:
        return _COMMAND_ALIASES[lowered]
    matches = [name for name in SUBCOMMAND_NAMES if name.startswith(lowered)]
    # an exact-length prefix would have matched above; 2 chars avoids turning
    # a shareable one-letter file into a subcommand
    return matches[0] if len(lowered) >= 3 and len(matches) == 1 else ""


def split_argv(argv: list[str]) -> tuple[str | None, list[str]]:
    """Return ``(subcommand, remaining)`` when a bare word names a subcommand.

    ``lfs --port 9000 theme mono`` → ``("theme", ["--port", "9000", "mono"])``.
    Anything that looks like a path stops the scan, so
    ``lfs ./doctor`` still shares a file called *doctor*.
    """
    skip_next = False
    for index, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token in ("-h", "--help"):
            continue
        if token.startswith("-"):
            if token in _VALUE_OPTIONS:
                skip_next = True
            continue
        command = resolve_subcommand(token)
        if command:
            return command, argv[:index] + argv[index + 1 :]
        return None, argv
    return None, argv


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _apply_cli_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    """CLI flags win over the config file, for this run only."""
    if getattr(args, "port", None):
        cfg.network.port = int(args.port)
    if getattr(args, "theme", None):
        cfg.theme = args.theme
    if getattr(args, "no_anim", False):
        cfg.animate_banner = False
    if getattr(args, "no_dashboard", False):
        cfg.ui.live_dashboard = False
    if getattr(args, "icons", None) is not None:
        cfg.icons = bool(args.icons)
    if getattr(args, "interface", ""):
        cfg.network.prefer_interface = args.interface
    if getattr(args, "no_copy", False):
        cfg.share.copy_links = False
    return cfg


def _setup_console(cfg: Config, args: argparse.Namespace) -> None:
    from .ui.console import set_theme
    from .ui.theme import build_theme, default_theme_name

    name = cfg.theme or default_theme_name()
    set_theme(build_theme(name, cfg.accent))


def _resolve_paths(raw: list[str]) -> list[Path]:
    resolved: list[Path] = []
    for item in raw:
        path = platforms.normalize_path(item)
        if not path.is_absolute():
            path = Path.cwd() / path
        resolved.append(path)
    return resolved


#: Words people type as commands. A word in this set that is not a file on
#: disk is far more likely a command this build lacks than a filename.
_KNOWN_COMMANDS = frozenset(
    {
        "configure",
        "setup",
        "init",
        "install",
        "start",
        "run",
        "share",
        "serve",
        "send",
        "receive",
        "help",
        "status",
        "uninstall",
    }
)


def normalise_options(argv: list[str]) -> list[str]:
    """Repair the single-dash typo: ``-ref main`` → ``--ref main``.

    ``-ref`` reads like a long option but argparse only accepts one dash for
    one-letter flags, so it fails with "unrecognized arguments: -ref main" and
    dumps the whole usage block at the user. It is the most natural typo in the
    world -- the previous instruction said ``--ref`` in a sentence and it is
    easy to drop a dash -- so repair it rather than punish it.

    A token is only rewritten when it matches a long option *this build knows*
    and is longer than two characters, so ``-p 9000``, ``-V`` and a negative
    number are untouched. ``-confirm`` stays broken because no such option
    exists, rather than being silently mangled into ``--onfirm``.
    """
    known = _known_long_options()
    out: list[str] = []
    for token in argv:
        if token == "--":
            out.append(token)
            out.extend(argv[argv.index(token) + 1 :])
            break
        if (
            len(token) > 2
            and token.startswith("-")
            and not token.startswith("--")
            and not token[1].isdigit()
            and token[1:] in known
        ):
            out.append("-" + token)
        else:
            out.append(token)
    return out


_LONG_OPTIONS: set[str] | None = None


def _known_long_options() -> set[str]:
    """Every ``--long-option`` this build accepts, built once and cached."""
    global _LONG_OPTIONS
    if _LONG_OPTIONS is None:
        found: set[str] = set()
        for parser in (build_parser(), *build_command_parsers().values()):
            for action in parser._actions:
                for option in action.option_strings:
                    if option.startswith("--"):
                        found.add(option[2:])
        _LONG_OPTIONS = found
    return _LONG_OPTIONS


def _looks_like_a_command(word: str) -> bool:
    """True for anything the shell is more likely to mean as a command.

    Covers the subcommands this build has, the old hand-written list (words
    from other tools and later versions), and anything `resolve_subcommand`
    recognises -- so `lfs conf` on an old build says "this build may be old"
    instead of opening a share of a file called *conf*.
    """
    lowered = word.strip().lower()
    if not lowered or "/" in lowered or lowered.startswith("."):
        return False
    return (
        lowered in _KNOWN_COMMANDS
        or lowered in SUBCOMMAND_NAMES
        or lowered in _COMMAND_ALIASES
        or bool(resolve_subcommand(lowered))
    )


def _unknown_command(word: str) -> int:
    """Explain an unknown bare word, including that the install may be old."""
    from .ui.components import error_panel  # noqa: PLC0415
    from .ui.console import console as make_console  # noqa: PLC0415
    from .updater import installed_ref  # noqa: PLC0415

    con = make_console()
    con.print(
        error_panel(
            f"There is no file or folder called {word!r} here.",
            title="Unknown command",
            hints=[
                "`lfs` on its own opens the menu — that is the usual entry point",
                "`lfs --help` lists the commands this build has",
                "`lfs doctor` shows which copy answers your shell",
                "    (several lfs binaries is common; the oldest usually wins)",
                f"This copy reports ref {installed_ref() or 'unknown'}; if it is old:",
                "    lfs upgrade",
                "Nothing was shared or changed",
            ],
        )
    )
    return 2


def _print_version() -> None:
    """Version, interpreter — and where this copy came from.

    Without the last line a stale install is indistinguishable from a current
    one: the user runs a command that a newer build removed, gets the old
    behaviour, and has no way to tell which code they are running.
    """
    from .updater import installed_from_git, installed_ref  # noqa: PLC0415

    print(f"{APP_NAME} {__version__}")
    print(f"python {sys.version.split()[0]} · {platforms.platform_key()}")
    if installed_from_git():
        print(f"installed from git · ref {installed_ref()}")
    else:
        print("installed from a package (no git origin)")
    print(f"source {Path(__file__).resolve().parent}")


def _handle_error(exc: BaseException, *, debug: bool) -> int:
    from .doctor import hints_for_error
    from .ui.components import error_panel
    from .ui.console import console

    con = console()
    if isinstance(exc, SharerError):
        con.print(error_panel(exc.message, hints=exc.all_hints(), detail=exc.detail or "", title=exc.title))
        return 2
    if isinstance(exc, KeyboardInterrupt):
        con.print("[lfs.muted]stopped[/]")
        return 130
    hints = hints_for_error(exc)
    if debug:
        traceback.print_exc()
    con.print(
        error_panel(
            f"{type(exc).__name__}: {exc}",
            hints=hints or ["Re-run with --debug for the full traceback"],
            title="Unexpected error",
        )
    )
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────
def _warn_about_shadowing_copy(con) -> None:  # noqa: ANN001
    """One warning when an older `lfs` is what the shell actually runs.

    Every other symptom of this is misleading: the menu is old, a subcommand
    "does not exist", configure shows the share error -- all because a
    different binary answered. It costs one PATH scan to say so up front, once.
    """
    import os  # noqa: PLC0415

    if os.environ.get("LFS_NO_COPY_WARNING"):
        return
    try:
        from .updater import _first_on_path, find_duplicates  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    try:
        winner = _first_on_path()
        if not winner or not find_duplicates():
            return
    except Exception:  # noqa: BLE001 - never block the menu on this
        return
    from . import platforms as _platforms  # noqa: PLC0415
    from .updater import removal_command  # noqa: PLC0415

    winner_item = next((d for d in find_duplicates() if d["path"] == winner), {"path": winner, "kind": "pip"})
    how = removal_command(winner_item)
    con.print(
        f"[lfs.warning]![/] [lfs.muted]a different lfs answers your shell:[/] "
        f"[lfs.faint]{winner}[/]"
    )
    con.print(
        f"[lfs.muted]  this is[/] [lfs.faint]{sys.argv[0] if sys.argv else 'lfs'}[/]"
        f"[lfs.muted] — remove the other with[/] [lfs.primary]{how}[/]"
    )
    hint = _platforms.rehash_hint()
    if _platforms.current_shell() == "fish":
        # Deleting the winner hands `lfs` to the *next* copy, not to this one.
        # On Termux that next copy may be the one `$PREFIX/bin/lfs` now points
        # at, so say that instead of leaving a bare `rm` to be interpreted.
        con.print(
            "[lfs.muted]  after that,[/] [lfs.primary]the one that answers "
            "is whichever comes next on PATH[/][lfs.muted] — check with "
            "[/][lfs.primary]command -v lfs[/]"
        )
    elif hint:
        con.print(f"[lfs.muted]  then[/] [lfs.primary]{hint}[/]")
    con.print(
        "[lfs.faint]  or let it do both: lfs doctor --fix-copies"
        "   (quiet with LFS_NO_COPY_WARNING=1)"
    )


def cmd_menu(cfg: Config, state: State, args: argparse.Namespace) -> int:
    """Bare `lfs`: show the menu, then act.

    Jumping straight into the file picker hid the five modes behind a screen
    that only offered "pick files", so people who wanted Serve or Stream never
    saw them and asked why the menu was missing. The menu is the front door;
    `lfs --picker` and pressing 1 keep the one-step path for people who share
    files all day.
    """
    from rich.text import Text  # noqa: PLC0415

    from .menu import MenuContext, render_menu, run_menu, share_paths  # noqa: PLC0415
    from .ui.console import console as make_console  # noqa: PLC0415
    from .ui.console import current_theme

    animate = cfg.animate_banner and not args.no_anim
    con = make_console()
    _warn_about_shadowing_copy(con)
    jump_in = getattr(args, "force_picker", False)
    if not con.is_terminal or not jump_in:
        return run_menu(config=cfg, state=state, animate=animate)

    from .ui.picker_ui import run_picker  # noqa: PLC0415

    ctx = MenuContext(config=cfg, state=state, animate=animate)
    con.print(render_menu(ctx))
    con.print(
        Text(
            " opening the picker — arrows move · Space pick · b up · s share · q menu",
            style=current_theme().info,
        )
    )
    start = platforms.start_dir(state.last_dir)
    result = run_picker(start, icons=cfg.icons, page_size=cfg.ui.page_size)
    if result.cancelled or not result.paths:
        return run_menu(config=cfg, state=state, animate=False)
    share_paths(ctx, result.paths)
    return 0


def cmd_upgrade(cfg: Config, state: State, args: argparse.Namespace) -> int:
    from .updater import upgrade_from_git  # noqa: PLC0415

    return upgrade_from_git(getattr(args, "ref", None), verbose=bool(getattr(args, "verbose", False)))


def cmd_share(cfg: Config, state: State, args: argparse.Namespace, paths: list[Path]) -> int:
    from .share import ShareResult  # noqa: F401
    from .ui.console import human_output_to_stderr

    # stdout belongs to the JSON object at the end; keep it parseable.
    human_output_to_stderr(bool(args.json))

    mode = args.mode or cfg.share.serve_mode or "download"
    pin = cfg.share.pin
    if args.pin is not None:
        from .share import make_pin

        pin = args.pin or make_pin()
        if args.pin == "":
            print(f"PIN: {pin}")
    expire = args.expire if args.expire is not None else cfg.share.auto_expire_minutes
    if args.expire is None and not paths:
        expire = ask_expiry(cfg) if sys.stdin.isatty() else expire
    request = ShareRequest(
        paths=paths,
        mode=mode,
        port=cfg.network.port,
        pin=pin,
        expire_minutes=expire,
        allow_upload=args.upload or cfg.share.allow_upload,
        use_tailscale=True if args.tailscale else (False if args.no_tailscale else None),
        use_cloudflare=True if args.cloudflare else (False if args.no_cloudflare else None),
        funnel=args.funnel,
        open_browser=args.open,
        no_qr=args.no_qr or not cfg.share.show_qr,
        copy_links=not args.no_copy,
        interface=args.interface,
    )
    runner = ShareRunner(request, config=cfg, state=state)
    result = runner.run(wait=True)
    if args.json:
        import json  # noqa: PLC0415

        print(
            json.dumps(
                {
                    "url": result.url,
                    "lan_url": result.lan_url,
                    "public_ip": result.public_ip,
                    "tailscale_url": result.tailscale_url,
                    "cloudflare_url": result.cloudflare_url,
                    "magnet": result.magnet,
                    "bytes_sent": result.bytes_sent,
                    "duration": round(result.duration, 2),
                }
            )
        )
    return 0


def cmd_doctor(cfg: Config, state: State, args: argparse.Namespace) -> int:
    from .doctor import run_doctor

    if getattr(args, "fix_copies", False):
        from .updater import remove_shadowing_copies  # noqa: PLC0415

        remove_shadowing_copies(dry_run=bool(args.json))
        return 0
    return run_doctor(as_json=args.json, fast=args.fast)


def cmd_uninstall(cfg: Config, state: State, args: argparse.Namespace) -> int:
    """Remove lfs from the device. Prints the plan first, then asks."""
    from .uninstall import make_plan, uninstall  # noqa: PLC0415
    from .ui.console import console  # noqa: PLC0415

    dry_run = bool(getattr(args, "dry_run", False))
    purge = bool(getattr(args, "purge_data", False))
    keep = bool(getattr(args, "keep_checkout", False))

    if not getattr(args, "yes", False) and not dry_run:
        plan = make_plan(purge_data=purge, keep_checkout=keep)
        con = console()
        con.print("[lfs.warning]this removes lfs from this device:[/]")
        for step in plan.steps:
            con.print(f"[lfs.faint]  {step.path}[/]  [lfs.faint]({step.what})[/]")
        for command in plan.commands:
            con.print(f"[lfs.muted]  you also need:[/] [lfs.primary]{command}[/]")
        for note in plan.kept:
            con.print(f"[lfs.faint]  kept: {note}[/]")
        con.print("\n[lfs.muted]your shared files are not touched. Type 'yes' to go ahead:[/] ", end="")
        try:
            answer = input()
        except (EOFError, KeyboardInterrupt):
            con.print("\n[lfs.muted]cancelled — nothing was changed[/]")
            return 1
        if answer.strip().lower() not in ("y", "yes"):
            con.print("[lfs.muted]cancelled — nothing was changed[/]")
            return 1
    return uninstall(dry_run=dry_run, purge_data=purge, keep_checkout=keep)


def cmd_config(cfg: Config, state: State, args: argparse.Namespace) -> int:
    action = args.config_action or "show"
    if action == "show":
        print(dump_config(cfg))
        return 0
    if action == "init":
        path, created = init_config(force=False)
        if created:
            print(f"created {path}")
        else:
            print(f"already exists: {path}")
        return 0
    if action == "path":
        print(config_path())
        return 0
    if action == "reset":
        target = config_path()
        if not target.is_file():
            print(f"nothing to reset ({target} does not exist)")
            return 0
        if not args.yes:
            answer = input(f"delete {target}? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("cancelled")
                return 0
        target.unlink()
        print(f"deleted {target}")
        return 0
    if action == "get":
        value = get_value(args.key)
        print(value if not isinstance(value, list) else ", ".join(value))
        return 0
    if action == "set":
        path = set_value(args.key, args.value)
        print(f"set {args.key} = {get_value(args.key)}  →  {path}")
        return 0
    print(f"unknown config action {action!r}")
    return 2


def cmd_configure(cfg: Config, state: State, args: argparse.Namespace) -> int:
    """`lfs configure` — the guided setup, run once after installing.

    Every question has a default that is already correct, so pressing Enter
    through all of them leaves a working configuration. Nothing is written
    until the end, and the file is only created if something differs from the
    defaults.
    """
    from .config import init_config, set_value  # noqa: PLC0415
    from .ui.console import console as make_console  # noqa: PLC0415

    con = make_console()

    def ask(question: str, default: str, hint: str = "") -> str:
        if args.yes:
            return default
        if hint:
            con.print(f"[lfs.muted]{hint}[/]")
        try:
            answer = input(f"  {question} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            con.print("")
            return default
        return answer or default

    con.print("[lfs.primary]configure[/]  [lfs.muted]press Enter to accept each default[/]")
    con.print("")

    port = ask("port", str(cfg.network.port), "which port the share listens on")
    pin = ask("PIN (blank for none)", cfg.share.pin or "", "a 4-6 digit code guests must type")
    theme_name = ask("theme (dark / light / mono)", cfg.theme, "how the terminal app is drawn")
    folder = ask("starting folder (blank for the picker's default)", str(state.last_dir or ""), "where the picker opens")

    # Validate before writing anything: a config that cannot load is worse than
    # no configure step at all.
    try:
        port_value = int(port)
        if not 1 <= port_value <= 65535:
            raise ValueError
    except ValueError:
        con.print(f"[lfs.warning]![/] {port!r} is not a port number — keeping {cfg.network.port}")
        port_value = cfg.network.port

    if theme_name not in ("dark", "light", "mono"):
        con.print(f"[lfs.warning]![/] unknown theme {theme_name!r} — keeping {cfg.theme}")
        theme_name = cfg.theme

    init_config(force=False)
    set_value("network.port", str(port_value))
    set_value("share.pin", pin)
    set_value("theme", theme_name)
    # The starting folder is session state, not configuration: it is
    # remembered by the picker after every share, so it lives in state.json.
    if folder:
        state.remember_dir(folder)

    con.print("")
    con.print(f"[lfs.success]✓[/] configured  [lfs.muted]port {port_value} · theme {theme_name}[/]")
    con.print(f"[lfs.muted]  file: {config_path()}[/]")
    # `lfs` is the command people type; `./run` only exists inside a checkout
    # and is the thing that keeps getting confused with a stale copy, so the
    # hint points at the command that is always correct.
    con.print(f"[lfs.muted]  next: {APP_CMD}   (the menu)[/]")
    return 0


def cmd_theme(cfg: Config, state: State, args: argparse.Namespace) -> int:
    from .theme_switch import show_theme, switch_theme
    from .ui.console import current_theme, set_theme
    from .ui.theme import build_theme, cycle_theme

    name = args.name
    if name is None or name == "list":
        set_theme(build_theme(cfg.theme, cfg.accent))
        show_theme(current_theme(), preview=args.preview or name is None)
        return 0
    if name == "next":
        name = cycle_theme(cfg.theme)
    switch_theme(name, cfg, state)
    show_theme(current_theme(), preview=args.preview)
    return 0


def cmd_completions(cfg: Config, state: State, args: argparse.Namespace) -> int:
    from .completions import HELP, get_script

    if args.shell is None:
        print(HELP)
        return 0
    try:
        print(get_script(args.shell))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


def cmd_banner(cfg: Config, state: State, args: argparse.Namespace) -> int:
    from .ui import banner
    from .ui.console import current_theme

    if args.loop:
        try:
            banner.animate(current_theme(), frames=30, loop=True)
        except KeyboardInterrupt:
            return 0
        return 0
    banner.animate(current_theme(), frames=14)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--no-color" in argv:
        os.environ["NO_COLOR"] = "1"

    if "-V" in argv or "--version" in argv:
        _print_version()
        return 0

    argv = normalise_options(argv)
    command, remaining = split_argv(argv)
    if command == "help":
        build_parser().print_help()
        return 0
    if command == "version":
        _print_version()
        return 0

    command_parsers = build_command_parsers()
    if command is not None:
        parser = command_parsers[command]
        args = parser.parse_args(remaining)
        args.command = "menu" if command == "menu" else command
        if command == "menu":
            args.command = "menu"
            args.initial_mode = getattr(args, "mode", None) or ""
        debug = bool(getattr(args, "debug", False))
        try:
            cfg = _apply_cli_overrides(load_config(), args)
            _setup_console(cfg, args)
            state = State.load()
            handler = {
                "menu": cmd_menu,
                "doctor": cmd_doctor,
                "config": cmd_config,
                "configure": cmd_configure,
                "theme": cmd_theme,
                "completions": cmd_completions,
                "completion": cmd_completions,
                "banner": cmd_banner,
                "upgrade": cmd_upgrade,
                "update": cmd_upgrade,
                "uninstall": cmd_uninstall,
            }[command]
            return handler(cfg, state, args)
        except KeyboardInterrupt:
            return 130
        except SharerError as exc:
            return _handle_error(exc, debug=debug)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            return _handle_error(exc, debug=debug)

    args = build_parser().parse_args(argv)
    debug = bool(getattr(args, "debug", False))
    try:
        cfg = _apply_cli_overrides(load_config(), args)
        _setup_console(cfg, args)
        state = State.load()
        if cfg.theme and not state.theme:
            state.theme = cfg.theme
        if args.update or args.check_update:
            from .updater import run_update

            return run_update(check_only=args.check_update, yes=False)
        if args.paths:
            # A bare word that is not a file is usually a command this build
            # does not have -- an old install, most often. "Nothing to share"
            # is a true and useless answer to that; name the real problem.
            missing = [
                word
                for word in args.paths
                if not Path(word).expanduser().exists()
            ]
            # A single bare word -- no slash, no dot-slash -- is a command far
            # more often than a filename. "lfs nonsense" answered "Nothing to
            # share", which blames the picker for a command typo and hides that
            # an older install may be what answered. The original word is only
            # available here: _resolve_paths() makes every path absolute, so
            # this decision cannot move down into prepare_manifest().
            bare = [word for word in missing if "/" not in word and "\\" not in word]
            if len(args.paths) == 1 and bare:
                return _unknown_command(bare[0])
            if len(args.paths) == 1 and missing and _looks_like_a_command(missing[0]):
                return _unknown_command(missing[0])
            return cmd_share(cfg, state, args, _resolve_paths(args.paths))
        if args.mode:
            from .menu import run_menu

            return run_menu(
                config=cfg,
                state=state,
                animate=cfg.animate_banner and not args.no_anim,
                initial_mode=args.mode,
            )
        return cmd_menu(cfg, state, args)
    except KeyboardInterrupt:
        return 130
    except SharerError as exc:
        return _handle_error(exc, debug=debug)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the CLI must never dump a traceback
        return _handle_error(exc, debug=debug)


def run() -> None:  # pragma: no cover - console_scripts shim
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    run()
