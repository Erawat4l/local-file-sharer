"""Themes: dark, light and mono.

The palette is greyscale on purpose. A file sharer is read on a phone over
sshd, in Termux, in a bright room and a dark one; colour there is noise that
competes with the one thing that must stand out — the link. So every role is a
level of grey, and the accent is white (dark) or black (light), which gives
maximum contrast instead of maximum hue.

A :class:`Theme` is a flat set of named styles (``primary``, ``accent``,
``muted`` …) so widgets never hard-code colours. Gradients are produced from a
small stop list and interpolated in RGB, which works on any 256-colour or
true-colour terminal because Rich degrades automatically.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from rich.style import Style
from rich.text import Text

THEME_NAMES = ("dark", "light", "mono")

RGB = tuple[int, int, int]


def parse_color(value: str) -> RGB | None:
    """Parse ``#rrggbb`` / ``rrggbb`` into RGB, else None for named colours."""
    text = value.strip().lstrip("#")
    if len(text) == 3 and all(c in "0123456789abcdefABCDEF" for c in text):
        text = "".join(c * 2 for c in text)
    if len(text) == 6 and all(c in "0123456789abcdefABCDEF" for c in text):
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    return None


NAMED_ACCENTS: dict[str, RGB] = {
    # The product is monochrome; these two are the accents that are not a hue.
    "white": (245, 245, 245),
    "grey": (163, 163, 163),
    "cyan": (34, 211, 238),
    "teal": (45, 212, 191),
    "blue": (96, 165, 250),
    "indigo": (129, 140, 248),
    "violet": (167, 139, 250),
    "purple": (192, 132, 252),
    "magenta": (232, 121, 249),
    "pink": (244, 114, 182),
    "rose": (251, 113, 133),
    "red": (248, 113, 113),
    "orange": (251, 146, 60),
    "amber": (251, 191, 36),
    "yellow": (250, 204, 21),
    "lime": (163, 230, 53),
    "green": (74, 222, 128),
    "emerald": (52, 211, 153),
    "sky": (56, 189, 248),
}

#: Named colour usable directly as a Rich style when it is not a hex value.
NAME_TO_RICH = {
    "white": "bright_white",
    "grey": "grey70",
    "cyan": "bright_cyan",
    "teal": "turquoise2",
    "blue": "bright_blue",
    "indigo": "medium_purple2",
    "violet": "medium_purple1",
    "purple": "purple",
    "magenta": "magenta",
    "pink": "hot_pink",
    "rose": "indian_red1",
    "red": "red1",
    "orange": "dark_orange",
    "amber": "orange1",
    "yellow": "yellow1",
    "lime": "green_yellow",
    "green": "green1",
    "emerald": "spring_green2",
    "sky": "sky_blue1",
}


def resolve_accent(name: str) -> tuple[str, RGB]:
    """Return ``(rich_colour, rgb)`` for a config accent value."""
    key = (name or "white").strip().lower()
    if key in NAMED_ACCENTS:
        return NAME_TO_RICH[key], NAMED_ACCENTS[key]
    rgb = parse_color(key)
    if rgb:
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}", rgb
    return "bright_white", NAMED_ACCENTS["white"]


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def rgb_to_style(rgb: RGB, *, bold: bool = False, dim: bool = False) -> Style:
    return Style(color=f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}", bold=bold, dim=dim)


def gradient_rgb(stops: Sequence[RGB], position: float) -> RGB:
    """Sample a multi-stop gradient at ``position`` in ``0..1``."""
    if not stops:
        return (255, 255, 255)
    if len(stops) == 1:
        return stops[0]
    position = max(0.0, min(1.0, position))
    scaled = position * (len(stops) - 1)
    idx = min(int(scaled), len(stops) - 2)
    return lerp(stops[idx], stops[idx + 1], scaled - idx)


