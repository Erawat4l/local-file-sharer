"""The interactive main menu — what ``lfs`` shows on a bare terminal.

A single screen: header with network status, the mode selector, quick actions
for recent files and favourite folders, and shortcuts for theme/doctor/config.
It never clears the screen; each action prints below the previous one so the
scrollback stays readable on a phone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Group
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from . import APP_NAME, __version__, links, platforms
from .config import Config
from .errors import SharerError
from .fmt import human_size
from .network import local_endpoints, tailscale_status
from .picker import analyze
from .share import MODE_TAGLINES, MODES, ShareRequest, ShareRunner, ask_expiry, ask_pin, make_pin
from .state import State
from .ui import banner
from .ui.components import divider, error_panel, info_panel
from .ui.console import console, current_theme, screen_width
from .ui.picker_ui import run_picker

ACTIONS = [
    ("1", "Download", "download", "⬇️", "forced download · resumable · folders auto-zip"),
    ("2", "Serve", "serve", "🌐", "web file browser for this device or the whole folder"),
    ("3", "Stream", "stream", "▶️", "media player with instant seeking"),
    ("4", "Torrent", "torrent", "🌱", ".torrent + magnet · built-in tracker + webseed"),
    ("r", "Recent", "recent", "🕘", "re-share a recently shared file"),
    ("f", "Favourites", "favorites", "⭐", "jump to a favourite folder"),
    ("c", "Config", "config", "⚙️", "edit settings, theme and PIN"),
    ("d", "Doctor", "doctor", "🩺", "check dependencies, firewall and network"),
    ("t", "Theme", "theme", "🎨", "switch dark / light / neon"),
    ("u", "Update", "update", "⬆️", "check GitHub for a new release"),
    ("?", "Help", "help", "?", "keys, one-liners and troubleshooting"),
    ("q", "Quit", "quit", "⏏️", "leave the menu"),
]


@dataclass
class MenuContext:
    config: Config
    state: State
    animate: bool = True
    exit_code: int = 0
    finished: bool = False
    history: list[str] = field(default_factory=list)


def _status_lines(config: Config) -> list[tuple[str, str]]:
    endpoints = local_endpoints(prefer_interface=config.network.prefer_interface)
    ip = endpoints[0].ip if endpoints else "no LAN address"
    kind = endpoints[0].kind if endpoints else "offline"
    ts = tailscale_status()
    ts_text = ts.summary() if ts.installed else "not installed"
    totals = _state_totals(config)
    return [
        ("device", platforms.hostname()),
        ("network", f"{ip} ({kind})"),
        ("public", "looked up when you share"),
        ("tailscale", ts_text),
        ("theme", config.theme),
        ("sent so far", totals),
    ]


_STATE: State | None = None


def _state_totals(config: Config) -> str:
    if _STATE is None:
        return "—"
    totals = _STATE.totals()
    if not totals.get("bytes_sent"):
        return "nothing yet"
    return f"{human_size(totals['bytes_sent'])} · {totals.get('files_sent', 0)} files"


def render_menu(ctx: MenuContext) -> Group:
    theme = current_theme()
    width = screen_width()
    blocks: list = []

    head = Table.grid(expand=True)
    head.add_column()
    head.add_column(justify="right")
    left = Text()
    left.append(" LFS ", style=f"bold black on {theme.accent}")
    left.append(f" {APP_NAME}", style=theme.primary)
    left.append(f"  v{__version__}", style=theme.faint)
    right = Text(f"{time.strftime('%H:%M')} · {platforms.hostname()}", style=theme.faint)
    head.add_row(left, right)
    blocks.append(head)
    blocks.append(divider(width=min(width - 2, 76)))

    blocks.append(info_panel(_status_lines(ctx.config), title="this device", border=theme.border_soft))
    blocks.append(Text(""))

    table = Table.grid(padding=(0, 2))
    table.add_column(width=3, no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(overflow="ellipsis")
    table.add_row("", Text("SHARE", style=theme.info), Text("", style=theme.faint))
    for key, label, _action, icon, desc in ACTIONS[:4]:
        table.add_row(
            Text(key, style=theme.key),
            Text(f"{icon} {label}", style=theme.secondary),
            Text(desc, style=theme.faint),
        )
    table.add_row("", Text("RECENT & FAVOURITES", style=theme.info), Text("", style=theme.faint))
    for key, label, _action, icon, desc in ACTIONS[4:6]:
        table.add_row(
            Text(key, style=theme.key),
            Text(f"{icon} {label}", style=theme.secondary),
            Text(desc, style=theme.faint),
        )
    table.add_row("", Text("SETTINGS", style=theme.info), Text("", style=theme.faint))
    for key, label, _action, icon, desc in ACTIONS[6:]:
        table.add_row(
            Text(key, style=theme.key),
            Text(f"{icon} {label}", style=theme.secondary),
            Text(desc, style=theme.faint),
        )
    blocks.append(table)
    blocks.append(Text(""))
    blocks.append(
        Text(
            " pick a mode, then choose files · `lfs <path>` skips the menu · `lfs doctor` checks your setup",
            style=theme.faint,
        )
    )
    return Group(*blocks)


def _pick_and_share(ctx: MenuContext, mode: str) -> None:
    theme = current_theme()
    con = console()
    start = ctx.state.last_dir or platforms.storage_root()
    result = run_picker(start, icons=ctx.config.icons, animate=False, page_size=ctx.config.ui.page_size)
    if result.cancelled or not result.paths:
        con.print(Text(" nothing selected", style=theme.faint))
        return
    summary = analyze(result.paths)
    con.print(divider(width=min(screen_width() - 2, 70)))
    con.print(
        info_panel(
            [
                ("selected", f"{summary.files} files · {summary.folders} folders"),
                ("size", summary.size_text),
                ("status", summary.status_text()),
            ],
            title="ready to share",
            border=theme.accent,
        )
    )
    pin = ask_pin(ctx.config)
    expire = ask_expiry(ctx.config)
    request = ShareRequest(
        paths=result.paths,
        mode=mode,
        port=ctx.config.network.port,
        pin=pin,
        expire_minutes=expire,
        use_tailscale=None,
        use_cloudflare=None,
    )
    try:
        ShareRunner(request, config=ctx.config, state=ctx.state).run(wait=True)
    except SharerError as exc:
        con.print(error_panel(exc.message, hints=exc.all_hints(), detail=exc.detail or "", title=exc.title))
    except KeyboardInterrupt:
        con.print(Text(" stopped", style=theme.warning))


def _recent(ctx: MenuContext) -> None:
    theme = current_theme()
    con = console()
    recents = [r for r in ctx.state.recent_files() if Path(r["path"]).exists()]
    if not recents:
        con.print(Text(" no recent files yet — share something first", style=theme.faint))
        return
    for index, record in enumerate(recents[: ctx.config.ui.recent_limit], start=1):
        path = Path(record["path"])
        icon = "📁" if path.is_dir() else "📄"
        con.print(
            Text(f"  {index:>2}. {icon} {path}  ", style=theme.secondary)
            + Text(f"×{record.get('count', 1)}", style=theme.faint)
        )
    choice = Prompt.ask(
        Text(" share which? (Enter to cancel)", style=theme.accent), default="", console=con
    ).strip()
    if not choice.isdigit():
        return
    index = int(choice) - 1
    if not 0 <= index < len(recents):
        con.print(Text(" out of range", style=theme.danger))
        return
    path = Path(recents[index]["path"])
    if not path.exists():
        con.print(Text(f" {path} no longer exists", style=theme.danger))
        return
    try:
        ShareRunner(
            ShareRequest(paths=[path], mode="download", port=ctx.config.network.port),
            config=ctx.config,
            state=ctx.state,
        ).run(wait=True)
    except SharerError as exc:
        con.print(error_panel(exc.message, hints=exc.all_hints(), title=exc.title))


def _favorites(ctx: MenuContext) -> None:
    theme = current_theme()
    con = console()
    favs = ctx.state.favorite_folders()
    if not favs:
        con.print(
            Text(" no favourite folders yet — press `f` inside the picker to add one", style=theme.faint)
        )
        return
    for index, fav in enumerate(favs, start=1):
        con.print(
            Text(f"  {index:>2}. ★ {fav.get('label') or fav['path']}  ", style=theme.secondary)
            + Text(fav["path"], style=f"bold {theme.faint}")
        )
    choice = Prompt.ask(
        Text(" browse which? (Enter to cancel)", style=theme.accent), default="", console=con
    ).strip()
    if not choice.isdigit():
        return
    index = int(choice) - 1
    if 0 <= index < len(favs):
        result = run_picker(
            Path(favs[index]["path"]), icons=ctx.config.icons, page_size=ctx.config.ui.page_size
        )
        if result.paths and not result.cancelled:
            try:
                ShareRunner(
                    ShareRequest(paths=result.paths, mode="download", port=ctx.config.network.port),
                    config=ctx.config,
                    state=ctx.state,
                ).run(wait=True)
            except SharerError as exc:
                con.print(error_panel(exc.message, hints=exc.all_hints(), title=exc.title))


def _config_menu(ctx: MenuContext) -> None:
    theme = current_theme()
    con = console()
    from .config import get_value, init_config, set_value  # noqa: PLC0415 - local import keeps startup light

    rows = [
        ("theme", ctx.config.theme),
        ("accent", ctx.config.accent),
        ("port", str(ctx.config.network.port)),
        ("pin", "(set)" if ctx.config.share.pin else "(empty)"),
        ("auto-expire", f"{ctx.config.share.auto_expire_minutes} min"),
        ("uploads", "on" if ctx.config.share.allow_upload else "off"),
        ("parallel", str(ctx.config.speed.parallel_connections)),
        ("config file", str(ctx.config.source_files[-1] if ctx.config.source_files else "not created")),
    ]
    con.print(info_panel(rows, title="configuration"))
    con.print(
        Text(
            " 1) set a value  2) create the default config file  3) reset PIN  Enter) back", style=theme.muted
        )
    )
    choice = Prompt.ask(Text(" choice", style=theme.accent), default="", console=con).strip()
    if choice == "1":
        key = Prompt.ask(
            Text(" key (e.g. network.port, share.pin, theme)", style=theme.accent), default="", console=con
        ).strip()
        if not key:
            return
        value = Prompt.ask(Text(f" value for {key}", style=theme.accent), default="", console=con).strip()
        try:
            path = set_value(key, value)
        except SharerError as exc:
            con.print(error_panel(exc.message, hints=exc.all_hints(), title=exc.title))
            return
        con.print(Text(f" ✔ {key} = {get_value(key)}  →  {path}", style=theme.success))
    elif choice == "2":
        path, created = init_config()
        con.print(Text(f" {'✔ created' if created else 'already exists:'} {path}", style=theme.success))
    elif choice == "3":
        try:
            set_value("share.pin", "")
        except SharerError:
            pass
        con.print(Text(" ✔ PIN cleared", style=theme.success))


def _theme_menu(ctx: MenuContext) -> None:
    from .theme_switch import switch_theme  # noqa: PLC0415

    theme = current_theme()
    con = console()
    con.print(Text(" 1) dark   2) light   3) neon", style=theme.secondary))
    choice = Prompt.ask(Text(" theme", style=theme.accent), default="", console=con).strip()
    mapping = {"1": "dark", "2": "light", "3": "neon", "d": "dark", "l": "light", "n": "neon"}
    name = mapping.get(choice)
    if name:
        switch_theme(name, ctx.config, ctx.state)


def _help(ctx: MenuContext) -> None:
    from .ui.components import HELP_MAIN  # noqa: PLC0415

    console().print(HELP_MAIN)


def run_menu(*, config: Config, state: State, animate: bool = True, initial_mode: str = "") -> int:
    """Main loop. Returns a process exit code."""
    global _STATE
    _STATE = state
    ctx = MenuContext(config=config, state=state, animate=animate)
    con = console()
    theme = current_theme()
    if animate:
        banner.animate(theme, frames=14)
    con.print(render_menu(ctx))
    if state.first_run():
        con.print(
            Text(
                " first run! try `lfs doctor` to check your setup, or press 1 to pick files.",
                style=theme.info,
            )
        )
        state.mark_started()

    while True:
        try:
            answer = (
                Prompt.ask(
                    Text(" what would you like to do?", style=theme.accent),
                    default=initial_mode or "1",
                    console=con,
                    show_default=bool(initial_mode),
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            con.print(Text(" bye 👋", style=theme.muted))
            return 0
        initial_mode = ""
        action = _resolve_action(answer)
        if action is None:
            con.print(Text(f" unknown choice {answer!r} — press ? for help", style=theme.warning))
            continue
        if action in MODES:
            _pick_and_share(ctx, action)
        elif action == "recent":
            _recent(ctx)
        elif action == "favorites":
            _favorites(ctx)
        elif action == "config":
            _config_menu(ctx)
        elif action == "doctor":
            from .doctor import run_doctor  # noqa: PLC0415

            run_doctor()
        elif action == "theme":
            _theme_menu(ctx)
        elif action == "update":
            from .updater import run_update  # noqa: PLC0415

            try:
                run_update(yes=False)
            except SharerError as exc:
                con.print(error_panel(exc.message, hints=exc.all_hints(), title=exc.title))
        elif action == "help":
            _help(ctx)
        elif action == "quit":
            con.print(Text(" bye 👋", style=theme.muted))
            return 0
        ctx.config = _reload_config(ctx.config)
        con.print(Text(""))
        con.print(render_menu(ctx), no_wrap=False)


def _reload_config(config: Config) -> Config:
    try:
        from .config import load_config  # noqa: PLC0415

        return load_config()
    except SharerError:
        return config


def _resolve_action(answer: str) -> str | None:
    lookup = {key: action for key, _l, action, _i, _d in ACTIONS}
    if answer in lookup:
        return lookup[answer]
    lowered = answer.lower()
    if lowered in MODES:
        return lowered
    if lowered in ("exit", "e"):
        return "quit"
    return None


def quick_actions_summary(config: Config, state: State) -> str:
    totals = state.totals()
    endpoints = local_endpoints()
    ip = endpoints[0].ip if endpoints else "offline"
    return (
        f"{ip} · {config.theme} · "
        f"{human_size(totals.get('bytes_sent', 0))} sent · "
        f"{links.parallel_hint(config.speed.parallel_connections).split('.')[0]}"
    )


_ = (Confirm, make_pin, MODE_TAGLINES)
