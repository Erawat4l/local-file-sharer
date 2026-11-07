"""Render Rich renderables to pixel-accurate SVG/PNG terminal screenshots.

Why not ``Console.export_svg``? It hard-codes Fira Code, uses clip-paths that
resvg ignores and cannot draw emoji. Here every cell is positioned explicitly,
so the output is identical in every browser and in the PNG we commit.
"""

from __future__ import annotations

import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTFont
from paths import FONT_DIR, TWEMOJI_DIR
from rich.cells import cell_len
from rich.color import ColorType
from rich.console import Console, RenderableType
from rich.style import Style
from rich.text import Text

FONT_SIZE = 20.0
CELL_W = FONT_SIZE * 0.6  # JetBrains Mono advance width
LINE_H = FONT_SIZE * 1.22
PAD_X = 26.0
FIRST_LINE = 62.0  # leaves room for the window chrome
PAD_BOTTOM = 22.0
CHROME_H = 42.0

DEFAULT_FG = "#e6edf3"
#: The recorded terminal's own colours. Greys plus a warm white: the palette
#: is here because Rich can emit an indexed colour, and an unlisted index was
#: silently painted in whatever the last one was.
ANSI_16 = [
    # Monochrome, deliberately: the product has no accent hue, so an indexed
    # colour must not be able to paint one. Rich still emits indexes for named
    # styles, and before this the gif showed the blue-ish "#0b0f19" tint that
    # the old palette carried in slot 0.
    "#000000",  # black
    "#3d3d3d",  # bright black / dark grey
    "#8a8a8a",  # grey
    "#a3a3a3",  # dim white
    "#bcbcbc",
    "#d4d4d4",
    "#e8e8e8",
    "#f5f5f5",  # white
    "#5b5b5b",
    "#6b6b6b",
    "#7d7d7d",
    "#8f8f8f",
    "#a8a8a8",
    "#c4c4c4",
    "#dedede",
    "#ffffff",  # bright white
]

TWEMOJI = TWEMOJI_DIR
EMOJI_RE = re.compile("([\U0001f000-\U0001faff\u2190-\u21ff\u2600-\u27bf\u2b00-\u2bff\u2300-\u23ff]+)")
# Glyphs JetBrains Mono cannot draw: fall back to the Noto emoji font.
EMOJI_FONT = str(FONT_DIR / "Noto-COLRv1.ttf")


# ── segments ────────────────────────────────────────────────────────────────
@dataclass
class Cell:
    char: str
    fg: str
    bg: str | None
    bold: bool
    dim: bool
    width: int = 1


def _rgb(console: Console, style: Style) -> tuple[str, str | None, bool, bool]:
    fg = "#e6edf3"
    if style.color is not None and style.color.get_truecolor() is not None:
        r, g, b = style.color.get_truecolor()
        fg = f"#{r:02x}{g:02x}{b:02x}"
    elif style.color is not None and style.color.name:
        resolved = console.get_style(style.color.name).color
        if resolved is not None:
            r, g, b = resolved.get_truecolor()
            fg = f"#{r:02x}{g:02x}{b:02x}"
    bg = None
    if style.bgcolor is not None:
        r, g, b = style.bgcolor.get_truecolor()
        bg = f"#{r:02x}{g:02x}{b:02x}"
    return fg, bg, bool(style.bold), bool(style.dim)


def to_grid(renderable: RenderableType, *, width: int, background: str = "#000000") -> list[list[Cell]]:
    """Render *renderable* and return a grid of styled cells."""
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        width=width,
        force_terminal=True,
        color_system="truecolor",
        emoji=True,
        legacy_windows=False,
        markup=False,
        highlight=False,
        soft_wrap=False,
        height=400,
    )
    console.print(renderable, end="")
    raw = buffer.getvalue().rstrip("\n")
    rows: list[list[Cell]] = []
    for line in raw.split("\n"):
        text = Text.from_ansi(line)
        row: list[Cell] = []
        for offset, char in enumerate(text.plain):
            if char == "\r":
                continue
            style = text.get_style_at_offset(console, offset)
            size = cell_len(char)
            if size == 0 and row and char != "\n":
                row[-1].char += char
                row[-1].width = max(1, cell_len(row[-1].char))
                continue
            row.append(Cell(char, *_cell_colors(style), width=max(1, size)))
        rows.append(row)
    while rows and not "".join(c.char for c in rows[-1]).strip():
        rows.pop()
    return rows


