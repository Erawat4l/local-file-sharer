"""Themes: a consistent accent colour system for dark, light and neon terminals.

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

THEME_NAMES = ("dark", "light", "neon")

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
    key = (name or "cyan").strip().lower()
    if key in NAMED_ACCENTS:
        return NAME_TO_RICH[key], NAMED_ACCENTS[key]
    rgb = parse_color(key)
    if rgb:
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}", rgb
    return NAME_TO_RICH["cyan"], NAMED_ACCENTS["cyan"]


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
    accent_name: str = "cyan"
    accent: str = "bright_cyan"
    accent_rgb: RGB = NAMED_ACCENTS["cyan"]
    primary: str = "bold bright_white"
    secondary: str = "grey93"
    muted: str = "grey54"
    faint: str = "grey35"
    success: str = "bold green1"
    warning: str = "bold orange1"
    danger: str = "bold red1"
    info: str = "bold bright_cyan"
    border: str = "grey35"
    border_soft: str = "grey23"
    panel: str = "on grey7"
    surface: str = "on grey11"
    highlight: str = "bold black on bright_cyan"
    selection: str = "bold bright_white on grey23"
    key: str = "bold bright_black on grey30"
    link: str = "underline bright_cyan"
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


def _build_dark(accent: str) -> Theme:
    rich_name, rgb = resolve_accent(accent)
    stops = (rgb, lerp(rgb, (140, 120, 255), 0.55), (125, 211, 252))
    return Theme(
        name="dark",
        accent_name=accent,
        accent=rich_name,
        accent_rgb=rgb,
        primary="bold bright_white",
        secondary="grey93",
        muted="grey58",
        faint="grey35",
        success="bold spring_green3",
        warning="bold orange1",
        danger="bold red1",
        info=rich_name,
        border="grey37",
        border_soft="grey27",
        panel="on grey7",
        surface="on grey11",
        highlight=f"bold black on {rich_name}",
        selection="bold bright_white on grey23",
        key="bold bright_black on grey30",
        link=f"underline {rich_name}",
        gradient_stops=stops,
        logo_stops=(rgb, lerp(rgb, (255, 255, 255), 0.35), stops[1]),
    )


def _build_light(accent: str) -> Theme:
    rich_name, rgb = resolve_accent(accent)
    dark_rgb = tuple(max(0, int(c * 0.55)) for c in rgb)  # type: ignore[assignment]
    stops = (dark_rgb, (30, 64, 175), (88, 28, 135))  # type: ignore[arg-type]
    return Theme(
        name="light",
        accent_name=accent,
        accent=rich_name,
        accent_rgb=rgb,
        primary="bold black",
        secondary="grey15",
        muted="grey30",
        faint="grey50",
        success="bold green4",
        warning="bold dark_orange3",
        danger="bold red3",
        info=rich_name,
        border="grey50",
        border_soft="grey66",
        panel="on grey88",
        surface="on grey93",
        highlight=f"bold white on {rich_name}",
        selection="bold black on grey78",
        key="bold white on grey42",
        link=f"underline {rich_name}",
        gradient_stops=stops,
        logo_stops=(dark_rgb, (12, 74, 110), (88, 28, 135)),
    )


def _build_neon(accent: str) -> Theme:
    _, rgb = resolve_accent(accent)
    neon_pink: RGB = (255, 42, 194)
    neon_cyan: RGB = (0, 246, 255)
    neon_lime: RGB = (170, 255, 0)
    stops = (neon_cyan, neon_pink, neon_lime)
    if accent.lower() not in ("cyan", ""):
        stops = (rgb, neon_pink, neon_cyan)
    return Theme(
        name="neon",
        accent_name=accent,
        accent=f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}",
        accent_rgb=rgb,
        primary="bold bright_white",
        secondary="bright_white",
        muted="medium_purple2",
        faint="grey42",
        success="bold spring_green1",
        warning="bold yellow1",
        danger="bold bright_red",
        info=f"bold #{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}",
        border="medium_purple3",
        border_soft="grey27",
        panel="on grey3",
        surface="on grey11",
        highlight="bold black on bright_white",
        selection="bold bright_white on dark_magenta",
        key="bold bright_black on medium_purple3",
        link=f"underline #{neon_cyan[0]:02x}{neon_cyan[1]:02x}{neon_cyan[2]:02x}",
        gradient_stops=stops,
        logo_stops=(neon_cyan, neon_pink, neon_lime),
    )


_BUILDERS = {"dark": _build_dark, "light": _build_light, "neon": _build_neon}


def build_theme(name: str = "dark", accent: str = "cyan") -> Theme:
    builder = _BUILDERS.get((name or "dark").lower(), _build_dark)
    return builder(accent or "cyan")


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
    accent = getattr(cfg, "accent", "cyan")
    return build_theme(name, accent)


def preview_styles(theme: Theme) -> Iterable[tuple[str, str]]:
    """Small palette listing used by ``lfs theme --list``."""
    for role in ("primary", "secondary", "muted", "accent", "success", "warning", "danger", "info"):
        yield role, theme.style(role)
