"""Interactive file picker.

Scroll-friendly by design: it never clears the screen, keeps a rolling window
of rows that fits the terminal (phone-friendly), and only re-renders the last
frame through ``rich.Live`` so a long browsing session leaves a readable
history in the scrollback.

Keys
----
``↑/↓`` or ``j/k``   move            ``PgUp/PgDn``   page
``Enter``            open folder / pick highlighted
``Space``            toggle selection
``6,7,8`` / ``6-8``  select by number (type then Enter)
``a``                select all          ``c`` clear
``s``                start sharing the selection
``/``                fuzzy search        ``esc`` clear search
``f``                favourite this folder
``d``/``h``/``b``    drives / home / up
``t``                theme               ``?`` help          ``q`` quit
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rich.align import Align
from rich.console import Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import platforms
from ..fmt import fit, human_size
from ..picker import (
    Entry,
    SelectionSummary,
    analyze,
    common_root,
    fuzzy_filter,
    icon_for,
    list_entries,
    parse_selection,
    resolve_user_path,
)
from . import banner
from .components import HELP_PICKER, KeyHint, divider, empty_state, footer_keys, stat_chip
from .console import console, current_theme, screen_height, screen_width


@dataclass
class PickerResult:
    paths: list[Path] = field(default_factory=list)
    summary: SelectionSummary = field(default_factory=SelectionSummary)
    cancelled: bool = False

    @property
    def empty(self) -> bool:
        return not self.paths


@dataclass
class PickerState:
    cwd: Path
    entries: list[Entry] = field(default_factory=list)
    cursor: int = 0
    offset: int = 0
    selected: dict[str, Path] = field(default_factory=dict)
    query: str = ""
    typing: str = ""
    typing_mode: bool = False
    show_hidden: bool = False
    message: str = ""
    message_style: str = "muted"
    page_size: int = 14
    favorite: bool = False
    root: Path | None = None

    def reload(self, *, icons: bool = True) -> None:
        self.entries = list_entries(self.cwd, show_hidden=self.show_hidden, icons=icons)
        self.cursor = min(self.cursor, max(0, len(self.visible()) - 1))
        self.offset = 0
        self.ensure_visible()

    def visible(self) -> list[Entry]:
        return fuzzy_filter(self.entries, self.query) if self.query else list(self.entries)

    def current(self) -> Entry | None:
        visible = self.visible()
        if not visible:
            return None
        return visible[min(self.cursor, len(visible) - 1)]

    def ensure_visible(self) -> None:
        visible = self.visible()
        if not visible:
            self.cursor = 0
            self.offset = 0
            return
        self.cursor = max(0, min(self.cursor, len(visible) - 1))
        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + self.page_size:
            self.offset = self.cursor - self.page_size + 1
        max_offset = max(0, len(visible) - self.page_size)
        self.offset = max(0, min(self.offset, max_offset))

    def move(self, delta: int) -> None:
        visible = self.visible()
        if not visible:
            return
        self.cursor = (self.cursor + delta) % len(visible)
        self.ensure_visible()

    def toggle(self) -> None:
        entry = self.current()
        if entry is None:
            return
        key = str(entry.path)
        if key in self.selected:
            del self.selected[key]
            self.flash(f"removed {entry.name}", "muted")
        else:
            self.selected[key] = entry.path
            self.flash(f"added {entry.name}", "success")

    def select_indices(self, indices: Sequence[int]) -> None:
        visible = self.visible()
        for index in indices:
            if 0 <= index < len(visible):
                entry = visible[index]
                self.selected[str(entry.path)] = entry.path

    def clear(self) -> None:
        self.selected.clear()
        self.flash("selection cleared", "muted")

    def flash(self, message: str, style: str = "muted") -> None:
        self.message = message
        self.message_style = style
        self._message_at = time.time()


# ── rendering ───────────────────────────────────────────────────────────────
def _breadcrumb(state: PickerState, width: int) -> Text:
    theme = current_theme()
    parts = list(state.cwd.parts)
    if platforms.WINDOWS and parts and parts[0].endswith("\\"):
        parts[0] = parts[0].rstrip("\\")
    tail = parts[-4:] if len(parts) > 4 else parts
    text = Text()
    if len(parts) > 4:
        text.append("…", style=theme.faint)
        text.append(" / ", style=theme.faint)
    for index, part in enumerate(tail):
        style = theme.primary if index == len(tail) - 1 else theme.muted
        text.append(str(part), style=style)
        if index < len(tail) - 1:
            text.append(" / ", style=theme.faint)
    return Text(fit(text.plain, width), style=theme.muted)


def _row(index: int, entry: Entry, state: PickerState, width: int, *, active: bool) -> Text:
    theme = current_theme()
    selected = str(entry.path) in state.selected
    number = f"{index:>3}"
    marker = "▸" if active else " "
    check = "●" if selected else ("◆" if entry.is_dir else "○")
    size = entry.display_size
    name_width = max(12, width - 30)
    name = fit(entry.name, name_width)
    text = Text()
    text.append(f"{marker} ", style=theme.accent if active else theme.faint)
    text.append(f"{number} ", style=theme.faint)
    text.append(f"{check} ", style=theme.success if selected else theme.faint)
    text.append(f"{entry.icon} " if entry.icon else "", style=theme.secondary)
    base_style = theme.selection if active else (theme.secondary if entry.readable else theme.danger)
    text.append(name, style=f"bold {theme.accent}" if (active and entry.is_dir) else base_style)
    if entry.is_dir:
        text.append("/", style=theme.faint)
    pad_width = max(0, width - 24 - len(name))
    text.append(" " * pad_width)
    text.append(size.rjust(10), style=theme.muted if entry.is_dir else theme.faint)
    if entry.link:
        text.append(" ↗️", style=theme.info)
    return text


def _selection_bar(state: PickerState, width: int) -> RenderableType:
    theme = current_theme()
    if not state.selected:
        return Group(
            Text("nothing selected · Space toggles a row · `a` selects all", style=theme.faint),
            Text(""),
        )
    paths = list(state.selected.values())
    summary = analyze(paths, max_entries=20_000)
    chips = [
        stat_chip("📄", f"{summary.files}", theme.accent),
        stat_chip("📁", f"{summary.folders}", theme.info),
        stat_chip("💾", summary.size_text, theme.success),
        stat_chip(
            "✅" if summary.shareable else "⚠️",
            summary.status_text(),
            theme.success if summary.shareable else theme.warning,
        ),
    ]
    group = Table.grid(padding=(0, 1))
    group.add_row(*chips)
    if state.message:
        return Group(group, Text(state.message, style=theme.muted))
    return group


def _search_box(state: PickerState) -> Text:
    theme = current_theme()
    if state.typing_mode:
        return Text(f" select › {state.typing}_   (Enter to apply, Esc to cancel)", style=theme.warning)
    if state.query:
        return Text(f" 🔎 {state.query}   ({len(state.visible())} matches, Esc to clear)", style=theme.info)
    return Text("")


def _footer() -> RenderableType:
    return footer_keys(
        [
            ("↑↓", "move"),
            ("Space", "pick"),
            ("Enter", "open"),
            ("a", "all"),
            ("s", "share"),
            ("/", "search"),
            ("f", "fav"),
            ("h", "home"),
            ("q", "quit"),
        ]
    )


def render(state: PickerState, *, width: int | None = None, height: int | None = None) -> RenderableType:
    theme = current_theme()
    width = width or screen_width()
    height = height or screen_height()
    # Reserve rows for the header/footer so nothing scrolls off a phone screen.
    rows_available = max(4, height - 12)
    state.page_size = min(state.page_size if state.page_size else rows_available, rows_available)

    visible = state.visible()
    state.ensure_visible()
    window = visible[state.offset : state.offset + state.page_size]
    lines: list[Text] = []
    for offset, entry in enumerate(window):
        index = state.offset + offset
        lines.append(_row(index + 1, entry, state, width - 4, active=(index == state.cursor)))
    if not visible:
        lines.append(Text("   (empty folder — press h to go up)", style=theme.faint))

    header = Table.grid(expand=True)
    header.add_column(justify="left")
    header.add_column(justify="right")
    header.add_row(
        banner.mini_header(theme, mode="picker", detail=str(state.cwd.name or state.cwd)),
        Text(f"{len(visible)} items · {state.offset + 1}-{state.offset + len(window)}", style=theme.faint),
    )

    body: list[RenderableType] = [
        header,
        _breadcrumb(state, width - 4),
        divider(width=width - 6),
        *lines,
        divider(width=width - 6),
    ]
    search = _search_box(state)
    if search.plain.strip():
        body.append(search)
    body.append(_selection_bar(state, width))
    body.append(_footer())
    if state.message and not state.selected:
        body.append(Text(state.message, style=theme.faint))

    fav = " ★" if state.favorite else ""
    title = Text.assemble(
        (" SELECT ", f"bold black on {theme.accent}"),
        (f"  {len(state.selected)} selected{fav}", theme.muted),
    )
    return Panel(
        Group(*body), title=title, title_align="left", border_style=theme.border, padding=(0, 1), expand=True
    )


# ── input handling ──────────────────────────────────────────────────────────
def _read_key() -> str:
    """Read one keypress cross-platform (Windows msvcrt, POSIX termios)."""
    if platforms.WINDOWS:  # pragma: no cover - Windows only
        import msvcrt  # noqa: PLC0415

        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {
                "H": "UP",
                "P": "DOWN",
                "K": "LEFT",
                "M": "RIGHT",
                "I": "PGUP",
                "Q": "PGDN",
                "G": "HOME",
                "O": "END",
                "S": "DELETE",
            }.get(code, "")
        if char == "\x03":
            raise KeyboardInterrupt
        if char == "\x1b":
            return "ESC"
        if char in ("\r", "\n"):
            return "ENTER"
        if char == "\x08":
            return "BACKSPACE"
        return char
    import sys  # noqa: PLC0415
    import termios  # noqa: PLC0415
    import tty  # noqa: PLC0415

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        char = sys.stdin.read(1)
        if char != "\x1b":
            return _normalise(char)
        # Escape sequence: read the rest without blocking for long.
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        import select  # noqa: PLC0415

        ready, _, _ = select.select([sys.stdin], [], [], 0.02)
        if not ready:
            return "ESC"
        tty.setcbreak(fd)
        rest = sys.stdin.read(1)
        if rest not in ("[", "O"):
            return "ESC"
        code = sys.stdin.read(1)
        if code.isdigit():
            # e.g. \x1b[5~ or \x1b[6~
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            import select as _select  # noqa: PLC0415

            ready, _, _ = _select.select([sys.stdin], [], [], 0.02)
            if ready:
                tty.setcbreak(fd)
                sys.stdin.read(1)
        return {
            "A": "UP",
            "B": "DOWN",
            "C": "RIGHT",
            "D": "LEFT",
            "H": "HOME",
            "F": "END",
            "5": "PGUP",
            "6": "PGDN",
            "1": "HOME",
            "4": "END",
            "3": "DELETE",
        }.get(code, "")
    finally:
        if not platforms.WINDOWS:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _normalise(char: str) -> str:
    if char in ("\r", "\n"):
        return "ENTER"
    if char == "\x7f":
        return "BACKSPACE"
    if char == "\x03":
        raise KeyboardInterrupt
    if char == "\x04":
        return "QUIT"
    return char


def _fallback_prompt(state: PickerState) -> str:
    """Line-mode fallback for pipes/CI where raw keys are unavailable."""
    console().print(Text("Interactive keys need a real terminal — entering line mode.", style="lfs.warning"))
    view = state.visible()
    for index, entry in enumerate(view[:40], start=1):
        console().print(
            Text(f"  {index:>3} {'📁' if entry.is_dir else entry.icon} {entry.name}  ({entry.display_size})")
        )
    return console().input(
        Text(" numbers (6,7,8 / 6-8) or path, Enter to share, q to quit › ", style="lfs.accent")
    )


def run_picker(
    start: Path | None = None,
    *,
    icons: bool = True,
    favorites: set[str] | None = None,
    animate: bool = False,
    page_size: int = 14,
) -> PickerResult:
    """Run the interactive picker; returns the chosen paths and their summary."""
    theme = current_theme()
    cwd = platforms.normalize_path(start or platforms.storage_root())
    if not cwd.is_dir():
        cwd = platforms.home_dir()
    favorites = favorites or set()
    state = PickerState(cwd=cwd, page_size=page_size, favorite=str(cwd) in favorites)
    try:
        state.reload(icons=icons)
    except Exception as exc:  # noqa: BLE001 - fall back to home
        console().print(Text(f"⚠️  {exc}", style=theme.warning))
        state.cwd = platforms.home_dir()
        state.reload(icons=icons)

    con = console()
    if not con.is_terminal:
        return _run_line_mode(state, icons=icons)

    if animate:
        banner.animate(theme, frames=8)

    result = PickerResult()
    with Live(render(state), console=con, auto_refresh=False, transient=False, screen=False) as live:
        while True:
            live.update(render(state), refresh=True)
            key = _read_key()
            if key in ("QUIT", "q", "ESC2"):
                result.cancelled = True
                return result
            if state.typing_mode:
                if key == "ENTER":
                    if state.typing:
                        try:
                            indices = parse_selection(state.typing, len(state.visible()))
                        except Exception as exc:  # noqa: BLE001
                            state.flash(str(exc), "danger")
                        else:
                            state.select_indices(indices)
                            state.flash(f"added {len(indices)} item(s)", "success")
                    state.typing_mode = False
                    state.typing = ""
                elif key == "ESC":
                    state.typing_mode = False
                    state.typing = ""
                elif key == "BACKSPACE":
                    state.typing = state.typing[:-1]
                elif len(key) == 1 and key.isprintable():
                    state.typing += key
                continue
            if key == "UP":
                state.move(-1)
            elif key == "DOWN":
                state.move(1)
            elif key == "PGUP":
                state.move(-state.page_size)
            elif key == "PGDN":
                state.move(state.page_size)
            elif key == "HOME":
                state.cursor = 0
                state.ensure_visible()
            elif key == "END":
                state.cursor = max(0, len(state.visible()) - 1)
                state.ensure_visible()
            elif key == " ":
                state.toggle()
            elif key == "ENTER":
                entry = state.current()
                if entry is None:
                    continue
                if entry.is_dir:
                    state.cwd = entry.path
                    state.cursor = 0
                    state.offset = 0
                    state.query = ""
                    state.favorite = str(entry.path) in favorites
                    try:
                        state.reload(icons=icons)
                    except Exception as exc:  # noqa: BLE001
                        state.flash(str(exc), "danger")
                        state.cwd = state.cwd.parent
                        state.reload(icons=icons)
                else:
                    state.selected.setdefault(str(entry.path), entry.path)
                    state.flash(f"added {entry.name}", "success")
            elif key in ("j",):
                state.move(1)
            elif key in ("k",):
                state.move(-1)
            elif key in ("g", "h"):
                state.cwd = state.cwd.parent
                state.cursor = 0
                state.query = ""
                state.favorite = str(state.cwd) in favorites
                try:
                    state.reload(icons=icons)
                except Exception:  # noqa: BLE001
                    state.cwd = platforms.home_dir()
                    state.reload(icons=icons)
            elif key == "H":
                state.cwd = platforms.home_dir()
                state.query = ""
                state.reload(icons=icons)
            elif key == "b":
                state.cwd = state.cwd.parent
                state.reload(icons=icons)
            elif key == "a":
                state.selected = {str(e.path): e.path for e in state.visible()}
                state.flash(f"selected all {len(state.selected)} items", "success")
            elif key == "c":
                state.clear()
            elif key == "d":
                _drive_menu(state, icons=icons)
            elif key == "f":
                state.favorite = not state.favorite
                if state.favorite:
                    favorites.add(str(state.cwd))
                else:
                    favorites.discard(str(state.cwd))
                state.flash("★ favourite added" if state.favorite else "favourite removed", "info")
            elif key == "/":
                query = console().input(
                    Text(" fuzzy search (Enter to keep, empty clears) › ", style="lfs.accent")
                )
                state.query = query.strip()
                state.cursor = 0
                state.ensure_visible()
            elif key == "?":
                console().print(HELP_PICKER)
                console().input(Text(" press Enter to go back ", style="lfs.faint"))
            elif key == "t":
                state.flash("theme changes apply on restart — try `lfs theme neon`", "info")
            elif key == "s":
                if not state.selected:
                    state.flash("pick something first (Space / a / numbers)", "warning")
                    continue
                result.paths = list(state.selected.values())
                result.summary = analyze(result.paths)
                return result
            elif key.isdigit() or key in (",", "-"):
                state.typing_mode = True
                state.typing = key


def _drive_menu(state: PickerState, *, icons: bool) -> None:
    drives = platforms.list_drives()
    if not drives:
        state.flash("no extra drives found", "muted")
        return
    console().print()
    for index, drive in enumerate(drives, start=1):
        console().print(Text(f"  {index}. {drive.icon} {drive.label}  {drive.detail}", style="lfs.secondary"))
    choice = console().input(Text(" drive number (or Enter to cancel) › ", style="lfs.accent")).strip()
    if choice.isdigit() and 1 <= int(choice) <= len(drives):
        state.cwd = drives[int(choice) - 1].path
        state.cursor = 0
        state.query = ""
        try:
            state.reload(icons=icons)
        except Exception as exc:  # noqa: BLE001
            state.flash(str(exc), "danger")


def _run_line_mode(state: PickerState, *, icons: bool) -> PickerResult:
    result = PickerResult()
    while True:
        try:
            answer = _fallback_prompt(state).strip()
        except (EOFError, KeyboardInterrupt):
            result.cancelled = True
            return result
        if answer.lower() in ("q", "quit", "exit"):
            result.cancelled = True
            return result
        if not answer:
            if state.selected:
                result.paths = list(state.selected.values())
                result.summary = analyze(result.paths)
                return result
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(state.visible()):
            entry = state.visible()[int(answer) - 1]
            if entry.is_dir:
                state.cwd = entry.path
                state.reload(icons=icons)
            else:
                state.selected[str(entry.path)] = entry.path
                return PickerResult(
                    paths=list(state.selected.values()), summary=analyze(list(state.selected.values()))
                )
            continue
        try:
            indices = parse_selection(answer, len(state.visible()))
            state.select_indices(indices)
            continue
        except Exception:  # noqa: BLE001 - not a number list: treat as a path
            path = resolve_user_path(answer, base=state.cwd)
            if path.is_dir():
                state.cwd = path
                state.reload(icons=icons)
            elif path.exists():
                state.selected[str(path)] = path
            else:
                console().print(Text(f"no such file or folder: {path}", style="lfs.danger"))


def pick_paths(start: Path | None = None, **kwargs) -> list[Path]:
    return run_picker(start, **kwargs).paths


def summarize(paths: Sequence[Path]) -> SelectionSummary:
    return analyze(paths)


def describe_path(path: Path, icons: bool = True) -> str:
    icon = icon_for(path, icons=icons)
    try:
        if path.is_dir():
            return f"{icon} {path.name}/"
        return f"{icon} {path.name} ({human_size(path.stat().st_size)})"
    except OSError:
        return f"{icon} {path.name}"


_ = (os, Align, empty_state, KeyHint, common_root)  # re-exported helpers
