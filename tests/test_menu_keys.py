"""Every key on the menu must do its job without raising.

A tuple in ``ACTIONS`` grew a field and two call sites still unpacked five, so
pressing `c` raised "too many values to unpack" in the middle of the menu. The
render path was covered; the *dispatch* path was not. These drive the real
``run_menu`` loop with keys instead of just rendering it.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from local_file_sharer import menu
from local_file_sharer.config import load_config
from local_file_sharer.state import State
from local_file_sharer.ui.console import set_theme
from local_file_sharer.ui.theme import build_theme

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _theme():
    set_theme(build_theme("dark"))


def test_every_action_is_a_complete_entry():
    """One shape for every row: key, label, action, emoji, fallback, help."""
    for entry in menu.ALL_ACTIONS:
        assert len(entry) == 6, f"{entry!r} has {len(entry)} fields, expected 6"
        key, label, action, icon, plain, desc = entry
        assert key and label and action and icon and plain and desc


def test_every_key_resolves_to_its_action():
    for key, _label, action, *_rest in menu.ALL_ACTIONS:
        assert menu._resolve_action(key) == action


def test_alias_keys_still_resolve():
    # run_menu lowercases the answer before resolving it, so these are the
    # forms that actually reach _resolve_action.
    for alias, expected in (("exit", "quit"), ("e", "quit"), ("quit", "quit")):
        assert menu._resolve_action(alias.lower()) == expected
    for mode in ("download", "serve", "stream", "torrent"):
        assert menu._resolve_action(mode) == mode


def test_unknown_key_returns_none_rather_than_raising():
    assert menu._resolve_action("zzzz-this-is-not-a-key") is None


def test_help_screen_renders_for_every_entry():
    """`?` walks EXTRA_ACTIONS, which also unpacked five fields.

    ``_help`` prints to the shared console, so this renders the pieces it
    prints rather than the return value (which is None).
    """
    from local_file_sharer.ui.components import HELP_MAIN

    out = Console(file=io.StringIO(), width=90, force_terminal=False)
    out.print(menu.render_extra_commands())
    out.print(HELP_MAIN)
    text = out.file.getvalue()
    # `?` extends the menu: the four modes are already on screen, so only the
    # extra commands are listed here.
    for _key, label, *_rest in menu.EXTRA_ACTIONS:
        assert label in text, f"{label} missing from the help screen"


@pytest.mark.parametrize("key", [entry[0] for entry in menu.ALL_ACTIONS])
def test_pressing_each_key_does_not_crash_the_loop(key, monkeypatch):
    """The reported bug: `c` raised ValueError from inside run_menu.

    Actions with side effects are stubbed; what is under test is that the
    dispatch table can reach them at all.
    """
    reached = {}
    for name in ("_pick_and_share", "_recent", "_favorites", "_config_menu", "_theme_menu", "_help"):
        monkeypatch.setattr(menu, name, lambda ctx, *a, _n=name, **k: reached.setdefault(_n, True))
    monkeypatch.setattr(menu, "render_menu", lambda ctx: "")

    import local_file_sharer.doctor as doctor
    import local_file_sharer.updater as updater

    monkeypatch.setattr(doctor, "run_doctor", lambda *a, **k: 0)
    monkeypatch.setattr(updater, "run_update", lambda *a, **k: 0)
    monkeypatch.setattr(menu, "_reload_config", lambda cfg: cfg)

    answers = iter([key, "q"])

    def fake_prompt(*_a, **_k):
        try:
            return _FakeAnswer(next(answers))
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(menu.Prompt, "ask", fake_prompt)
    ctx_state = State.load()
    rc = menu.run_menu(config=load_config(), state=ctx_state, animate=False)
    assert rc == 0


class _FakeAnswer(str):
    """Prompt.ask returns a string; unicode helpers are used on it."""


# 62-68 is where the bug lived: the key column was dropped outright below 64
# and squeezed to a lone ellipsis up to 68, so the menu listed no keys at all.
@pytest.mark.parametrize("width", [44, 46, 52, 60, 62, 63, 64, 65, 66, 68, 70, 80])
def test_a_narrow_phone_keeps_the_key_and_icon_columns(width, monkeypatch):
    """The bug the user hit on a 60-column phone.

    The section rows were a ``Table.grid`` with fixed-width columns. Rich drops
    columns that do not fit, and on a narrow terminal it dropped the key *and*
    the icon -- every row read " Download  forced download ...", with nothing to
    press. Rows are laid out by hand now, so the two columns that matter are
    always present and only the description is trimmed.
    """
    monkeypatch.setattr(menu, "screen_width", lambda default=100: width)
    buf = io.StringIO()
    Console(file=buf, width=width, force_terminal=False).print(menu.render_menu(_ctx()))
    out = buf.getvalue()

    for key, label, _action, _icon, _plain, _desc in menu.ALL_ACTIONS:
        # Match the row, not just any line containing the name: the section
        # heading " Recent & Favourites" also contains " Recent", and it comes
        # first, so the old lookup tested the heading and reported the action
        # as having lost its key.
        row = next(
            (line for line in out.splitlines() if line.strip().startswith(f"{key} ") and label in line),
            None,
        )
        assert row is not None, f"no row for {label} at width {width}"
        # the key is what the user presses: it must be on the line
        assert row.strip().startswith(key.strip()), f"{label} lost its key at {width}"
        # and the icon must still be between the key and the name
        head, _, _tail = row.partition(label)
        assert head.strip() != key.strip(), f"{label} lost its icon at {width}"


@pytest.mark.parametrize("width", [44, 46, 52, 60, 70, 80, 100])
def test_no_row_is_wider_than_the_terminal(width, monkeypatch):
    """A row one cell too wide wraps, and wrapping doubles the menu's height."""
    from local_file_sharer.fmt import cell_len

    monkeypatch.setattr(menu, "screen_width", lambda default=100: width)
    buf = io.StringIO()
    Console(file=buf, width=width, force_terminal=False).print(menu.render_menu(_ctx()))
    for line in buf.getvalue().splitlines():
        assert cell_len(line) <= width, f"{line!r} is {cell_len(line)} cells at {width}"


def _ctx() -> menu.MenuContext:
    return menu.MenuContext(config=load_config(), state=State.load())


def test_every_menu_icon_is_a_glyph_the_font_can_draw():
    """A mark the shipped face does not have renders as an empty box.

    Six of them did -- ⤓ ◷ ★ ⚙ ✚ ◑ -- so the icon column was a row of tofu
    rectangles on the very terminals the screenshots are made from. Checked
    against the font the screenshots actually use, which is also the face the
    README tells people to install.
    """
    font_path = ROOT / "tools" / "screenshots" / "inputs" / "fonts" / "JetBrainsMono-Regular.ttf"
    if not font_path.exists():  # pragma: no cover - fetched by the shot tooling
        pytest.skip("the screenshot fonts are not present")

    from fontTools.ttLib import TTFont

    cmap = set(TTFont(str(font_path)).getBestCmap())
    missing = [
        (key, icon) for key, _label, _action, icon, _plain, _desc in menu.ALL_ACTIONS if ord(icon) not in cmap
    ]
    assert not missing, f"these icons have no glyph in the shipped font: {missing}"
