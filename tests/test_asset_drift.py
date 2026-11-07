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
import re
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
    # recording was made on the build sandbox's hostname / link-local IP with no
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
    # The brand block reads as one run in the browser (a space, not a newline),
    # and the caption is uppercased by CSS -- so the label is the pair.
    ("LOCAL FILE SHARER DOWNLOAD STATION", "text-transform: uppercase"),
    ("DOWNLOAD STATION", "Download Station"),  # the caption, uppercased by CSS
    ("Mode download", "Mode <b>{{mode}}</b>"),
    ("1 Device", "{{device_word}}"),
    ("Download all", "Download all"),
    ("Everything as .zip", "Everything as .zip"),
    ("Copy curl one-liner", "Copy curl one-liner"),
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
    ("LOCAL FILE SHARER WEB FILE SERVER", "Web File Server"),
    ("Browse ", "Browse "),
    ("Filter in this folder…", "Filter in this folder…"),
    ("Serving", "Serving"),
    ("All Files", "All Files"),
    ("Resume · Range", "Resume · Range"),
    ("Filter", ">Filter<"),
    ("Select All", "Select All"),
    ("Download Selected", "Download Selected"),
    # the count is the demo share's four playable files, uppercased by CSS
    ("PLAYLIST · 4", "Playlist · {{playlist_count}}"),
    ("buffered", "buffered"),
    ("Streaming with HTTP Range", "Streaming with HTTP Range"),
    ("0 B/s", "0 B/s"),
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
        "Download everything, fast.",  # the old sentence-case heading
    ]
    glyphs = ["⏪", "⏩", "🔊"]  # the player draws no emoji controls
    present = [text for text in retired if text in lines]
    present += [glyph for glyph in glyphs if glyph in drawn]
    assert not present, f"retired web copy came back: {present}"


#: Every sample file, folder, host and token in the recording tools, the shot
#: fixtures and the docs. These were a real device's contents: a game folder, a
#: build number, two clips named after people, a laptop name, the build
#: sandbox's hostname. They leaked into a public repository, and the whole set
#: must stay out. Add a name here whenever one is retired.
RETIRED_REAL_NAMES = (
    "FREE FIRE",
    "KRATOS",
    "ISHAN",
    "Agon-1.4",
    "VID-2026",
    "index_287",
    "ab_book",
    "roms/",
    "aurora-laptop",
    "Aurora-Film",
    "Termux-notes",
    "live-set-2026",
    "moodboard",
    "holiday-photos",
    "IMG_2026",
    "wedding",
    "e2b.local",
    "b45d56a6",
)


def test_no_real_file_names_are_published():
    """Nothing committed may name a real file, folder or machine.

    The screenshots, the gif and the tests are all published, and the person
    who runs this program is not the only person who reads the repository. A
    sample row that says "FREE FIRE MAX/" is somebody's phone, not an example.

    Scans the tree rather than the assets only: the names got back in through
    the generator scripts and a test fixture, not through the images.
    """
    repo = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", "inputs"}
    suffixes = {
        ".py",
        ".md",
        ".txt",
        ".html",
        ".css",
        ".js",
        ".toml",
        ".json",
        ".yml",
        ".yaml",
        ".cfg",
        ".sh",
    }
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or set(path.parts) & skip_dirs:
            continue
        if path.suffix.lower() not in suffixes:
            continue
        if path.name == "test_asset_drift.py":
            continue  # this file *is* the list; it would always match itself
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file
            continue
        for name in RETIRED_REAL_NAMES:
            if name in text:
                offenders.append(f"{path.relative_to(repo)}: {name}")
    assert not offenders, "real device names are published in:\n  " + "\n  ".join(offenders)


def _player_template() -> str:
    repo = Path(__file__).resolve().parents[1]
    return (repo / "src" / "local_file_sharer" / "web" / "assets" / "player.html").read_text(encoding="utf-8")


def test_the_player_shot_draws_the_page_s_own_icons():
    """The shot's transport glyphs must be the page's path data, not lookalikes.

    This is the failure that prompted the check: the screenshot and the gif drew
    their own round controls -- different sizes, a lighter border, the controls
    in a different order, and a "+" where the download button belongs. Nothing
    compared the drawing to the markup, so a picture of a page that never
    existed could sit in the README indefinitely.

    The shot imports its glyphs from webgen.GLYPHS, which is a copy of the
    page's markup; this holds the copy against the original.
    """
    # Parsed out of the source rather than imported: webgen pulls in fontTools,
    # resvg and the font files at import time, and none of those are installed
    # in CI, so importing it here would make this test skip on the one machine
    # that decides whether the assets are allowed to ship.
    repo = Path(__file__).resolve().parents[1]
    source = (repo / "tools" / "screenshots" / "webgen.py").read_text(encoding="utf-8")
    block = source[source.index("GLYPHS: dict[str, tuple[str, ...]] = {") :]
    block = block[: block.index("\n}\n") + 1]

    # every quoted literal in the table, split into its individual SVG shapes so
    # one string holding three <path>s is checked shape by shape
    literals = re.findall(r"'([^']*)'", block)
    shapes = [chunk for literal in literals for chunk in re.findall(r"<[^>]+>", literal)]
    assert len(shapes) >= 20, f"expected the page's glyph shapes, found {len(shapes)}"

    def normalise(markup: str) -> str:
        """The geometry and nothing else.

        The shot resolves two CSS-only shorthands itself, because an SVG string
        has no cascade to inherit from: ``class="solid"`` becomes an explicit
        fill, and ``currentColor`` becomes a literal colour. Strip the
        presentation back off before comparing, so the check is about the
        drawing (which paths, which coordinates) rather than about how each
        side spells its colours.
        """
        markup = re.sub(r'\s*class="solid"', "", markup)
        markup = re.sub(r'\s*fill="(?:currentColor|#[0-9a-f]{3,6}|none)"', "", markup)
        markup = re.sub(r'\s*stroke="(?:currentColor|none)"', "", markup)
        return markup

    page = normalise(_player_template())
    for fragment in shapes:
        if True:
            # ``class="solid"`` is a CSS hook the shot resolves itself; the path
            # geometry around it still has to match the page character for character.
            assert normalise(fragment) in page, f"the shot draws {fragment!r}, which player.html does not"