@dataclass(frozen=True)
class Theme:
    name: str = "dark"
    accent_name: str = "white"
    accent: str = "bright_white"
    accent_rgb: RGB = NAMED_ACCENTS["white"]
    primary: str = "bold bright_white"
    secondary: str = "grey93"
    muted: str = "grey54"
    faint: str = "grey35"
    success: str = "bold green1"
    warning: str = "bold orange1"
    danger: str = "bold red1"
    info: str = "bold bright_white"
    border: str = "grey35"
    border_soft: str = "grey23"
    panel: str = "on grey7"
    surface: str = "on grey11"
    highlight: str = "bold black on bright_white"
    selection: str = "bold bright_white on grey23"
    key: str = "bold bright_black on grey30"
    link: str = "underline bright_white"
    gradient_stops: tuple[RGB, ...] = field(default_factory=tuple)
    logo_stops: tuple[RGB, ...] = field(default_factory=tuple)

    # convenience ------------------------------------------------------------
    @property
    def is_light(self) -> bool:
        return self.name == "light"

    def style(self, role: str) -> str:
        return getattr(self, role, self.secondary)

    def gradient(self, text: str, *, offset: float = 0.0, bold: bool = True, span: float = 1.0) -> Text:
        """Colour *text* character by character along the theme gradient."""
        out = Text()
        length = max(1, len(text) - 1)
        stops = self.gradient_stops or (self.accent_rgb,)
        for index, char in enumerate(text):
            pos = (index / length * span + offset) % 1.0
            out.append(char, style=rgb_to_style(gradient_rgb(list(stops), pos), bold=bold))
        return out

    def swatch(self) -> Text:
        stops = self.gradient_stops or (self.accent_rgb,)
        block = Text()
        for index in range(len(stops) * 4):
            block.append("█", style=rgb_to_style(gradient_rgb(list(stops), index / (len(stops) * 4 - 1))))
        return block


def _greys(bright: bool) -> dict[str, str]:
    """Grey levels for a dark (light text) or light (dark text) terminal."""
    return {
        "primary": "bold bright_white" if bright else "bold black",
        "secondary": "grey93" if bright else "grey15",
        "muted": "grey58" if bright else "grey30",
        # Faint is for hints and rules only. The gif draws the description
        # column at the same brightness as the names (#E4EBF1); grey35 made it
        # look disabled, so this is near-white and the *muted* role carries the
        # dimmer text.
        "faint": "grey89" if bright else "grey50",
        "border": "grey37" if bright else "grey50",
        "border_soft": "grey27" if bright else "grey66",
        "panel": "on grey7" if bright else "on grey85",
        "surface": "on grey11" if bright else "on grey93",
        "selection": "bold bright_white on grey23" if bright else "bold black on grey82",
        # The gif draws the key as plain grey text, not a filled chip: no
        # background, so the gutter reads as a column of numbers rather than a
        # row of buttons.
        "key": "grey62" if bright else "grey39",
    }


#: The wordmark gradient, in greys only.
#:
#: It used to be blue into cyan (#62B7F6 -> #41D9F0), taken from the first
#: demo gif. The whole product is monochrome now -- terminal, web pages and
#: the gif -- so the sweep is brightness instead of hue: white through mid
#: grey and back. Names kept for compatibility with the docs and tests.
LOGO_BLUE: RGB = (245, 245, 245)  # was #62B7F6; now the bright end
LOGO_CYAN: RGB = (120, 120, 120)  # was #41D9F0; now the dim end
SUBTITLE_BLUE: RGB = (163, 163, 163)  # was #49BBE5; now the subtitle grey


def _build_dark(accent: str = "white") -> Theme:
    """Dark terminal: black, white and greys, with one weight of accent.

    The *default* accent is white, so the product ships monochrome. ``accent``
    is still honoured when someone sets one: it used to be ignored here, which
    made `lfs config set accent cyan` a no-op and was its own small lie.
    """
    g = _greys(bright=True)
    accent_style, accent_rgb = resolve_accent(accent)
    return Theme(
        name="dark",
        # Headings and the ` LFS ` badge are told apart from body text by
        # weight, not by hue; the badge is a filled block so it still reads.
        accent_name=accent,
        accent=f"bold {accent_style}",
        accent_rgb=accent_rgb,
        primary=g["primary"],
        secondary=g["secondary"],
        muted=g["muted"],
        faint=g["faint"],
        # status stays readable without a hue: weight and brightness, not colour
        success="bold bright_white",
        warning="bold grey93",
        danger="bold bright_white reverse",
        info="bright_white",
        border=g["border"],
        border_soft=g["border_soft"],
        panel=g["panel"],
        surface=g["surface"],
        highlight="bold black on bright_white",
        selection=g["selection"],
        key=g["key"],
        link="underline bright_white",
        gradient_stops=(LOGO_BLUE, LOGO_CYAN, LOGO_BLUE),
        logo_stops=(LOGO_BLUE, LOGO_CYAN, LOGO_BLUE),
    )


