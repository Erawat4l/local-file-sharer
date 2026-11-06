"""Guards for the screenshot renderer's two silent-failure modes.

Both were found by looking at the committed PNGs rather than at the code, so
they get tests. The renderer is a maintainer tool, not part of the package —
these tests skip cleanly if ``tools/screenshots`` is absent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools" / "screenshots"

pytest.importorskip("rich")
pytest.importorskip("fontTools", reason="the screenshot toolchain needs fontTools")

if not (TOOLS / "termsvg.py").exists():  # pragma: no cover - tool is optional
    pytest.skip("screenshot toolchain not present", allow_module_level=True)


def _load(name: str):
    """Import a tool module without putting ``tools/screenshots`` on sys.path."""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location(f"_shots_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # dataclasses look the module up in sys.modules while decorating, so register
    # it before executing or the @dataclass in termsvg.py blows up.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


termsvg = _load("termsvg")


def test_missing_twemoji_is_reported_not_swallowed(monkeypatch, tmp_path, capsys):
    """An emoji with no SVG must warn once, or shots silently grow tofu boxes."""
    monkeypatch.setattr(termsvg, "TWEMOJI", tmp_path)
    monkeypatch.setattr(termsvg, "_MISSING_EMOJI", set())

    assert termsvg._twemoji("🤖") is None
    assert termsvg._twemoji("🤖") is None

    err = capsys.readouterr().err
    assert err.count("no Twemoji SVG") == 1, "warn once per glyph, not once per row"
    assert "🤖" in err or "1f916" in err


def test_twemoji_is_found_when_the_directory_is_right(monkeypatch):
    """The happy path: a real lookup must return markup, not None."""
    twemoji = Path(termsvg.TWEMOJI)
    if not (twemoji / "1f916.svg").exists():
        pytest.skip("Twemoji inputs are not installed (LFS_SHOT_INPUTS)")

    markup = termsvg._twemoji("🤖")
    assert markup is not None
    assert "svg" not in markup.split()[0] if markup.split() else True
    assert "<path" in markup or "<g" in markup


def test_twemoji_ignores_ascii():
    assert termsvg._twemoji("A") is None
    assert termsvg._twemoji("") is None


def test_to_svg_pads_every_row_to_cols():
    """Without ``cols`` each frame sized itself, so GIF frames came out different
    widths — a corruption that only shows up in the animation."""
    rows = [
        [termsvg.Cell("a", "#fff", None, False, False)],
        [termsvg.Cell("b", "#fff", None, False, False)] * 5,
    ]
    svg_narrow = termsvg.to_svg(rows, title="t", chrome=False)
    svg_padded = termsvg.to_svg(rows, title="t", chrome=False, cols=8)

    def width_of(svg: str) -> float:
        import re

        return float(re.search(r'<svg[^>]*width="([\d.]+)"', svg).group(1))

    assert width_of(svg_padded) > width_of(svg_narrow)

    # Two different shapes must still produce the same canvas when cols is fixed.
    other = [[termsvg.Cell("c", "#fff", None, False, False)] * 3]
    assert width_of(termsvg.to_svg(other, title="t", chrome=False, cols=8)) == width_of(svg_padded)


def test_grid_measures_every_glyph_as_at_least_one_cell():
    from rich.text import Text

    rows = termsvg.to_grid(Text("a\u200bb界"), width=8)
    assert all(cell.width >= 1 for row in rows for cell in row)
    assert "".join(c.char for c in rows[0]).startswith("a")


def test_the_gif_shows_the_menu_the_code_draws(monkeypatch):
    """The demo gif has to depict the menu this code renders today.

    A gif is pixels: nothing in a diff or a test can read it, so it silently
    advertised ``start <path>`` and ``switch dark / light / neon`` long after
    the program had been renamed to say ``lfs <path>`` and ``light / mono``.
    ``demo_gif.py`` now also writes the menu as text; this compares that
    record against a fresh render, so the animation cannot drift again.
    """
    recorded = REPO / "docs" / "assets" / "demo-menu.txt"
    if not recorded.exists():  # pragma: no cover - committed alongside the gif
        pytest.skip("docs/assets/demo-menu.txt is not present")

    import io

    from rich.console import Console

    from local_file_sharer import menu
    from local_file_sharer.config import load_config
    from local_file_sharer.state import State
    from local_file_sharer.ui.console import set_console, set_theme
    from local_file_sharer.ui.theme import build_theme

    # conftest.py sets NO_COLOR=1 for every test; that switches the menu to its
    # ASCII icon set, which is not what the gif shows. The recording was made
    # with colour enabled, so this comparison has to be too.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("LFS_ASCII_ICONS", raising=False)

    # set_console/set_theme/_STATE are module globals; leaving them swapped
    # makes later tests render against this console and fail at a distance.
    from local_file_sharer.ui import console as console_module

    monkeypatch.setattr(console_module, "_console", console_module._console, raising=False)
    monkeypatch.setattr(console_module, "_theme", console_module._theme, raising=False)
    monkeypatch.setattr(menu, "_STATE", menu._STATE, raising=False)

    theme = build_theme("dark", "cyan")
    set_theme(theme)
    buffer = io.StringIO()
    con = Console(
        file=buffer, width=96, force_terminal=False, emoji=True,
        highlight=False, soft_wrap=False, color_system=None,
    )
    set_console(con)
    state = State.load(None)
    state.data["totals"] = {
        "bytes_sent": 128_849_018_880, "files_sent": 412, "shares": 37, "downloads": 96,
    }
    menu._STATE = state  # noqa: SLF001 - the renderer reads the module global
    ctx = menu.MenuContext(config=load_config(respect_env=False), state=state, animate=False)
    con.print(menu.render_menu(ctx))
    today = buffer.getvalue()

    # The clock and hostname differ per machine; every other line must match.
    def stable(text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            if "·" in line and ("LFS" in line or "LOCAL FILE SHARER" in line):
                continue
            out.append(line)
        return out

    assert stable(today) == stable(recorded.read_text(encoding="utf-8")), (
        "docs/assets/demo.gif is stale — rerun tools/screenshots/demo_gif.py"
    )