def test_the_player_shot_uses_the_page_s_button_geometry():
    """46px play disc, 40px circles, #141414 inside, 26px icons, gap 8."""
    page = _player_template()
    assert "border-radius:50%" in page
    assert "background:#141414" in page or "#141414" in page
    # the numbers the shot hardcodes, which is why they are asserted here
    for rule in ("width:40px;height:40px", "width:46px;height:46px", "width:26px;height:26px"):
        assert rule in page.replace(" ", ""), f"player.html no longer says {rule!r}"


def test_the_web_shots_do_not_glow():
    """No gradient or glow on a button: app.css is flat, so the shots are too.

    The download shot drew a grey gradient primary button with a 30px halo, and
    the accent colour it glowed with was removed from the product two rounds
    ago. A screenshot with a glow in it is a screenshot of a different app.
    """
    repo = Path(__file__).resolve().parents[1]
    source = (repo / "tools" / "screenshots" / "webshot.py").read_text(encoding="utf-8")
    block = source[source.index("def button(") : source.index("def qr(")]
    for banned in ("glow_rect(", "linear([("):
        assert banned not in block, f"the button draws {banned!r}, which app.css does not"
    assert '"#ffffff" if variant == "primary" else "#d4d4d4"' in block, (
        "primary/alt fills must be the app.css values #fff / #d4d4d4"
    )


def test_the_server_shot_draws_the_page_s_row_metrics():
    """The listing's icon size, colours and row height come from app.css.

    The shot drew 17px icons in --text-dim, a 13px name and 54px rows, against
    a page that has 20px icons in --text, a 14px name and 46px rows. Every icon
    in the gif was therefore smaller, greyer and further apart than the one a
    person actually sees, which is what "the icons look different" meant.
    """
    repo = Path(__file__).resolve().parents[1]
    css = (repo / "src" / "local_file_sharer" / "web" / "assets" / "static" / "app.css").read_text(
        encoding="utf-8"
    )
    # the rules the shot hardcodes, so a change to either side is caught
    for rule in (
        "grid-template-columns: 16px 20px minmax(0, 1fr) auto 20px",
        "gap: 10px",
        "padding: 11px 18px",
        "border-bottom: 1px solid #1a1a1a",
        ".row-name { display: flex",
        "font-size: 14px",
        ".row-get { text-align: right; color: var(--accent)",
    ):
        assert rule in css, f"app.css no longer says {rule!r}; the shot needs updating"

    source = (repo / "tools" / "screenshots" / "webgen.py").read_text(encoding="utf-8")
    block = source[source.index("row_pad_x, gap = 18, 10") :]
    block = block[: block.index("return shot")]

    assert "row_pad_x, gap = 18, 10" in block, "the .row padding/gap pairing changed"
    assert "show_pick" not in block
    assert "shot.icon(icon_x, ry + 13, kind, icon_w, color=TEXT)" in block, (
        ".row-icon must be 20px in --text: the markup sets width=20 height=20 and "
        "the icon inherits .row's colour, which is --text, not a dim grey"
    )
    assert "row_h = 46" in block, ".row is 11px padding around a 14px line: 46px"
    assert "size=14" in block, ".row-name is 14px"


def test_every_page_sets_a_base_anchor_colour():
    """No page may fall back to the browser's default link colour.

    The player's download control is an ``<a download>`` -- a link, because that
    is how a browser is asked to save a file -- and ``player.html`` set no base
    ``a`` rule. On a real phone it rendered in Chrome's default link blue,
    underlined, inside a circle of otherwise monochrome icons: the only colour
    in the whole application, straight from a missing three-line CSS rule.

    Any page that can render a link needs the rule, so every template is
    checked rather than just the player.
    """
    repo = Path(__file__).resolve().parents[1]
    assets = repo / "src" / "local_file_sharer" / "web" / "assets"

    # The player carries its own <style> block and links no stylesheet, so its
    # rule has to be in the file itself. The other two link app.css, which
    # already sets it -- checked once, there.
    player = (assets / "player.html").read_text(encoding="utf-8")
    # anchored to the start of a line: `.understage a{color:…}` and
    # `.playlist li a{color:…}` are not the base rule and were matching it,
    # which made this test pass with the rule deleted.
    assert re.search(r"(?m)^\s*a\s*\{\s*color:", player), (
        "player.html has no base `a{color:…}` rule of its own, so its <a download> "
        "control renders in the browser's default link blue"
    )
    app_css = (assets / "static" / "app.css").read_text(encoding="utf-8")
    assert re.search(r"(^|\n)a\s*\{\s*color:", app_css), (
        "app.css lost its base anchor colour, which server.html and downloader.html rely on"
    )


