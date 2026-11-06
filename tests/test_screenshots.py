"""Guards for the screenshot renderer's two silent-failure modes.

Both were found by looking at the committed PNGs rather than at the code, so
they get tests. The renderer is a maintainer tool, not part of the package —
these tests skip cleanly if ``tools/screenshots`` is absent.
"""

from __future__ import annotations

import importlib.util
import re
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


#: Copy the shots legitimately draw that no template literal matches: demo
#: values, sample filenames, and prose the templates assemble from placeholders.
_WEB_SAMPLE_VALUES = {
    "http://192.168.1.42:8080/",
    "http://192.168.1.42:8080/d/3e4524dd/",
    "http://192.168.1.42:8080/s/b45d56a6",
    "no-store",
    "LFS",
    "1.0.0",
    "local-file-sharer",
    "local-file-sharer 1.0.0",
    "1.0.0 · all files",
    "demo",
    "8 files · 4.0 GB",
    "8 items · 4.0 GB",
    "4.0 GB",
    "2.5 GB",
    "2.5 GB / 4.0 GB · 3 files",
    "72 MB/s",
    "ETA 21s · 72 MB/s",
    "16",
    "16×",
    "8",
    "3",
    "1×",
    "10",
    "▶",
    "🔇",
    "↓",
    "·",
    "0:00",
    "0:41:12",
    "1:48:20",
    "buffered 38%",
    "192.168.1.42:8080",
    "0 B/s",
    "Aurora-Film…mkv",
    "3.6 GB · playing",
    "matroska",
    "Aurora-Film-Festival-Keynote-4K.mkv",
    "3.6 GB · video",
    "Termux-notes.txt",
    "4.1 KB · text",
    "live-set-2026.flac",
    "402 MB · audio",
    "moodboard-final.png",
    "17.6 MB · image",
    "report.pdf",
    "2.8 MB · pdf",
    "holiday-photos.zip",
    "612 MB · archive",
    "IMG_20261101_143210.jpg",
    "2026-10-31-wedding-012.jpg",
    "3.6 GB",
    "4.1 KB",
    "402 MB",
    "17.6 MB",
    "2.8 MB",
    "612 MB",
    "4.9 MB",
    "Download ",
    "everything",
    ", fast.",
    "Download  →",
    "video/x-matroska · 3.6 GB",
    "NAME",
    "SIZE",
    "name",
    "size",
    "Download all",
    "Search name or type — try mp4",
    "All",
    "Video",
    "Images",
    "Audio",
    "Documents",
    "Archives",
}

#: Small words the templates emit separately but the shots draw as one label.
_WEB_FRAGMENTS = {"All", "Video", "Images", "Audio", "Documents", "Archives"}
#: (label drawn by a shot, string that must exist in the shipped templates).
#: Curated on purpose: a browser renders these pages, and this toolchain draws
#: them by hand, so the risk is copy that no template contains. These are the
#: lines that were wrong, plus the ones around them. Demo values and anything a
#: ``{{placeholder}}`` or the page's javascript fills in are NOT covered here —
#: no text comparison can check those without a browser.
_WEB_COPY = [
    ("DOWNLOAD STATION", "text-transform: uppercase"),
    ("download station", "download station"),
    ("mode download", 'mode <b>{{mode}}</b>'),
    ("8 devices", "{{device_count}}"),
    ("Download all", "Download all"),
    ("Everything as .zip", "Everything as .zip"),
    ("Copy curl one-liner", "Copy curl one-liner"),
    ("aria2c -x16", "aria2c -x16"),
    ("scan to open on another device", "scan to open on another device"),
    ("Shared files", "Shared files"),
    ("Search name or type — try mp4", "Search name or type — try mp4"),
    ("All", 'data-filter="all"'),
    ("Video", 'data-filter="video"'),
    ("Images", 'data-filter="image"'),
    ("Audio", 'data-filter="audio"'),
    ("Documents", 'data-filter="document"'),
    ("Archives", 'data-filter="archive"'),
    ("resumable · range requests · zero-copy sendfile", "resumable · range requests · zero-copy sendfile"),
    ("powered by pure Python + rich", "powered by pure Python + rich"),
    ("web file server", "web file server"),
    ("Browse ", "Browse "),
    ("Every file is served with HTTP Range support — resume, seek and parallelise freely.",
     "Every file is served with HTTP Range support — resume, seek and parallelise freely."),
    ("Filter in this folder…", "Filter in this folder…"),
    ("Filter", ">Filter<"),
    ("Select all", "Select all"),
    ("Download selected", "Download selected"),
    ("GET /api/manifest", "GET /api/manifest"),
    ("tip: aria2c -x16 -s16 for maximum speed", "for maximum speed"),
    ("Playlist · 1", "Playlist · {{playlist_count}}"),
    ("buffered 38%", "buffered"),
    ("space play · ← → ±10s · f fullscreen · m mute", "play ·"),
    ("Streaming with HTTP Range — seek anywhere instantly.",
     "Streaming with HTTP Range — seek anywhere instantly."),
    ("If the preview stays black, the codec is not supported by this browser (common for MKV/HEVC).",
     "If the preview stays black"),
    ("0 B/s", "0 B/s"),
    ("1.0.0 · all files", 'title="All files in this share"'),
]


