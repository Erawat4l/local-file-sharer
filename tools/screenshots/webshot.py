"""Author the three web-UI screenshots as SVG using the app's own design tokens.

Every colour, radius and piece of copy below is taken from the shipped
`web/assets/*.html` + `static/app.css`, so the pictures match what a phone
actually shows. Text is measured from the real Inter/JetBrains Mono files.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import ClassVar

from fontTools.ttLib import TTFont
from paths import FONT_DIR, TWEMOJI_DIR

TWEMOJI = TWEMOJI_DIR

FONTS = {
    "sans": [
        (str(FONT_DIR / "Inter-Regular.ttf"), 400),
        (str(FONT_DIR / "Inter-Medium.ttf"), 500),
        (str(FONT_DIR / "Inter-SemiBold.ttf"), 600),
        (str(FONT_DIR / "Inter-Bold.ttf"), 700),
        (str(FONT_DIR / "Inter-ExtraBold.ttf"), 800),
    ],
    "mono": [
        (str(FONT_DIR / "JetBrainsMono-Regular.ttf"), 400),
        (str(FONT_DIR / "JetBrainsMono-Bold.ttf"), 700),
    ],
}

# ── palette: read off app.css :root, and it is greys only ───────────────────
# This block used to be a blue-black theme (#05080f page, #22d3ee cyan accent,
# #a78bfa violet second accent), and the screenshots showed a product that no
# longer exists. The page is black and white now, so the shots are too.
BG = "#000000"
BG2 = "#0a0a0a"
PANEL = "#0d0d0d"
PANEL2 = "#141414"
LINE = "rgba(255,255,255,0.10)"
LINE_STRONG = "rgba(255,255,255,0.22)"
TEXT = "#f5f5f5"
DIM = "#a3a3a3"
FAINT = "#6b6b6b"
ACCENT = "#f5f5f5"  # "accent" is contrast now, not a hue
ACCENT2 = "#d4d4d4"
ACCENT3 = "#a3a3a3"
DANGER = "#f5f5f5"
WARN = "#d4d4d4"


#: Geometry copied from ``_ICON_PATHS`` in transfer/http_handler.py -- the glyphs
#: the listing actually draws. Keep them in sync; test_asset_drift.py checks
#: that every kind drawn in a shot has a glyph in the template's table.
_PAGE_ICON_PATHS: dict[str, tuple[str, ...]] = {
    "folder": ('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',),
    "video": ('<path d="M4 6h11v12H4z"/>', '<path d="M15 10l5-3v10l-5-3z"/>'),
    "audio": (
        '<path d="M9 18V6l10-2v12"/>',
        '<circle cx="6" cy="18" r="3"/>',
        '<circle cx="16" cy="16" r="3"/>',
    ),
    "image": (
        '<rect x="3" y="4" width="18" height="16" rx="2"/>',
        '<circle cx="9" cy="10" r="2"/>',
        '<path d="M21 16l-5-5-6 6-3-3-4 4"/>',
    ),
    "pdf": (
        '<path d="M6 3h8l4 4v14H6z"/>',
        '<path d="M14 3v5h4"/>',
        '<path d="M9 13h6M9 17h4"/>',
    ),
    "text": (
        '<path d="M6 3h8l4 4v14H6z"/>',
        '<path d="M14 3v5h4"/>',
        '<path d="M9 12h6M9 16h6"/>',
    ),
    "archive": ('<path d="M4 8h16v12H4z"/>', '<path d="M3 4h18v4H3z"/>', '<path d="M10 12h4"/>'),
    "download": (
        '<path d="M12 4v11"/>',
        '<path d="M7 11l5 5 5-5"/>',
        '<path d="M5 20h14"/>',
    ),
}


class Measure:
    _cache: ClassVar[dict] = {}

    def __init__(self, family: str, weight: int, size: float) -> None:
        self.family = family
        self.weight = weight
        self.size = size

    def width(self, text: str) -> float:
        key = (self.family, self.weight, self.size)
        table = Measure._cache.get(key)
        if table is None:
            paths = {weight: file for file, weight in FONTS[self.family]}
            available = sorted(paths)
            chosen = (
                self.weight if self.weight in paths else min(available, key=lambda w: abs(w - self.weight))
            )
            path = paths[chosen]
            font = TTFont(path)
            cmap = font.getBestCmap()
            hmtx = font["hmtx"]
            upem = font["head"].unitsPerEm
            table = {code: hmtx[name][0] / upem * self.size for code, name in cmap.items()}
            table[0xA0] = table.get(0x20, self.size * 0.28)
            Measure._cache[key] = table
        total = 0.0
        for char in text.replace("\ufe0f", ""):
            total += table.get(ord(char), self.size * 0.55)
        return total


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Canvas:
    def __init__(self, width: int, height: int, *, background: str | None = None) -> None:
        self.width = width
        self.height = height
        self.parts: list[str] = []
        self.defs: list[str] = []
        self._grad = 0
        self._clip = 0
        self._emoji = 0
        #: every visible string drawn, in draw order. ``Shot`` records it to
        #: web-text.txt so a test can hold the copy against the real templates;
        #: a bare Canvas (the README hero) just needs the list to exist.
        self.labels: list[str] = []
        self._setup_defs()
        if background:
            self.rect(0, 0, width, height, fill=background)

    def _setup_defs(self) -> None:
        """Register @font-face so the SVG is self-contained in a browser too."""
        for family, files in FONTS.items():
            for path, weight in files:
                data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
                name = "JetBrains Mono" if family == "mono" else "Inter"
                self.defs.append(
                    "<style>@font-face{font-family:'"
                    + name
                    + "';font-weight:"
                    + str(weight)
                    + ";src:url(data:font/ttf;base64,"
                    + data
                    + ") format('truetype');}</style>"
                )

    # ── primitives ──────────────────────────────────────────────────────────
    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str = "none",
        stroke: str | None = None,
        sw: float = 1.0,
        r: float = 0.0,
        opacity: float = 1.0,
        dash: str | None = None,
    ) -> None:
        attrs = [
            f'x="{x:.2f}"',
            f'y="{y:.2f}"',
            f'width="{w:.2f}"',
            f'height="{h:.2f}"',
            f'fill="{fill}"',
        ]
        if r:
            attrs.append(f'rx="{r:.2f}"')
        if stroke:
            attrs.append(f'stroke="{stroke}" stroke-width="{sw:.2f}"')
        if dash:
            attrs.append(f'stroke-dasharray="{dash}"')
        if opacity < 1:
            attrs.append(f'opacity="{opacity:.2f}"')
        self.parts.append(f"<rect {' '.join(attrs)}/>")

    def circle(self, cx: float, cy: float, r: float, *, fill: str, opacity: float = 1.0) -> None:
        op = f' opacity="{opacity:.2f}"' if opacity < 1 else ""
        self.parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{fill}"{op}/>')

    def line(
        self, x1: float, y1: float, x2: float, y2: float, *, stroke: str = LINE, sw: float = 1.0
    ) -> None:
        self.parts.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{stroke}" stroke-width="{sw:.2f}"/>'
        )

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        family: str = "sans",
        weight: int = 400,
        size: float = 14.0,
        fill: str = TEXT,
        anchor: str = "start",
        spacing: float = 0.0,
        opacity: float = 1.0,
    ) -> float:
        """Draw text; returns its measured width."""
        m = Measure(family, weight, size)
        width = m.width(value)
        attrs = [
            f'x="{x:.2f}"',
            f'y="{y:.2f}"',
            f'font-family="{"JetBrains Mono" if family == "mono" else "Inter"}"',
            f'font-size="{size:.2f}"',
            f'fill="{fill}"',
        ]
        if weight != 400:
            attrs.append(f'font-weight="{weight}"')
        if anchor != "start":
            attrs.append(f'text-anchor="{anchor}"')
        if spacing:
            attrs.append(f'letter-spacing="{spacing:.2f}"')
        if opacity < 1:
            attrs.append(f'opacity="{opacity:.2f}"')
        self.parts.append(f'<text {" ".join(attrs)} xml:space="preserve">{esc(value)}</text>')
        if value.strip():
            self.labels.append(value)
        return width

    def rich(
        self,
        x: float,
        y: float,
        runs: list[tuple[str, dict]],
        *,
        family: str = "sans",
        size: float = 14.0,
        fill: str = TEXT,
    ) -> float:
        """Draw consecutive styled runs on one baseline."""
        cursor = x
        for index, (chunk, opts) in enumerate(runs):
            drawn = self.text(
                cursor,
                y,
                chunk,
                family=opts.get("family", family),
                weight=opts.get("weight", 400),
                size=opts.get("size", size),
                fill=opts.get("fill", fill),
                opacity=opts.get("opacity", 1.0),
            )
            gap = opts.get("gap")
            if gap is None:
                # Runs are separate <text> elements. In the markup a heading is
                # literally "Download <span>Everything</span>, fast.", so the
                # space lives at the end of the first run -- where it is
                # measured but never drawn, which is how "DownloadEverything"
                # appeared. Re-add it as an advance. A following run that starts
                # with punctuation ("  , fast.") gets nothing: its own leading
                # space plus a gap floated the comma off the word.
                nxt = runs[index + 1][0] if index + 1 < len(runs) else ""
                gap = size * 0.26 if chunk.endswith(" ") and nxt[:1] not in ",.;:!?)" else 0.0
            cursor += drawn + gap
        return cursor - x

    def emoji(self, x: float, y: float, char: str, size: float) -> None:
        base = char.replace("\ufe0f", "").replace("\u200d", "-")
        code = "-".join(f"{ord(c):x}" for c in base)
        path = TWEMOJI / f"{code}.svg"
        if not path.exists():
            return
        body = path.read_text(encoding="utf-8")
        body = strip_svg_head(body)
        self.parts.append(f'<g transform="translate({x:.2f},{y:.2f}) scale({size / 36:.4f})">{body}</g>')

    def icon(self, x: float, y: float, kind: str, size: float = 20, color: str = DIM) -> None:
        """One of the page's own file glyphs, at ``size`` pixels.

        The shots used to draw Twemoji: a red PDF, a green video camera, a blue
        audio note. The real listing draws inline monochrome SVG from
        ``icon_svg()`` in http_handler.py, so the screenshots were not just
        tinted -- they were showing icons that no template contains, and those
        emoji were the only colour left in any committed asset.
        """
        paths = _PAGE_ICON_PATHS.get(kind, _PAGE_ICON_PATHS["archive"])
        scale = size / 24
        body = "".join(paths).replace("<path ", "<path ")
        self.parts.append(
            f'<g transform="translate({x:.2f},{y:.2f}) scale({scale:.4f})" fill="none" '
            f'stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</g>'
        )

    # ── gradients ───────────────────────────────────────────────────────────
    def linear(self, stops: list[tuple[float, str]], *, x1=0, y1=0, x2=1, y2=1) -> str:
        self._grad += 1
        name = f"lg{self._grad}"
        body = "".join(f'<stop offset="{pos:.2f}" stop-color="{color}"/>' for pos, color in stops)
        self.defs.append(
            f'<linearGradient id="{name}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">{body}</linearGradient>'
        )
        return f"url(#{name})"

    def radial(self, stops: list[tuple[float, str]], *, cx=0.5, cy=0.5, r=0.5) -> str:
        self._grad += 1
        name = f"rg{self._grad}"
        body = "".join(f'<stop offset="{pos:.2f}" stop-color="{color}"/>' for pos, color in stops)
        self.defs.append(f'<radialGradient id="{name}" cx="{cx}" cy="{cy}" r="{r}">{body}</radialGradient>')
        return f"url(#{name})"

    def glow_rect(
        self, x, y, w, h, *, r: float, fill: str, glow: str, spread: float = 34.0, opacity=0.55
    ) -> None:
        self._grad += 1
        name = f"gl{self._grad}"
        self.defs.append(
            f'<filter id="{name}" x="-60%" y="-60%" width="220%" height="220%">'
            f'<feDropShadow dx="0" dy="0" stdDeviation="{spread / 3:.1f}" flood-color="{glow}" '
            f'flood-opacity="{opacity}"/></filter>'
        )
        self.parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{r:.2f}" '
            f'fill="{fill}" filter="url(#{name})"/>'
        )

    # ── output ──────────────────────────────────────────────────────────────
    def svg(self) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}">'
            f"<defs>{''.join(self.defs)}</defs>{''.join(self.parts)}</svg>"
        )

    def save(self, path: Path, scale: float = 2.0) -> None:
        import resvg_py

        svg = self.svg()
        png = resvg_py.svg_to_bytes(
            svg_string=svg,
            font_files=[p for family in FONTS.values() for p, _ in family],
            skip_system_fonts=True,
            monospace_family="JetBrains Mono",
            sans_serif_family="Inter",
            font_family="Inter",
            zoom=scale,
        )
        path.with_suffix(".png").write_bytes(png)


def strip_svg_head(body: str) -> str:
    body = re.sub(r"<\?xml[^>]*\?>", "", body)
    body = re.sub(r"<!DOCTYPE[^>]*>", "", body)
    body = re.sub(r"<svg[^>]*>", "", body, count=1)
    return body.replace("</svg>", "")


# ── shared chrome ───────────────────────────────────────────────────────────
class Shot(Canvas):
    """Phone/browser frame + app backdrop used by all three pages."""

    def __init__(self, width: int, height: int, *, url: str) -> None:
        super().__init__(width, height)
        # app background: layered radial glows over a vertical gradient
        self.rect(0, 0, width, height, fill=self.linear([(0, BG2), (1, BG)]))
        for cx, cy, r, color, op in (
            (width * 0.12, -40, width * 0.8, "#afafaf", 0.16),
            (width * 0.92, 10, width * 0.65, "#999999", 0.14),
            (width * 0.5, height * 1.05, width * 0.6, "#adadad", 0.08),
        ):
            g = self.radial([(0, color), (1, "rgba(8,8,8,0)")])
            self.parts.append(
                f'<ellipse cx="{cx:.0f}" cy="{cy:.0f}" rx="{r:.0f}" ry="{r * 0.62:.0f}" fill="{g}" opacity="{op}"/>'
            )
        self.chrome_h = 64
        # browser chrome bar
        self.rect(0, 0, width, self.chrome_h, fill="rgba(17,17,17,0.92)")
        self.line(0, self.chrome_h, width, self.chrome_h, stroke="rgba(161,161,161,0.16)")
        for index, color in enumerate(("#808080", "#c0c0c0", "#9c9c9c")):
            self.circle(26 + index * 22, 24, 6.5, fill=color)
        # address pill
        pill_x, pill_y, pill_w, pill_h = 106, 12, width - 132, 24
        self.rect(pill_x, pill_y, pill_w, pill_h, fill="rgba(25,25,25,0.9)", stroke=LINE, r=12)
        self.text(pill_x + 14, pill_y + 16.5, url, family="sans", size=11.5, fill=DIM)
        self.text(pill_x + pill_w - 12, pill_y + 16.5, "no-store", size=10.5, fill=FAINT, anchor="end")

    # rounded card
    def card(
        self, x, y, w, h, *, r: float = 18, fill: str = "rgba(25,25,25,0.78)", stroke: str = LINE
    ) -> None:
        self.parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{r:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
        )

    def chip(
        self,
        x: float,
        y: float,
        label: str,
        *,
        accent: bool = False,
        size: float = 11.5,
        icon: str | None = None,
    ) -> float:
        """A header/link chip, per app.css .chip.

        ``icon`` draws the page's leading glyph: "Download Folder" and
        "Download Station" are anchors with an SVG inside them, which the shot
        drew as bare text.
        """
        m = Measure("sans", 500, size)
        icon_w = 16 if icon else 0
        w = m.width(label) + 24 + (icon_w + 7 if icon else 0)
        h = 26
        if accent:
            self.rect(x, y, w, h, fill="rgba(175,175,175,0.12)", stroke="rgba(175,175,175,0.42)", r=13)
            self.circle(x + 12, y + h / 2, 3.2, fill=ACCENT)
            self.text(x + 20, y + 17, label, weight=500, size=size, fill="#e3e3e3")
            return w
        self.rect(x, y, w, h, fill="rgba(23,23,23,0.72)", stroke=LINE, r=13)
        tx = x + 12
        if icon:
            self.icon(tx, y + h / 2 - icon_w / 2, icon, icon_w, color=DIM)
            tx += icon_w + 7
        self.text(tx, y + 17, label, weight=500, size=size, fill=DIM)
        return w

    def button(
        self,
        x: float,
        y: float,
        label: str,
        *,
        w: float | None = None,
        h: float = 42,
        variant: str = "primary",
        size: float = 13.5,
        icon: str | None = None,
        badge: str | None = None,
    ) -> float:
        m = Measure("sans", 600, size)
        if w is None:
            w = m.width(label) + 40 + (22 if icon else 0) + (52 if badge else 0)
        # These are app.css's .btn rules, not an interpretation of them:
        #   .btn        background #fff            border 1px #fff   color #000
        #   .btn.alt    background #d4d4d4         border #d4d4d4     color #000
        #   .btn.ghost  background transparent     border var(--line-strong)
        # radius 10px, weight 600, 13.5px.
        # The shot used to draw a grey gradient with a 30px glow on the primary
        # button, which is not a button this project has ever shipped: the page
        # is flat white on black, and the screenshots are supposed to be the
        # page. Anything glowing was the old accent system leaking through.
        r = 10
        if variant in ("primary", "alt"):
            fill = "#ffffff" if variant == "primary" else "#d4d4d4"
            self.rect(x, y, w, h, fill=fill, stroke=fill, r=r)
            text_fill = "#000000"
            weight = 600
        else:  # ghost
            self.rect(x, y, w, h, fill="none", stroke=LINE_STRONG, r=r)
            text_fill = TEXT
            weight = 500
        cursor = x + (20 if not badge else 18)
        if icon:
            self.emoji(cursor, y + h / 2 - 9, icon, 18)
            cursor += 22
        self.text(cursor, y + h / 2 + 5, label, weight=weight, size=size, fill=text_fill)
        if badge:
            bw = m.width(badge) + 16
            bx = x + w - bw - 10
            self.rect(bx, y + 9, bw, h - 18, fill="rgba(28,28,28,0.22)", r=8)
            self.text(bx + 8, y + h / 2 + 4, badge, family="mono", weight=700, size=11, fill=text_fill)
        return w

    def qr(self, x: float, y: float, size: float, seed: str = "lfs") -> None:
        """A deterministic, plausible QR: three finder patterns + a module hash."""
        self.rect(x, y, size, size, fill="#ffffff", r=8)
        pad = size * 0.07
        inner = size - pad * 2
        n = 29
        cell = inner / n
        seed_value = sum(ord(c) * (i + 7) for i, c in enumerate(seed))

        def finder(ox: float, oy: float) -> None:
            self.rect(ox, oy, cell * 7, cell * 7, fill="#080808")
            self.rect(ox + cell, oy + cell, cell * 5, cell * 5, fill="#ffffff")
            self.rect(ox + cell * 2, oy + cell * 2, cell * 3, cell * 3, fill="#080808")

        finder(x + pad, y + pad)
        finder(x + pad + inner - cell * 7, y + pad)
        finder(x + pad, y + pad + inner - cell * 7)
        value = seed_value
        for row in range(n):
            for col in range(n):
                in_finder = (row < 8 and col < 8) or (row < 8 and col > n - 9) or (row > n - 9 and col < 8)
                if in_finder:
                    continue
                value = (value * 1103515245 + 12345) & 0x7FFFFFFF
                if (value >> 16) % 100 < 46:
                    self.rect(
                        x + pad + col * cell, y + pad + row * cell, cell * 1.02, cell * 1.02, fill="#080808"
                    )

    def progress(
        self, x: float, y: float, w: float, ratio: float, *, h: float = 8, color: str = ACCENT
    ) -> None:
        self.rect(x, y, w, h, fill="rgba(161,161,161,0.16)", r=h / 2)
        grad = self.linear([(0, color), (1, ACCENT2)], x1=0, y1=0, x2=1, y2=0)
        self.rect(x, y, max(h, w * ratio), h, fill=grad, r=h / 2)

    def pill_input(
        self, x: float, y: float, w: float, value: str, *, h: float = 34, mono: bool = True
    ) -> None:
        self.rect(x, y, w, h, fill="rgba(8,8,8,0.66)", stroke=LINE_STRONG, r=10)
        self.text(x + 12, y + h / 2 + 4.5, value, family="mono" if mono else "sans", size=12, fill="#d4d4d4")