def test_the_download_control_is_a_link_on_the_player():
    """Holds the reason the rule above matters: that control is an anchor."""
    repo = Path(__file__).resolve().parents[1]
    page = (repo / "src" / "local_file_sharer" / "web" / "assets" / "player.html").read_text(encoding="utf-8")
    assert re.search(r'<a class="round" id="get"[^>]*download', page), (
        "the player's download control stopped being an <a download>"
    )


def _pillow():
    """Pillow, or skip.

    CI installs only ``.[dev]``, which does not include Pillow, so the checks
    that read committed pixels skip there. The copy guards above deliberately
    do not need Pillow: those are the ones that must run on every platform.
    """
    try:
        from PIL import Image  # noqa: PLC0415 - only the pixel checks need it
    except ModuleNotFoundError:  # pragma: no cover - depends on the environment
        pytest.skip("Pillow is not installed")
    return Image


def _web_png_pixels(name: str):
    """Every pixel of a committed web screenshot, as ``(r, g, b)``."""
    Image = _pillow()

    path = ASSETS / name
    if not path.exists():  # pragma: no cover - committed with the PNGs
        pytest.skip(f"docs/assets/{name} is not present")
    with Image.open(path) as image:
        return image.convert("RGB").getdata()


def test_the_web_screenshots_are_monochrome():
    """Not one coloured pixel, in any of the three pages.

    Everything about this product is black and white, and a real capture is the
    only place a stray colour can be caught: the pages set no link colour of
    their own, so the browser's default blue appears on any anchor that misses
    the base rule, and the player's toast hard-coded a cyan border on a navy
    background for months. Neither shows up in a template diff or in a
    hand-drawn mock -- both show up here, in one line.

    A pixel is allowed a little channel spread for antialiasing of white text
    over near-black, but anything above the threshold is a real colour.
    """
    offenders = []
    for name in ("web-download.png", "web-server.png", "web-player.png"):
        worst = 0
        count = 0
        for red, green, blue in _web_png_pixels(name):
            spread = max(red, green, blue) - min(red, green, blue)
            if spread > 12:
                count += 1
                worst = max(worst, spread)
        if count:
            offenders.append(f"{name}: {count} coloured pixels (worst spread {worst})")
    assert not offenders, "colour leaked into a monochrome page: " + "; ".join(offenders)


def test_the_web_screenshots_are_the_declared_size():
    """Each shot is the viewport the capture script asked for, at 2x.

    The player is taller than the share pages on purpose -- at 900px its
    "Streaming with HTTP Range" line lands one pixel below the fold and the
    screenshot silently loses it. This holds the committed images to the
    script's own table so a crop cannot quietly reappear.
    """
    Image = _pillow()

    source = (Path(__file__).resolve().parents[1] / "tools" / "screenshots" / "webshot_live.py").read_text(
        encoding="utf-8"
    )
    table = source[source.index("VIEWPORT = {") : source.index("\n", source.index("VIEWPORT = {"))]
    scale = int(re.search(r"^SCALE = (\d+)$", source, re.MULTILINE).group(1))
    for name, width, height in re.findall(r'"([\w-]+)": \((\d+), (\d+)\)', table):
        path = ASSETS / f"{name}.png"
        if not path.exists():  # pragma: no cover
            pytest.skip(f"docs/assets/{name}.png is not present")
        with Image.open(path) as image:
            assert image.size == (int(width) * scale, int(height) * scale), (
                f"{name}.png is {image.size}, but webshot_live.py captures "
                f"{width}x{height} at {scale}x -- regenerate the shot"
            )


def test_the_qr_code_in_the_hero_shot_is_not_blank():
    """The QR card must actually hold a scannable code.

    A capture once shipped with a white square where the code belongs: it was
    taken before the page's follow-up ``/api/qr`` request finished. The area is
    white either way in a thumbnail, so count the ink.
    """
    path = ASSETS / "web-download.png"
    if not path.exists():  # pragma: no cover
        pytest.skip("docs/assets/web-download.png is not present")
    Image = _pillow()

    with Image.open(path) as image:
        width, height = image.size
        # the QR frame sits in the hero's right-hand card, near the top
        card = image.convert("L").crop(
            (int(width * 0.70), int(height * 0.12), int(width * 0.95), int(height * 0.45))
        )
        dark = sum(1 for pixel in card.get_flattened_data() if pixel < 100)
    assert dark > 5_000, f"the QR area has only {dark} dark pixels -- it is blank"