def _cell_colors(style: Style) -> tuple[str, str | None, bool, bool]:
    """(fg, bg, bold, dim) for a resolved Style."""
    fg = DEFAULT_FG
    color = style.color
    if color is not None and color.type is ColorType.TRUECOLOR:
        triplet = color.get_truecolor()
        fg = f"#{triplet.red:02x}{triplet.green:02x}{triplet.blue:02x}"
    elif color is not None and color.type is ColorType.STANDARD and color.number is not None:
        fg = ANSI_16[color.number]
    bg: str | None = None
    bcolor = style.bgcolor
    if bcolor is not None and bcolor.type is ColorType.TRUECOLOR:
        triplet = bcolor.get_truecolor()
        bg = f"#{triplet.red:02x}{triplet.green:02x}{triplet.blue:02x}"
    elif bcolor is not None and bcolor.type is ColorType.STANDARD and bcolor.number is not None:
        bg = ANSI_16[bcolor.number]
    return fg, bg, bool(style.bold), bool(style.dim)


# ── svg ─────────────────────────────────────────────────────────────────────
def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_MONO_CMAP = None


def _in_mono(char: str) -> bool:
    global _MONO_CMAP
    if _MONO_CMAP is None:
        _MONO_CMAP = set(TTFont(str(FONT_DIR / "JetBrainsMono-Regular.ttf")).getBestCmap())
    return all(ord(c) in _MONO_CMAP for c in char)


def _is_emoji(char: str) -> bool:
    stripped = char.replace("\ufe0f", "")
    if not stripped or not EMOJI_RE.fullmatch(stripped):
        return False
    if char in "\u2190\u2192\u2191\u2193\u21b5":
        return False
    return not _in_mono(char)


_MISSING_EMOJI: set[str] = set()


def _twemoji(char: str) -> str | None:
    base = char.rstrip("\ufe0f").replace("\u200d", "-")
    if not base or (base.isascii() and len(base) == 1):
        return None
    if not base:
        return None
    code = "-".join(f"{ord(c):x}" for c in base)
    path = TWEMOJI / f"{code}.svg"
    if not path.exists():
        # try dropping variation selectors inside the sequence
        code = "-".join(f"{ord(c):x}" for c in base if c not in "\ufe0f")
        path = TWEMOJI / f"{code}.svg"
        if not path.exists():
            # Recorded once per glyph so a bad TWEMOJI_DIR shows up in the run
            # output instead of quietly rendering every emoji as a tofu box.
            if char not in _MISSING_EMOJI:
                _MISSING_EMOJI.add(char)
                print(
                    f"warning: no Twemoji SVG for {char!r} ({code}) in {TWEMOJI} — "
                    "it will render as a fallback glyph",
                    file=sys.stderr,
                )
            return None
    body = path.read_text(encoding="utf-8")
    body = re.sub(r"<\?xml[^>]*\?>", "", body)
    body = re.sub(r"<!DOCTYPE[^>]*>", "", body)
    body = re.sub(r"<svg[^>]*>", "", body, count=1)
    body = body.replace("</svg>", "")
    body = re.sub(r'\sid="[^"]*"', "", body)
    return body