def test_web_screenshots_copy_matches_the_templates():
    """Copy the web shots draw must be copy the shipped pages contain.

    These three PNGs are hand-drawn SVG, not renders of the HTML, so they can
    display wording that no template has — and did:

    * a "Filter files…" box where the page has kind chips and
      "Search name or type — try mp4"
    * a "powered by pure Python" footer where the page says
      "powered by pure Python + rich" beside the brand line
    * emoji transport controls (⏪ ▶️ ⏩ 🔊) that the player never renders
    * a "live / HTTP Range seek / mode stream" header on a player whose real
      top line is filename, size, speed, back link and build

    This checks a curated list of those lines in both directions: the label is
    in the generated web-text.txt, and its source is in the templates.
    """
    recorded = REPO / "docs" / "assets" / "web-text.txt"
    if not recorded.exists():  # pragma: no cover - committed with the PNGs
        pytest.skip("docs/assets/web-text.txt is not present")

    web = REPO / "src" / "local_file_sharer" / "web" / "assets"
    templates = "\n".join(
        (web / name).read_text(encoding="utf-8", errors="ignore")
        for name in ("downloader.html", "server.html", "player.html")
    )
    css = (web / "static" / "app.css").read_text(encoding="utf-8", errors="ignore")
    source = templates + "\n" + css
    drawn = recorded.read_text(encoding="utf-8")

    for label, origin in _WEB_COPY:
        assert origin in source, (
            f"the templates no longer contain {origin!r} (for label {label!r}) — "
            "if the page changed, update webgen.py and this list"
        )
        assert label in drawn, (
            f"the screenshots no longer draw {label!r} — regenerate with "
            "tools/screenshots/webgen.py"
        )


def test_web_screenshots_do_not_revive_retired_copy():
    """The exact strings that were wrong, so fixing them cannot silently regress."""
    recorded = REPO / "docs" / "assets" / "web-text.txt"
    if not recorded.exists():  # pragma: no cover
        pytest.skip("docs/assets/web-text.txt is not present")
    drawn = recorded.read_text(encoding="utf-8")

    # Whole-line matches: "powered by pure Python" was the bug, but the fixed
    # line legitimately starts with those words, so a substring test cannot tell
    # them apart. Compare what the shot actually printed, line by line.
    lines = {line.strip() for line in drawn.splitlines()}
    retired = [
        "Filter files…",            # a control the downloader does not have
        "powered by pure Python",   # truncated: the page says "+ rich"
        "HTTP Range seek",          # invented player badge
        "mode stream",              # invented player badge
        "FILE BROWSER",             # the page says "web file server"
        "seeking works instantly",  # invented overlay subtitle
    ]
    glyphs = ["⏪", "⏩", "🔊"]        # the player draws no emoji controls
    present = [text for text in retired if text in lines]
    present += [g for g in glyphs if g in drawn]
    assert not present, f"retired web copy came back: {present}"
