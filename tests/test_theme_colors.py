"""Every style in every theme must be a colour rich can actually parse.

A typo like ``grey38`` (rich only defines some steps) is invisible until the
screen renders, and then it raises mid-menu. Checking the strings up front is
cheaper than discovering it on a phone.
"""

from __future__ import annotations

import pytest
from rich.errors import StyleSyntaxError
from rich.style import Style

from local_file_sharer.ui.theme import THEME_NAMES, build_theme

STYLE_FIELDS = (
    "accent",
    "primary",
    "secondary",
    "muted",
    "faint",
    "success",
    "warning",
    "danger",
    "info",
    "border",
    "border_soft",
    "panel",
    "surface",
    "highlight",
    "selection",
    "key",
    "link",
)


@pytest.mark.parametrize("name", THEME_NAMES)
@pytest.mark.parametrize("field", STYLE_FIELDS)
def test_style_parses(name, field):
    theme = build_theme(name)
    value = getattr(theme, field)
    try:
        Style.parse(value)
    except StyleSyntaxError as exc:  # pragma: no cover - the failure message
        pytest.fail(f"{name}.{field} = {value!r} does not parse: {exc}")


@pytest.mark.parametrize("name", THEME_NAMES)
def test_gradients_are_rgb_triples(name):
    theme = build_theme(name)
    for stops in (theme.gradient_stops, theme.logo_stops):
        for stop in stops:
            assert len(stop) == 3
            assert all(isinstance(v, int) and 0 <= v <= 255 for v in stop)