def to_svg(
    rows: list[list[Cell]],
    *,
    title: str = "",
    background: str = "#000000",
    chrome: bool = True,
    scale: float = 2.0,
    cols: int | None = None,
) -> str:
    cols = cols or max((len(r) for r in rows), default=1)
    width = PAD_X * 2 + cols * CELL_W
    top = CHROME_H + 14 if chrome else 18.0
    height = top + len(rows) * LINE_H + PAD_BOTTOM
    out: list[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width * scale:.0f}" '
        f'height="{height * scale:.0f}" viewBox="0 0 {width:.1f} {height:.1f}">'
    )
    out.append("<defs>")
    out.append(
        '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{background}"/>'
        f'<stop offset="1" stop-color="{background}"/></linearGradient>'
    )
    out.append(
        # A grey edge, not a cyan-to-purple one. The frame around the picture
        # is part of the picture, and the product has no colour in it.
        '<linearGradient id="edge" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#f5f5f5" stop-opacity="0.34"/>'
        '<stop offset="0.55" stop-color="#8a8a8a" stop-opacity="0.22"/>'
        '<stop offset="1" stop-color="#f5f5f5" stop-opacity="0.08"/></linearGradient>'
    )
    out.append("</defs>")
    out.append(f'<rect width="{width:.1f}" height="{height:.1f}" rx="14" fill="url(#bg)"/>')
    out.append(
        f'<rect x="0.6" y="0.6" width="{width - 1.2:.1f}" height="{height - 1.2:.1f}" rx="13.4" '
        'fill="none" stroke="url(#edge)" stroke-width="1.2"/>'
    )
    if chrome:
        # Greys, not the usual red/amber/green: these dots are part of the
        # product's picture, and the product has no colour in it anywhere.
        for index, color in enumerate(("#3d3d3d", "#6b6b6b", "#a3a3a3")):
            out.append(
                f'<circle cx="{PAD_X + index * 21 + 4:.1f}" cy="{CHROME_H / 2 + 4:.1f}" r="6.5" fill="{color}"/>'
            )
        if title:
            out.append(
                f'<text x="{width / 2:.1f}" y="{CHROME_H / 2 + 10:.1f}" text-anchor="middle" '
                f'font-family="JetBrains Mono, monospace" font-size="13" fill="#8b949e" '
                f'letter-spacing="0.6">{_esc(title)}</text>'
            )
        out.append(
            f'<line x1="0" y1="{CHROME_H:.1f}" x2="{width:.1f}" y2="{CHROME_H:.1f}" '
            'stroke="#262626" stroke-width="1"/>'
        )

    for row_index, row in enumerate(rows):
        y = top + row_index * LINE_H + FONT_SIZE * 0.92
        offsets: list[float] = []
        cursor = 0.0
        for cell in row:
            offsets.append(cursor)
            cursor += CELL_W * cell.width
        # background runs
        col = 0
        while col < len(row):
            cell = row[col]
            if cell.bg is None:
                col += 1
                continue
            start_col = col
            while col < len(row) and row[col].bg == cell.bg:
                col += 1
            x = PAD_X + offsets[start_col]
            span = PAD_X + offsets[col] if col < len(row) else PAD_X + cursor
            out.append(
                f'<rect x="{x:.2f}" y="{y - FONT_SIZE * 0.86:.2f}" width="{span - x:.2f}" '
                f'height="{LINE_H:.2f}" fill="{cell.bg}"/>'
            )
        # text runs grouped by (fg, bold, dim)
        col = 0
        while col < len(row):
            cell = row[col]
            if cell.char.strip() == "":
                col += 1
                continue
            emoji = _twemoji(cell.char) if _is_emoji(cell.char) else None
            if emoji is not None:
                wide = 2 if cell.width >= 2 else 1.12
                size = min(CELL_W * wide * 0.94, LINE_H * 0.92)
                x = PAD_X + offsets[col] + (CELL_W * wide - size) / 2
                py = y - FONT_SIZE * 0.86 + (LINE_H - size) / 2
                out.append(f'<g transform="translate({x:.2f},{py:.2f}) scale({size / 36:.5f})">{emoji}</g>')
                col += 1
                continue
            key = (cell.fg, cell.bold, cell.dim)
            start_col = col
            chars: list[str] = []
            while (
                col < len(row)
                and (row[col].fg, row[col].bold, row[col].dim) == key
                and not (_is_emoji(row[col].char) and _twemoji(row[col].char) is not None)
            ):
                chars.append(row[col].char)
                col += 1
            text = "".join(chars).replace("\u00a0", " ")
            if not text.strip():
                continue
            x = PAD_X + offsets[start_col]
            weight = ' font-weight="700"' if cell.bold else ""
            opacity = ' opacity="0.62"' if cell.dim else ""
            out.append(
                f'<text x="{x:.2f}" y="{y:.2f}" font-family="JetBrains Mono, monospace" '
                f'font-size="{FONT_SIZE:.0f}" fill="{cell.fg}"{weight}{opacity} '
                f'xml:space="preserve">{_esc(text)}</text>'
            )
    out.append("</svg>")
    return "".join(out)


def svg_to_png(svg: str, out: Path) -> None:
    import resvg_py

    png = resvg_py.svg_to_bytes(
        svg_string=svg,
        font_files=[str(FONT_DIR / "JetBrainsMono-Regular.ttf"), str(FONT_DIR / "JetBrainsMono-Bold.ttf")],
        skip_system_fonts=True,
        monospace_family="JetBrains Mono",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
