"""Animated gradient banner.

The banner is an ANSI half-block "LFS" wordmark plus a gradient subtitle. On a
real TTY it shimmers once (or loops for ``--demo``); in pipes, CI and
``--no-anim`` it renders a single static frame, so screenshots and logs stay
clean.
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass

from rich.align import Align
from rich.console import Group, RenderableType
from rich.live import Live
from rich.text import Text

from .. import APP_TAGLINE, __version__, platforms
from . import console as console_mod
from .theme import Theme, gradient_rgb, rgb_to_style

# ── Wordmark ─────────────────────────────────────────────────────────────────
# 5-row block letters drawn with unicode half blocks. Each row is a list of
# glyphs separated by a single blank column.
_GLYPHS: dict[str, list[str]] = {
    "L": ["█▌   ", "█▌   ", "█▌   ", "█▌   ", "█████"],
    "F": ["█████", "█▌   ", "████ ", "█▌   ", "█▌   "],
    "S": ["▐████", "█▌   ", "▐███▌", "   ██", "████▌"],
}

_LOGO_SCALE: dict[str, str] = {
    "█": "█",
    "▌": "▌",
    "▐": "▐",
    " ": " ",
}


def wordmark(text: str = "LFS") -> list[str]:
    """Render block letters row by row."""
    rows = ["", "", "", "", ""]
    for index, char in enumerate(text.upper()):
        glyph = _GLYPHS.get(char)
        if glyph is None:
            continue
        for row, line in enumerate(glyph):
            if index:
                rows[row] += " "
            rows[row] += line
    rows = [row.rstrip() for row in rows]
    return rows if any(rows) else []


# ── Rendering ────────────────────────────────────────────────────────────────
def gradient_text(text: str, theme: Theme, *, offset: float = 0.0, bold: bool = True) -> Text:
    out = Text()
    width = max(1, len(text) - 1)
    stops = list(theme.logo_stops or theme.gradient_stops or (theme.accent_rgb,))
    for index, char in enumerate(text):
        if char == " ":
            out.append(" ")
            continue
        pos = (index / width * 0.85 + offset) % 1.0
        out.append(char, style=rgb_to_style(gradient_rgb(stops, pos), bold=bold))
    return out


def logo(theme: Theme, *, offset: float = 0.0) -> Text:
    """The LFS wordmark, optionally shimmered by *offset*."""
    rows = wordmark("LFS")
    block = Text()
    max_width = max((len(r) for r in rows), default=1) or 1
    for row_index, row in enumerate(rows):
        for col, char in enumerate(row.ljust(max_width)):
            if char == " ":
                block.append(" ")
                continue
            pos = ((col / max_width) * 0.7 + (row_index * 0.06) + offset) % 1.0
            stops = list(theme.logo_stops or (theme.accent_rgb,))
            block.append(char, style=rgb_to_style(gradient_rgb(stops, pos), bold=True))
        if row_index < len(rows) - 1:
            block.append("\n")
    return block


def subtitle(theme: Theme, *, offset: float = 0.0) -> Text:
    text = f"local-file-sharer v{__version__}  ·  {APP_TAGLINE}"
    return gradient_text(text, theme, offset=offset, bold=False)


def banner_renderable(
    theme: Theme,
    *,
    offset: float = 0.0,
    badge: bool = True,
    width: int | None = None,
) -> RenderableType:
    """Just the logo + subtitle (used by `start banner` and the header)."""
    parts: list[RenderableType] = [logo(theme, offset=offset), Text(""), subtitle(theme, offset=offset)]
    if badge:
        chip = Text()
        chip.append(" TERMUX ", style="bold black on bright_yellow")
        chip.append(" ")
        chip.append(" LINUX ", style="bold black on bright_white")
        chip.append(" ")
        chip.append(" WINDOWS ", style="bold black on bright_blue")
        chip.append("  ")
        chip.append("rich + qrcode", style=theme.muted)
        parts.extend([Text(""), chip])
    return Align.left(Align.center(Group(*parts)) if width is None else Group(*parts))


def _frame(theme: Theme, offset: float, width: int) -> Text:
    """One animation frame: logo + gradient subtitle + shimmering rule."""
    out = logo(theme, offset=offset)
    out.append("\n\n")
    out.append(subtitle(theme, offset=offset * 1.6))
    out.append("\n")
    rule_chars = "─" * max(10, min(width - 2, 72))
    stops = list(theme.gradient_stops or (theme.accent_rgb,))
    for index, char in enumerate(rule_chars):
        pos = (index / max(1, len(rule_chars) - 1) * 0.9 + offset) % 1.0
        out.append(char, style=rgb_to_style(gradient_rgb(stops, pos)))
    return out


def supports_animation() -> bool:
    """True when it is safe (and polite) to animate."""
    if platforms.os.environ.get("NO_COLOR"):
        return False
    try:
        con = console_mod.console()
        return bool(con.is_terminal and con.color_system)
    except Exception:  # noqa: BLE001
        return False


@dataclass
class BannerPlayer:
    theme: Theme
    frames: int = 18
    fps: float = 22.0
    loop: bool = False

    def play(self) -> None:
        if not supports_animation():
            console_mod.console().print(banner_renderable(self.theme))
            return
        con = console_mod.console()
        width = console_mod.screen_width()
        delay = 1.0 / max(1.0, self.fps)
        try:
            with Live(
                _frame(self.theme, 0.0, width),
                console=con,
                refresh_per_second=max(8, int(self.fps)),
                transient=False,
                auto_refresh=False,
            ) as live:
                total = self.frames
                index = 0
                while True:
                    phase = index / max(1, total)
                    live.update(_frame(self.theme, (phase) % 1.0, width), refresh=True)
                    time.sleep(delay)
                    index += 1
                    if index >= total and not self.loop:
                        break
                live.update(_frame(self.theme, 0.0, width), refresh=True)
        except (KeyboardInterrupt, OSError):
            pass


def animate(theme: Theme, *, frames: int = 18, loop: bool = False) -> None:
    """Play the startup shimmer (no-op-safe when the terminal is static)."""
    BannerPlayer(theme=theme, frames=frames, loop=loop).play()


def static(theme: Theme) -> None:
    console_mod.console().print(banner_renderable(theme))


def shimmer_strength(offset: float) -> float:
    """Exposed for tests: the brightness multiplier of the travelling highlight."""
    return 0.55 + 0.45 * (0.5 + 0.5 * math.sin(offset * 2 * math.pi))


def mini_header(theme: Theme, *, mode: str = "", detail: str = "") -> Text:
    """One-line header used above menus so the screen never scrolls away."""
    text = Text()
    text.append(" LFS ", style=f"bold black on {theme.accent}")
    text.append(" local-file-sharer ", style=theme.muted)
    if mode:
        text.append(f"› {mode} ", style=f"bold {theme.accent}")
    if detail:
        text.append(f"· {detail}", style=theme.muted)
    return text


def clear_line(count: int = 1) -> str:
    """ANSI helper: move up *count* lines and clear them (no full clear)."""
    if not sys.stdout.isatty():
        return ""
    return "\x1b[1A\x1b[2K" * count