def _build_light(accent: str = "black") -> Theme:
    """Light terminal: black is the accent, greys carry the structure."""
    g = _greys(bright=False)
    return Theme(
        name="light",
        accent_name="black",
        accent="bold black",
        accent_rgb=(0, 0, 0),
        primary=g["primary"],
        secondary=g["secondary"],
        muted=g["muted"],
        faint=g["faint"],
        success="bold black",
        warning="bold grey15",
        danger="bold black reverse",
        info="black",
        border=g["border"],
        border_soft=g["border_soft"],
        panel=g["panel"],
        surface=g["surface"],
        highlight="bold white on black",
        selection=g["selection"],
        key=g["key"],
        link="underline black",
        gradient_stops=((0, 0, 0), (110, 110, 110), (0, 0, 0)),
        logo_stops=((0, 0, 0), (90, 90, 90), (0, 0, 0)),
    )


def _build_mono(accent: str = "white") -> Theme:
    """Monochrome: no grey filler at all — pure black and white.

    Only for terminals that render bold/reverse well; the structure comes from
    borders and spacing, and nothing is dimmed into unreadability.
    """
    return Theme(
        name="mono",
        accent_name="white",
        accent="bold bright_white",
        accent_rgb=(255, 255, 255),
        primary="bold bright_white",
        secondary="bright_white",
        muted="white",
        faint="grey70",
        success="bold bright_white",
        warning="bright_white",
        danger="bold bright_white reverse",
        info="bright_white",
        border="white",
        border_soft="grey42",
        panel="",
        surface="",
        highlight="bold black on bright_white",
        selection="reverse",
        key="bold reverse",
        link="underline bright_white",
        gradient_stops=((255, 255, 255), (255, 255, 255)),
        logo_stops=((255, 255, 255), (255, 255, 255)),
    )


_BUILDERS = {"dark": _build_dark, "light": _build_light, "mono": _build_mono}


def build_theme(name: str = "dark", accent: str = "white") -> Theme:
    builder = _BUILDERS.get((name or "dark").lower(), _build_dark)
    return builder(accent or "white")


def cycle_theme(current: str) -> str:
    names = list(THEME_NAMES)
    try:
        idx = names.index(current)
    except ValueError:
        idx = 0
    return names[(idx + 1) % len(names)]


def detect_light_background() -> bool:
    """Guess whether the terminal has a light background."""
    for var in ("COLORFGBG", "TERM_BACKGROUND"):
        raw = os.environ.get(var, "")
        if not raw:
            continue
        if var == "COLORFGBG":
            parts = raw.split(";")
            try:
                return int(parts[-1]) >= 7
            except ValueError:
                continue
        if raw.lower() in ("light", "white"):
            return True
    return False


def default_theme_name() -> str:
    if os.environ.get("NO_COLOR"):
        return "dark"
    return "light" if detect_light_background() else "dark"


def load_theme(cfg, state=None) -> Theme:  # noqa: ANN001 - avoids circular import
    name = (getattr(cfg, "theme", "") or "").strip() or (getattr(state, "theme", "") if state else "")
    if not name:
        name = default_theme_name()
    accent = getattr(cfg, "accent", "white")
    return build_theme(name, accent)


def preview_styles(theme: Theme) -> Iterable[tuple[str, str]]:
    """Small palette listing used by ``lfs theme --list``."""
    for role in ("primary", "secondary", "muted", "accent", "success", "warning", "danger", "info"):
        yield role, theme.style(role)
