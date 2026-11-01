"""Theme switching shared by ``start theme`` and the interactive menu."""

from __future__ import annotations

from rich.text import Text

from .config import Config, config_path, set_value
from .errors import ConfigError
from .state import State
from .ui.components import theme_preview
from .ui.console import console, set_theme
from .ui.theme import THEME_NAMES, build_theme


def switch_theme(name: str, config: Config, state: State | None = None, *, persist: bool = True) -> str:
    """Apply *name* immediately and persist it to config + state."""
    lowered = name.strip().lower()
    if lowered not in THEME_NAMES:
        raise ConfigError(
            f"Unknown theme {name!r}",
            hints=[f"Choose one of: {', '.join(THEME_NAMES)}"],
        )
    theme = build_theme(lowered, config.accent)
    set_theme(theme)
    config.theme = lowered
    if persist:
        try:
            set_value("theme", lowered)
        except ConfigError:
            try:
                config.save()
            except OSError:
                pass
    if state is not None:
        state.theme = lowered
    return lowered


def show_theme(theme, *, preview: bool = True) -> None:  # noqa: ANN001
    con = console()
    con.print(
        f"[lfs.muted]theme[/] [lfs.primary]{theme.name}[/]  [lfs.muted]accent[/] [lfs.primary]{theme.accent_name}[/]"
    )
    con.print(Text("available: " + ", ".join(THEME_NAMES), style=theme.muted))
    if preview:
        con.print(theme_preview(theme))


def cycle(config: Config, state: State | None = None) -> str:
    from .ui.theme import cycle_theme  # noqa: PLC0415

    return switch_theme(cycle_theme(config.theme), config, state)


def theme_config_note() -> str:
    return f"themes are saved to {config_path()}"
