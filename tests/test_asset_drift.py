"""The committed screenshots must match the code and templates they depict.

Nothing else caught this: a GIF is pixels and a hand-drawn PNG is not a render
of the HTML, so both drifted silently. ``demo.gif`` advertised ``start <path>``
and ``switch dark / light / neon`` after the program had been renamed to say
``lfs <path>`` and ``light / mono``; the web shots drew a ``Filter files…`` box,
a ``powered by pure Python`` footer and emoji transport controls that no
template contains.

These live here rather than in ``test_screenshots.py`` on purpose: that module
skips itself when ``fontTools`` is missing, and CI installs ``.[dev]`` — which
does not include it. Guards that only run on a machine that happens to have the
renderers are not guards.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "docs" / "assets"

pytest.importorskip("rich")


# ── the terminal menu in docs/assets/demo.gif ───────────────────────────────
def test_the_gif_shows_the_menu_the_code_draws(monkeypatch):
    """The demo gif has to depict the menu this code renders today.

    ``demo_gif.py`` writes the menu it drew to ``docs/assets/demo-menu.txt``.
    Render the menu fresh and compare, so the animation cannot drift again.
    """
    recorded = ASSETS / "demo-menu.txt"
    if not recorded.exists():  # pragma: no cover - committed alongside the gif
        pytest.skip("docs/assets/demo-menu.txt is not present")

    from rich.console import Console

    from local_file_sharer import menu
    from local_file_sharer.config import load_config
    from local_file_sharer.state import State
    from local_file_sharer.ui import console as console_module
    from local_file_sharer.ui.console import set_console, set_theme
    from local_file_sharer.ui.theme import build_theme

    # conftest.py sets NO_COLOR=1 for every test; that switches the menu to its
    # ASCII icon set, which is not what the gif shows.
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("LFS_ASCII_ICONS", raising=False)

    # set_console/set_theme/_STATE are module globals; leaving them swapped
    # makes later tests render against this console and fail at a distance.
    monkeypatch.setattr(console_module, "_console", console_module._console, raising=False)
    monkeypatch.setattr(console_module, "_theme", console_module._theme, raising=False)
    monkeypatch.setattr(menu, "_STATE", menu._STATE, raising=False)

    set_theme(build_theme("dark", "cyan"))
    buffer = io.StringIO()
    con = Console(
        file=buffer,
        width=96,
        force_terminal=False,
        emoji=True,
        highlight=False,
        soft_wrap=False,
        color_system=None,
        # Same reason as ui/console.py: without it Rich substitutes square box
        # corners on a legacy Windows console and the menu no longer matches
        # the gif it is being compared against.
        safe_box=False,
    )
    set_console(con)
    state = State.load(None)
    state.data["totals"] = {
        "bytes_sent": 128_849_018_880,
        "files_sent": 412,
        "shares": 37,
        "downloads": 96,
    }
    menu._STATE = state  # noqa: SLF001 - the renderer reads the module global
    con.print(
        menu.render_menu(menu.MenuContext(config=load_config(respect_env=False), state=state, animate=False))
    )
    today = buffer.getvalue()

    # The "this device" panel is machine state, not copy: hostname, LAN address
    # and whether tailscale happens to be installed. All of it varies by host
    # (this test failed on every CI runner until it was excluded, because the
    # recording was made on e2b.local / 169.254.0.21 with no tailscale). What is
    # left -- the header chrome, the mode list, the descriptions and the footer
    # -- is what actually went stale in the gif, and is what this compares.
    def stable(text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            if "·" in line and ("LFS" in line or "LOCAL FILE SHARER" in line):
                continue
            stripped = line.strip(" │")
            if stripped.startswith(
                ("device ", "network ", "public ", "tailscale ", "theme ", "sent so far ")
            ):
                continue
            out.append(line)
        return out

    want = stable(recorded.read_text(encoding="utf-8"))
    got = stable(today)
    if got != want:
        # Show the first differing line: on a remote runner the assertion is the
        # only thing anyone can read, and "lists differ" is not actionable.
        # zip(strict=...) is 3.10+, and this package supports 3.9.
        first = f"{len(got)} lines drawn vs {len(want)} recorded"
        for index in range(min(len(got), len(want))):
            if got[index] != want[index]:
                first = f"line {index}: drawn={got[index]!r} recorded={want[index]!r}"
                break
        raise AssertionError(f"docs/assets/demo.gif is stale — rerun tools/screenshots/demo_gif.py ({first})")


def test_the_gif_text_is_recorded_beside_it():
    """A gif whose menu was never recorded cannot be checked at all."""
    gif = ASSETS / "demo.gif"
    recorded = ASSETS / "demo-menu.txt"
    if not gif.exists():  # pragma: no cover
        pytest.skip("demo.gif is not present")
    assert recorded.exists(), (
        "docs/assets/demo-menu.txt is missing — tools/screenshots/demo_gif.py "
        "writes it; without it nothing can tell whether the gif is stale"
    )
    assert len(recorded.read_text(encoding="utf-8").splitlines()) > 10


def test_the_web_gif_is_built_from_the_same_page_builders():
    """docs/assets/web-demo.gif must exist and show the pages, not a mock-up.

    The gif is generated by ``tools/screenshots/web_gif.py`` from the very
    functions that draw the committed PNGs, so there is no separate copy to
    drift. What can rot is the wiring: a renamed builder, or a gif rebuilt from
    a stale module. Check the file is present, is animated, and that its first
    frame is the size the builder draws.
    """
    gif = ASSETS / "web-demo.gif"
    assert gif.exists(), "docs/assets/web-demo.gif is missing — run tools/screenshots/web_gif.py"
    # Pillow renders the screenshots; it is not a test dependency and CI
    # installs only .[dev], so this check skips there. The rest of the module
    # deliberately does not need Pillow: the copy guards are the ones that must
    # run everywhere.
    try:
        from PIL import Image  # noqa: PLC0415 - only needed here
    except ModuleNotFoundError:  # pragma: no cover - depends on the environment
        pytest.skip("Pillow is not installed")

    with Image.open(gif) as image:
        assert getattr(image, "n_frames", 1) > 3, "the web gif is not animated"
        width, height = image.size
    # 1440x900 drawn at whatever SCALE the generator uses; the ratio is what
    # identifies it as a page shot rather than a terminal one.
    assert abs(width / height - 1440 / 900) < 0.02, f"unexpected aspect {width}x{height}"


# ── the three web pages in docs/assets/web-*.png ────────────────────────────
#: (label drawn by a shot, string that must exist in the shipped templates).
#: Curated on purpose: a browser renders these pages and the toolchain draws
#: them by hand, so the risk is copy no template contains. Demo values and
#: anything a ``{{placeholder}}`` or the page's javascript fills in are not
#: covered here — no text comparison can check those without a browser.
_WEB_COPY = [
    ("DOWNLOAD STATION", "text-transform: uppercase"),
    ("Download Station", "Download Station"),
    ("Mode download", "Mode <b>{{mode}}</b>"),
    ("3 Devices", "{{device_word}}"),
    ("Download All", "Download all"),
    ("Everything As .zip", "Everything as .zip"),
    ("Copy curl One-liner", "Copy curl one-liner"),
    ("aria2c -x16", "aria2c -x16"),
    ("scan to open on another device", "scan to open on another device"),
    ("Shared Files", "Shared Files"),
    ("Search name or type — try mp4", "Search name or type — try mp4"),
    ("All", 'data-filter="all"'),
    ("Video", 'data-filter="video"'),
    ("Images", 'data-filter="image"'),
    ("Audio", 'data-filter="audio"'),
    ("Documents", 'data-filter="document"'),
    ("Archives", 'data-filter="archive"'),
    ("Web File Server", "Web File Server"),
    ("Browse ", "Browse "),
    ("Filter in this folder…", "Filter in this folder…"),
    ("Serving", "Serving"),
    ("All Files", "All Files"),
    ("Streaming · Resume · Range", "Streaming · Resume · Range"),
    ("Filter", ">Filter<"),
    ("Select All", "Select All"),
    ("Download Selected", "Download Selected"),
    ("PLAYLIST · 1", "Playlist · {{playlist_count}}"),
    ("buffered", "buffered"),
    ("Streaming with HTTP Range", "Streaming with HTTP Range"),
    ("72 MB/s", "0 B/s"),
    # the player's back link, and the version badge that replaced the build id
    ("1.0.0", 'title="All Files in This Share"'),
    # the five stat rows the player actually renders
    ("transferred", "transferred"),
    ("devices", "devices"),
    ("format", "format"),
    ("shape", "shape"),
]


def _web_templates() -> str:
    web = REPO / "src" / "local_file_sharer" / "web" / "assets"
    return "\n".join(
        (web / name).read_text(encoding="utf-8", errors="ignore")
        for name in ("downloader.html", "server.html", "player.html")
    )


def test_web_screenshots_copy_matches_the_templates():
    """Copy the web shots draw must be copy the shipped pages contain.

    These PNGs are hand-drawn SVG, so they can display wording no template has
    — and did: a "Filter files…" box where the page has kind chips, a
    "powered by pure Python" footer where the page says "+ rich", emoji
    transport controls the player never renders, and a "live / HTTP Range seek
    / mode stream" header on a player whose top line is filename, size, speed,
    back link and build.

    Checked in both directions, so a page whose copy changes fails loudly and
    points at the line that needs updating.
    """
    recorded = ASSETS / "web-text.txt"
    if not recorded.exists():  # pragma: no cover - committed with the PNGs
        pytest.skip("docs/assets/web-text.txt is not present")

    web = REPO / "src" / "local_file_sharer" / "web" / "assets"
    source = (
        _web_templates() + "\n" + (web / "static" / "app.css").read_text(encoding="utf-8", errors="ignore")
    )
    drawn = recorded.read_text(encoding="utf-8")

    for label, origin in _WEB_COPY:
        assert origin in source, (
            f"the templates no longer contain {origin!r} (for label {label!r}) — "
            "if the page changed, update webgen.py and this list"
        )
        assert label in drawn, (
            f"the screenshots no longer draw {label!r} — regenerate with tools/screenshots/webgen.py"
        )


def test_web_screenshots_do_not_revive_retired_copy():
    """The exact strings that were wrong, so fixing them cannot silently regress."""
    recorded = ASSETS / "web-text.txt"
    if not recorded.exists():  # pragma: no cover
        pytest.skip("docs/assets/web-text.txt is not present")
    drawn = recorded.read_text(encoding="utf-8")

    # Whole-line matches: "powered by pure Python" was the bug, but the fixed
    # line legitimately starts with those words, so a substring test cannot tell
    # them apart. Compare what the shot actually printed, line by line.
    lines = {line.strip() for line in drawn.splitlines()}
    retired = [
        "Filter files…",  # a control the downloader does not have
        "powered by pure Python",  # cut: the footer is two words now
        "HTTP Range seek",  # invented player badge
        "mode stream",  # invented player badge
        "FILE BROWSER",  # the page says "Web File Server"
        "seeking works instantly",  # invented overlay subtitle
        "GET /api/manifest",  # developer detail, cut from the footer
        "for maximum speed",  # the aria2c tip, cut
        "zero-copy sendfile",  # protocol brag, cut
        "Every file is served with HTTP Range support",  # cut from the heading
        "never expires",  # the chip is conditional now
        "Download Everything, Fast.",  # the page is two runs, not one string
    ]
    glyphs = ["⏪", "⏩", "🔊"]  # the player draws no emoji controls
    present = [text for text in retired if text in lines]
    present += [glyph for glyph in glyphs if glyph in drawn]
    assert not present, f"retired web copy came back: {present}"
