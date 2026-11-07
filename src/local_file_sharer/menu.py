"""The interactive main menu — what ``lfs`` shows on a bare terminal.

A single screen: header with network status, the mode selector, quick actions
for recent files and favourite folders, and shortcuts for theme/doctor/config.
It never clears the screen; each action prints below the previous one so the
scrollback stays readable on a phone.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Group
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from . import APP_NAME, __version__, links, platforms
from .config import Config
from .errors import SharerError
from .fmt import cell_len, fit, human_size
from .network import local_endpoints, tailscale_status
from .picker import FILE_ICON, FOLDER_ICON, analyze
from .share import MODE_TAGLINES, MODES, ShareRequest, ShareRunner, ask_expiry, ask_pin, make_pin
from .state import State
from .ui import banner
from .ui.components import divider, error_panel, info_panel
from .ui.console import console, current_theme, screen_width, wrap_command
from .ui.picker_ui import run_picker

# Icons are one cell wide. A double-width emoji here pushed every row's
# description out by a column on a phone, and Termux often has no emoji font, so
# the menu came out as a column of tofu boxes.
# The menu is five numbered choices, not twelve. Everything a user needs to
# share a file is in those five; settings, doctor, theme and update live behind
# `?` so the screen answers one question instead of listing a control panel.
# Every icon is one cell wide, so no row can shift the name column. This used to
# carry real colour emoji with a single-cell fallback, chosen per terminal;
# the emoji are gone because the product is monochrome. The two-column form is
# kept so a caller can still force the plain set.
#: Icons are monochrome glyphs, not colour emoji. The product has no colour in
#: it anywhere -- terminal, web pages, README images -- and a row of emoji put
#: eight different hues into a black-and-white screen. These are all single
#: cell, all from the basic block/shape range, and all one weight, so the column
#: reads as a set of marks rather than a set of pictures.
# Every glyph below was checked against the font the app ships with
# (JetBrainsMono). A mark the font does not have renders as a tofu box, which
# is what ⤓ ◷ ★ ⚙ ✚ ◑ did -- six empty rectangles down the icon column -- so the
# set is limited to what the face actually draws.
ACTIONS = [
    ("1", "Download", "download", "↓", "v", "Forced Download · Resumable · Folders Auto-Zip"),
    ("2", "Serve", "serve", "◈", "o", "Web File Browser For This Device Or The Whole Folder"),
    ("3", "Stream", "stream", "▶", ">", "Media Player With Instant Seeking"),
    ("4", "Torrent", "torrent", "◆", "*", ".Torrent + Magnet · Built-In Tracker + Webseed"),
]

#: Shown by `?` — the same actions plus the maintenance commands.
EXTRA_ACTIONS = [
    ("r", "Recent", "recent", "●", "o", "Re-Share A Recently Shared File"),
    ("f", "Favourites", "favorites", "◆", "*", "Jump To A Favourite Folder"),
    ("c", "Config", "config", "≡", "=", "Edit Settings, Theme And PIN"),
    ("d", "Doctor", "doctor", "✓", "+", "Check Dependencies, Firewall And Network"),
    ("t", "Theme", "theme", "○", "o", "Switch Dark / Light / Mono"),
    ("u", "Update", "update", "↑", "^", "Install The Newest Code From GitHub"),
    ("?", "Help", "help", "?", "?", "Keys, One-Liners And Troubleshooting"),
    ("q", "Quit", "quit", "⏻", "x", "Leave The Menu"),
]

#: The menu is grouped like the design: what you came to do, then the two
#: shortlists, then the settings that used to sit in a second screen.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Share", ("1", "2", "3", "4")),
    ("Shortcuts", ("r", "f")),
    ("Settings", ("c", "d", "t", "u", "?", "q")),
)
_BY_KEY = {
    key: (key, label, action, icon, plain, desc)
    for key, label, action, icon, plain, desc in ACTIONS + EXTRA_ACTIONS
}

ALL_ACTIONS = ACTIONS + EXTRA_ACTIONS


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


def icon_for(icon: str, plain: str) -> str:
    """The row's icon. Both arguments are monochrome now; kept for callers.

    ``LFS_ASCII_ICONS=1`` still selects the plainer of the two, for a terminal
    whose font has no block or geometric shapes.
    """
    import os  # noqa: PLC0415 - only needed here

    if os.environ.get("LFS_ASCII_ICONS"):
        return plain
    return icon


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

    name = f" {APP_NAME}  v{__version__}"
    right_text = f"{time.strftime('%H:%M')} · {platforms.hostname()}"
    head = Text()
    # The gif's badge is a cyan block with dark letters. theme.accent is
    # "bold bright_cyan", which is a foreground style, so the background has to
    # be named explicitly or the chip renders as plain text.
    head.append(" LFS ", style=f"bold black on {theme.accent_name}")
    if len(name) + 8 + len(right_text) <= width:
        # wide enough for one line: name on the left, clock on the right
        padding = max(1, width - len(name) - len(right_text) - 8)
        head.append(name, style=theme.primary)
        head.append(" " * padding)
        head.append(right_text, style=theme.faint)
    else:
        # a phone: name first, clock on its own line rather than squeezed out
        head.append(name, style=theme.primary)
        head.append("\n" + right_text, style=theme.faint)
    blocks.append(head)
    # the rule runs the full width, like the design: a short one under a long
    # header looked like a stray line
    blocks.append(divider(width=max(20, width - 1)))

    blocks.append(info_panel(_status_lines(ctx.config), title="this device", border=theme.border_soft))
    blocks.append(Text(""))

    # Four columns, like the design: the key you press, its icon, the name, and
    # what it does. The description shares one left edge on purpose — without a
    # fixed name column it started wherever the longest label happened to end,
    # which is what made the old menu look ragged.
    name_width = max(len(_BY_KEY[key][1]) for _entries in SECTIONS for key in _entries[1])
    # a phone cannot hold "name + description" on one line, and letting the
    # description wrap doubles the height of the menu; drop it instead
    show_desc = width >= 62
    show_sections = width >= 44
    for heading, keys in SECTIONS:
        if show_sections:
            blocks.append(Text(f" {heading}", style=f"bold {theme.accent}"))
        # Rows are laid out by hand rather than with Table.grid. A Table with
        # fixed column widths drops the columns that do not fit, and on a
        # 60-column phone it dropped the KEY AND THE ICON -- every row read
        # " Download  forced download ...", with nothing to press. Building the
        # line explicitly means the two columns that matter are always there
        # and only the description, which is optional, is trimmed.
        for key in keys:
            _key, label, _action, icon, plain, desc = _BY_KEY[key]
            row = Text()
            row.append(f"{key:>2}", style=theme.key)
            row.append(" ")
            icon_text = icon_for(icon, plain)
            row.append(icon_text, style=theme.secondary)
            # a double-width emoji occupies two cells; pad so names line up
            row.append(" " * max(1, 3 - cell_len(icon_text)))
            row.append(label, style=theme.secondary)
            row.append(" " * max(1, name_width - cell_len(label) + 1))
            if show_desc and desc:
                # -1 for the terminal's own last column, which some emulators
                # use to avoid wrapping.
                used = cell_len(row.plain)
                room = width - used - 1
                if room > 8:
                    row.append(fit(desc, room), style=theme.faint)
            blocks.append(row)
    blocks.append(Text(""))
    for line in wrap_command(
        "`lfs <path>` shares straight away · `?` for more commands",
        indent=" ",
        width=width,
    ):
        blocks.append(Text(line, style=theme.faint))
    return Group(*blocks)


def share_paths(ctx: MenuContext, paths: Sequence[Path], mode: str = "download") -> None:
    """Show the selection summary and run the share — shared by both entry points."""
    theme = current_theme()
    con = console()
    summary = analyze(paths)
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
        paths=list(paths),
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


def _pick_and_share(ctx: MenuContext, mode: str) -> None:
    theme = current_theme()
    start = platforms.start_dir(ctx.state.last_dir)
    result = run_picker(start, icons=ctx.config.icons, animate=False, page_size=ctx.config.ui.page_size)
    if result.cancelled or not result.paths:
        console().print(Text(" nothing selected", style=theme.faint))
        return
    share_paths(ctx, result.paths, mode)


def _recent(ctx: MenuContext) -> None:
    theme = current_theme()
    con = console()
    recents = [r for r in ctx.state.recent_files() if Path(r["path"]).exists()]
    if not recents:
        con.print(Text(" no recent files yet — share something first", style=theme.faint))
        return
    for index, record in enumerate(recents[: ctx.config.ui.recent_limit], start=1):
        path = Path(record["path"])
        icon = FOLDER_ICON if path.is_dir() else FILE_ICON
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
    con.print(Text(" 1) dark   2) light   3) mono", style=theme.secondary))
    choice = Prompt.ask(Text(" theme", style=theme.accent), default="", console=con).strip()
    mapping = {"1": "dark", "2": "light", "3": "mono", "d": "dark", "l": "light", "m": "mono"}
    name = mapping.get(choice)
    if name:
        switch_theme(name, ctx.config, ctx.state)


def render_extra_commands() -> Group:
    """The commands that do not fit on the five-row menu."""
    theme = current_theme()
    table = Table.grid(padding=(0, 2))
    table.add_column(width=3, no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")
    for key, label, _action, _icon, _plain, desc in EXTRA_ACTIONS:
        table.add_row(
            Text(key, style=theme.key),
            Text(label, style=theme.secondary),
            Text(desc, style=theme.faint),
        )
    return Group(
        Text(" more commands", style=theme.info),
        table,
        Text(""),
        Text(" lfs <path> shares immediately · ? again for the full help", style=theme.faint),
    )


def _help(ctx: MenuContext) -> None:
    from .ui.components import HELP_MAIN  # noqa: PLC0415

    console().print(render_extra_commands())
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
    # the menu prints five rows, but the other keys keep working: the screen is
    # short, the vocabulary is not
    lookup = {entry[0]: entry[2] for entry in ALL_ACTIONS}
    # By name as well as by key: typing "quit" or "doctor" should work, and it
    # did not -- only the single-letter keys and MODES were accepted, so
    # "quit" fell through to "unknown choice" while "q" worked.
    names = {entry[2]: entry[2] for entry in ALL_ACTIONS}
    answer = answer.lower()
    if answer in lookup:
        return lookup[answer]
    if answer in names:
        return names[answer]
    if answer in MODES:
        return answer
    if answer in ("exit", "e", "bye", "stop"):
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
