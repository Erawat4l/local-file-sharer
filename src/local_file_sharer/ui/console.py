"""Shared Rich console with theme-aware helpers.

A single :class:`Console` instance keeps Live/Progress renders from fighting
with each other and makes ``--no-color`` / ``NO_COLOR`` behave consistently.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme as RichTheme

from .theme import Theme

_console: Console | None = None
_theme: Theme | None = None


def _rich_theme(theme: Theme) -> RichTheme:
    return RichTheme(
        {
            "lfs.primary": theme.primary,
            "lfs.secondary": theme.secondary,
            "lfs.muted": theme.muted,
            "lfs.faint": theme.faint,
            "lfs.accent": theme.accent,
            "lfs.success": theme.success,
            "lfs.warning": theme.warning,
            "lfs.danger": theme.danger,
            "lfs.info": theme.info,
            "lfs.border": theme.border,
            "lfs.key": theme.key,
            "lfs.link": theme.link,
            "repr.path": theme.accent,
            "repr.filename": theme.secondary,
            "repr.number": theme.info,
            "bar.complete": theme.accent,
            "bar.finished": theme.success,
            "bar.pulse": theme.accent,
            "progress.description": theme.secondary,
            "progress.percentage": theme.accent,
            "progress.remaining": theme.muted,
            "rule.line": theme.border_soft,
            "prompt.choices": theme.accent,
            "prompt.default": theme.muted,
            "prompt.required": theme.warning,
        }
    )


#: When set, human output goes to stderr so stdout stays machine-readable.
_human_to_stderr = False


def human_output_to_stderr(enabled: bool = True) -> None:
    """Send panels, banners and progress to stderr, keeping stdout clean.

    ``lfs --json`` prints one JSON object on stdout; without this the banner
    and the "share ready" panel land there too, and `lfs … --json | jq` fails
    on the first box-drawing character.
    """
    global _console, _human_to_stderr
    _human_to_stderr = enabled
    _console = None  # rebuilt on next use with the new stream


def console() -> Console:
    """The process-wide console (created lazily so tests can patch it)."""
    global _console, _theme
    if _console is None:
        _theme = _theme or Theme()
        no_color = bool(os.environ.get("NO_COLOR")) or "--no-color" in sys.argv
        _console = Console(
            theme=_rich_theme(_theme),
            no_color=no_color,
            highlight=False,
            soft_wrap=False,
            emoji=True,
            log_time=False,
            stderr=_human_to_stderr,
            # Rich swaps its rounded box corners for square ones on a legacy
            # Windows console, which would make the menu a different picture
            # per platform. The look is part of the product, so pin it.
            safe_box=False,
        )
    return _console


def set_theme(theme: Theme) -> None:
    """Re-create the console so new styles apply immediately."""
    global _console, _theme
    _theme = theme
    previous = _console
    no_color = bool(os.environ.get("NO_COLOR")) or (previous.no_color if previous else False)
    _console = Console(
        theme=_rich_theme(theme),
        no_color=no_color,
        highlight=False,
        soft_wrap=False,
        emoji=True,
        safe_box=False,
        force_terminal=None if previous is None else previous.is_terminal,
        width=None if previous is None or previous._width is None else previous.width,  # noqa: SLF001
    )


def set_console(console: Console) -> None:
    """Swap the process-wide console (tests, screenshots, embedded use)."""
    global _console
    _console = console


def current_theme() -> Theme:
    return _theme or Theme()


#: Below this, box-drawing layouts stop being readable; prose is used instead.
NARROW_WIDTH = 46


def screen_width(default: int = 100) -> int:
    """The real terminal width, never a pretend one.

    This used to floor at 40, which is wrong on a phone: a 41-column Termux
    session got panels laid out for 40 columns plus their border and padding,
    so every line wrapped mid-word and the layout looked shattered.
    """
    try:
        width = console().width
    except Exception:  # noqa: BLE001 - never fail on size probing
        width = default
    if not width or width < 10:  # detached from a terminal
        width = default
    return min(width, 200)


def is_narrow() -> bool:
    return screen_width() < NARROW_WIDTH


def screen_height(default: int = 30) -> int:
    try:
        height = console().height
    except Exception:  # noqa: BLE001
        return default
    return max(10, height or default)


def bold(text: str) -> Text:
    theme = current_theme()
    return Text(text, style=theme.primary)


def muted(text: str) -> Text:
    return Text(text, style=current_theme().muted)


def accent(text: str) -> Text:
    theme = current_theme()
    return Text(text, style=f"bold {theme.accent}")


def success(text: str) -> Text:
    return Text(text, style=current_theme().success)


def warning(text: str) -> Text:
    return Text(text, style=current_theme().warning)


def danger(text: str) -> Text:
    return Text(text, style=current_theme().danger)


def panel(body, *, title: str | Text | None = None, border: str | None = None, theme: Theme | None = None):
    """A bordered panel that fits the terminal instead of sizing to its content.

    ``expand=False`` made every panel shrink-wrap, which is what pushed long
    one-liner commands past the right edge on a phone. A width of at most the
    screen minus the two border columns keeps the box inside the window.
    """
    theme = theme or current_theme()
    width = max(20, screen_width() - 1)
    return Panel(
        body,
        title=title,
        border_style=border or theme.border,
        title_align="left",
        padding=(0, 1),
        expand=False,
        width=width,
    )


def wrap_command(command: str, *, indent: str = "   ", width: int | None = None) -> list[str]:
    """Split a shell command for display without breaking arguments mid-token.

    A phone-width panel cannot hold ``curl -L --fail --progress-bar -C - -O
    http://…`` on one line, and hard-wrapping it produced ``-C`` / ``- -O`` on
    separate lines — text that looks like a command but is not one. Break on
    spaces only, and mark the continuation with a backslash so it stays clear
    that the command continues.

    A flag that takes a value keeps it: ``-C -`` is one unit, so a wrap never
    leaves the value stranded on the next line where copy-paste would break it.
    """
    limit = max(20, (width or screen_width()) - len(indent) - 3)
    words = command.split(" ")
    units: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        # a short flag is followed by its value: -C -, -o file.txt, -x16 is one
        takes_value = (
            word.startswith("-")
            and not word.startswith("--")
            and "=" not in word
            and len(word) == 2
            and index + 1 < len(words)
            and not words[index + 1].startswith("-")
        )
        pair = f"{word} {words[index + 1]}" if takes_value else ""
        # only glue them when the pair itself fits; `-O <40-char url>` never
        # will on a phone, and an unbreakable unit just overflows the panel
        if takes_value and len(pair) <= limit:
            units.append(pair)
            index += 2
        else:
            units.append(word)
            index += 1

    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}".strip()
        if current and len(candidate) > limit:
            lines.append(current + " \\")
            current = unit
        else:
            current = candidate
    if current:
        lines.append(current)
    return [indent + line for line in lines]


def print_line(text: str | Text = "", **kwargs) -> None:
    console().print(text, **kwargs)


@contextmanager
def status(message: str):
    """Inline spinner that degrades to a plain line when not a terminal."""
    con = console()
    if con.is_terminal:
        with con.status(message, spinner="dots"):
            yield
    else:
        con.print(Text(message, style="lfs.muted"))
        yield
