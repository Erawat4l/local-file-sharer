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

    ``start --json`` prints one JSON object on stdout; without this the banner
    and the "share ready" panel land there too, and `start … --json | jq` fails
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
        force_terminal=None if previous is None else previous.is_terminal,
        width=None if previous is None or previous._width is None else previous.width,  # noqa: SLF001
    )


def set_console(console: Console) -> None:
    """Swap the process-wide console (tests, screenshots, embedded use)."""
    global _console
    _console = console


def current_theme() -> Theme:
    return _theme or Theme()


def screen_width(default: int = 100) -> int:
    """Usable terminal width, clamped to something sane for narrow phones."""
    try:
        width = console().width
    except Exception:  # noqa: BLE001 - never fail on size probing
        width = default
    return max(40, min(width, 200))


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
    theme = theme or current_theme()
    return Panel(
        body,
        title=title,
        border_style=border or theme.border,
        title_align="left",
        padding=(0, 1),
        expand=False,
    )


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
