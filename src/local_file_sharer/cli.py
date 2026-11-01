"""Command line entry point: the ``start`` command.

```
start                      interactive menu
start <path> [...]         share files/folders right away
start --mode stream        pick media and open the player
start --mode torrent       build a .torrent + magnet with a built-in tracker
start doctor               diagnostics
start config show|init|…
start theme dark|light|neon
start completions fish|bash|powershell
start --update             self-update from GitHub
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

from . import APP_NAME, APP_TAGLINE, REPO_URL, __version__, platforms
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
    "theme",
    "completions",
    "completion",
    "banner",
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
    parser.add_argument("--theme", choices=("dark", "light", "neon"), default=None, help="colour theme")
    parser.add_argument("--no-anim", action="store_true", help="static banner (no shimmer)")
    parser.add_argument(
        "--icons", dest="icons", action="store_true", default=None, help="emoji icons in the picker"
    )
    parser.add_argument("--no-icons", dest="icons", action="store_false", help="plain names in the picker")
    parser.add_argument("--no-color", action="store_true", help="disable colour (same as NO_COLOR=1)")
    parser.add_argument("--interface", default="", help="prefer a network interface, e.g. wlan0")
    parser.add_argument("--debug", action="store_true", help="show the full traceback on errors")
    parser.add_argument("--json", action="store_true", help="machine-readable output where supported")
    parser.add_argument("-V", "--version", action="version", version=f"{APP_NAME} {__version__}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="start",
        description=f"{APP_NAME} — {APP_TAGLINE}",
        epilog=(
            "commands:\n"
            "  start                     open the interactive menu\n"
            "  start <path> [...]        share files/folders right away\n"
            "  start doctor              check dependencies, firewall and network\n"
            "  start config show         print the effective configuration\n"
            "  start theme neon          switch theme (dark / light / neon)\n"
            "  start completions fish    print a shell completion script\n"
            "  start --update            self-update from GitHub\n"
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
        prog="start menu", parents=[common], description="open the interactive menu"
    )

    doctor = argparse.ArgumentParser(
        prog="start doctor", parents=[common], description="check dependencies, firewall and network"
    )
    doctor.add_argument("--fast", action="store_true", help="skip tool and network probes")
    parsers["doctor"] = doctor

    config = argparse.ArgumentParser(
        prog="start config", parents=[common], description="manage the configuration file"
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

    theme = argparse.ArgumentParser(
        prog="start theme", parents=[common], description="switch or preview the colour theme"
    )
    theme.add_argument("name", nargs="?", choices=("dark", "light", "neon", "list", "next"), default=None)
    theme.add_argument("--preview", action="store_true", help="show a colour preview")
    parsers["theme"] = theme

    completions = argparse.ArgumentParser(
        prog="start completions", parents=[common], description="print a shell completion script"
    )
    completions.add_argument("shell", nargs="?", choices=_SHELLS, default=None)
    parsers["completions"] = completions
    parsers["completion"] = completions

    banner = argparse.ArgumentParser(
        prog="start banner", parents=[common], description="preview the animated banner"
    )
    banner.add_argument("--loop", action="store_true", help="loop until Ctrl+C")
    parsers["banner"] = banner

    parsers["version"] = argparse.ArgumentParser(
        prog="start version", parents=[common], description="print the version"
    )
    parsers["help"] = argparse.ArgumentParser(
        prog="start help", parents=[common], description="show the main help"
    )
    return parsers


#: Options that consume the following token as their value.
_VALUE_OPTIONS = {"-p", "--port", "-m", "--mode", "--theme", "--expire", "--interface", "--pin"}


def split_argv(argv: list[str]) -> tuple[str | None, list[str]]:
    """Return ``(subcommand, remaining)`` when a bare word names a subcommand.

    ``start --port 9000 theme neon`` → ``("theme", ["--port", "9000", "neon"])``.
    Anything that looks like a path stops the scan, so
    ``start ./doctor`` still shares a file called *doctor*.
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
        if token in SUBCOMMAND_NAMES:
            return token, argv[:index] + argv[index + 1 :]
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


def _print_version() -> None:
    print(f"{APP_NAME} {__version__}")
    print(f"python {sys.version.split()[0]} · {platforms.platform_key()}")


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
def cmd_menu(cfg: Config, state: State, args: argparse.Namespace) -> int:
    from .menu import run_menu

    return run_menu(config=cfg, state=state, animate=cfg.animate_banner and not args.no_anim)


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

    return run_doctor(as_json=args.json, fast=args.fast)


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
                "theme": cmd_theme,
                "completions": cmd_completions,
                "completion": cmd_completions,
                "banner": cmd_banner,
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
