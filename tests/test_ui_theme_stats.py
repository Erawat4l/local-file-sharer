"""Themes, banner, QR rendering, transfer stats and error rendering."""

from __future__ import annotations

import io
import time

import pytest
from rich.console import Console, Group
from rich.text import Text

from local_file_sharer.errors import ConfigError, PickerError, ShareError
from local_file_sharer.fmt import strip_ansi
from local_file_sharer.transfer.stats import TransferTracker
from local_file_sharer.ui import banner
from local_file_sharer.ui.components import error_panel, info_panel, mode_badge, summary_footer
from local_file_sharer.ui.theme import (
    NAMED_ACCENTS,
    THEME_NAMES,
    build_theme,
    cycle_theme,
    gradient_rgb,
    lerp,
    parse_color,
    preview_styles,
    resolve_accent,
)


def render(renderable, width: int = 100) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, width=width, force_terminal=False, no_color=True)
    console.print(renderable)
    return buffer.getvalue()


def test_themes_exist():
    assert set(THEME_NAMES) == {"dark", "light", "mono"}
    for name in THEME_NAMES:
        theme = build_theme(name, "cyan")
        assert theme.name == name
        assert theme.accent
        assert theme.gradient_stops


def test_cycle_theme():
    assert cycle_theme("dark") == "light"
    assert cycle_theme("light") == "mono"
    assert cycle_theme("mono") == "dark"
    assert cycle_theme("bogus") == "light"


def test_accent_resolution():
    rich_name, rgb = resolve_accent("cyan")
    assert rich_name and rgb == NAMED_ACCENTS["cyan"]
    rich_name, rgb = resolve_accent("#ff0000")
    assert rgb == (255, 0, 0)
    assert rich_name.startswith("#")
    assert resolve_accent("nonsense")[1] == NAMED_ACCENTS["cyan"]


def test_parse_color():
    assert parse_color("#22d3ee") == (0x22, 0xD3, 0xEE)
    assert parse_color("22d3ee") == (0x22, 0xD3, 0xEE)
    assert parse_color("#abc") == (0xAA, 0xBB, 0xCC)
    assert parse_color("cyan") is None


def test_gradient_helpers():
    assert lerp((0, 0, 0), (255, 255, 255), 0.5) == (128, 128, 128)
    assert gradient_rgb([(0, 0, 0)], 0.5) == (0, 0, 0)
    assert gradient_rgb([(0, 0, 0), (10, 10, 10)], 1.0) == (10, 10, 10)
    assert gradient_rgb([], 0.3) == (255, 255, 255)


def test_theme_gradient_text_has_every_char():
    theme = build_theme("mono", "white")
    text = theme.gradient("local-file-sharer")
    assert text.plain == "local-file-sharer"


def test_preview_styles():
    pairs = list(preview_styles(build_theme("dark")))
    assert any(role == "accent" for role, _ in pairs)


def test_banner_wordmark_shape():
    rows = banner.wordmark("LFS")
    assert len(rows) == 5
    assert all(rows)
    assert banner.wordmark("") == []


def test_banner_does_not_crash_without_tty():
    out = render(banner.banner_renderable(build_theme("dark")))
    assert "LFS" in out or "█" in out
    assert "LOCAL FILE SHARER" in out


def test_banner_static_and_animate_are_safe(capsys):
    banner.static(build_theme("light"))
    banner.animate(build_theme("dark"), frames=2)
    out = capsys.readouterr().out
    assert out


def test_shimmer_strength_range():
    for step in range(10):
        value = banner.shimmer_strength(step / 10)
        assert 0.5 <= value <= 1.01


def test_mini_header():
    text = banner.mini_header(build_theme("dark"), mode="picker", detail="photos")
    assert "picker" in text.plain


def test_qr_matrix_and_outputs():
    from local_file_sharer import qr

    url = "http://192.168.1.42:8080/"
    rows = qr.matrix(url)
    assert len(rows) == len(rows[0])
    assert all(isinstance(cell, bool) for cell in rows[0])
    half = qr.to_half_blocks(url)
    assert "█" in half or "▀" in half or "▄" in half
    ascii_art = qr.to_ascii(url)
    assert "██" in ascii_art
    svg = qr.svg(url)
    assert svg.startswith("<svg") and "<rect" in svg
    assert qr.svg_data_url(url).startswith("data:image/svg+xml;base64,")


def test_qr_size_helpers():
    from local_file_sharer import qr

    cols, rows = qr.size_hint("http://10.0.0.2:8080/")
    assert cols > 10 and rows > 5
    assert isinstance(qr.fits("http://10.0.0.2:8080/", width=80, height=40), bool)
    assert qr.choose_error_correction("http://10.0.0.2:8080/", width=100) > 0


def test_qr_png_encoder_matches_the_matrix():
    """The PNG writer is dependency-free; it must agree with the SVG matrix."""
    import struct
    import zlib

    from local_file_sharer import qr

    matrix = qr.matrix("http://10.0.0.2:8080/", border=2)
    scale = 3
    data = qr._png(matrix, scale)  # noqa: SLF001 - the encoder is a helper

    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    assert (width, height) == (len(matrix) * scale, len(matrix) * scale)
    assert (depth, colour) == (8, 0)  # 8-bit greyscale

    # Walk the chunks and inflate the pixels back out.
    pos, pixels = 8, b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        assert (
            zlib.crc32(kind + payload) & 0xFFFFFFFF
            == struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0]
        )
        if kind == b"IDAT":
            pixels = zlib.decompress(payload)
        pos += 12 + length

    stride = width + 1  # one filter byte per scanline
    assert len(pixels) == stride * height
    for row_index in range(height):
        line = pixels[row_index * stride : (row_index + 1) * stride]
        assert line[0] == 0, "filter type must be 0 (None)"
        expected = matrix[row_index // scale]
        for column in range(width):
            dark = line[1 + column] == 0
            assert dark == bool(expected[column // scale]), (row_index, column)


def test_tracker_counts_bytes_and_speed():
    tracker = TransferTracker()
    device = tracker.register_request("192.168.1.9", "curl/8.0")
    tracker.start_file("f1", "movie.mp4", 1_000_000)
    tracker.add_bytes(500_000, device=device, file_id="f1")
    snapshot = tracker.snapshot()
    assert snapshot["bytes_sent"] == 500_000
    assert snapshot["requests"] == 1
    assert snapshot["device_count"] == 1
    assert snapshot["transferred"]["total"] == 1_000_000
    assert device.bytes_sent == 500_000
    assert device.label == "curl"


def test_tracker_device_labels():
    cases = {
        "Mozilla/5.0 (Linux; Android 13)": "Android",
        "aria2/1.36.0": "aria2c",
        "Wget/1.21": "wget",
        "Mozilla/5.0 (Windows NT 10.0)": "Windows",
        "curl/8.4.0": "curl",
    }
    for agent, expected in cases.items():
        tracker = TransferTracker()
        device = tracker.register_request("1.2.3.4", agent)
        assert device.label == expected


def test_tracker_eta_and_reset():
    tracker = TransferTracker()
    tracker.start_file("f1", "x", 1000)
    tracker.add_bytes(100)
    tracker.reset()
    assert tracker.snapshot()["bytes_sent"] == 0
    assert tracker.eta() is None


def test_tracker_prunes_idle_devices(monkeypatch):
    tracker = TransferTracker()
    device = tracker.register_request("10.0.0.9", "curl")
    device.last_seen = time.time() - 300
    assert tracker.active_devices() == []


def test_error_panel_renders_hints():
    out = render(error_panel("boom", hints=["do this", "or that"], detail="trace"))
    assert "boom" in out
    assert "do this" in out
    assert "Try this" in out


def test_info_panel_and_badges():
    out = render(info_panel([("port", "8080"), ("ip", "192.168.1.2")], title="share"))
    assert "8080" in out and "192.168.1.2" in out
    assert "Torrent" in render(mode_badge("torrent"))
    assert "3 files" in render(summary_footer(3, 2, 5_000_000, "shareable"))


def test_error_classes_carry_hints():
    exc = ShareError("nope", hints="try again")
    assert exc.all_hints() == ["try again"]
    assert exc.title == "Share failed"
    plain = PickerError("bad")
    assert plain.all_hints()
    assert ConfigError("x").title == "Configuration problem"


def test_strip_ansi_helper():
    assert strip_ansi("\x1b[32mok\x1b[0m") == "ok"


@pytest.mark.parametrize("name", list(THEME_NAMES))
def test_dashboard_renders_in_every_theme(name):
    from local_file_sharer.ui.console import set_theme
    from local_file_sharer.ui.dashboard import DashboardOptions, render_dashboard

    set_theme(build_theme(name, "violet"))
    tracker = TransferTracker()
    device = tracker.register_request("192.168.1.10", "Mozilla/5.0 (Android)")
    tracker.start_file("f", "clip.mp4", 10_000_000)
    tracker.add_bytes(2_000_000, device=device, file_id="f")
    out = render(render_dashboard(tracker, DashboardOptions(links=[("LAN", "http://x:8080/")])))
    assert "clip.mp4" in out
    assert "LIVE" in out
    assert "LAN" in out
    assert "device" in out.lower()


def test_live_console_status_online():
    from local_file_sharer.ui.dashboard import one_line_status

    tracker = TransferTracker()
    tracker.add_bytes(1024)
    assert "sent" in one_line_status(tracker)


def test_theme_preview_renders():
    from local_file_sharer.ui.components import theme_preview

    assert "theme" in render(theme_preview(build_theme("mono", "white")))


def test_qr_matrix_is_a_structurally_valid_symbol():
    """Check the layout rules a scanner relies on, cheaply and without a decoder.

    The matrix carries a 2-module quiet zone, so the finder patterns start at
    (2, 2) rather than (0, 0). Getting that offset wrong makes every pattern
    check fail even though the code is correct — hence the explicit ``q``.
    """
    from local_file_sharer import qr

    url = "http://192.168.1.5:8080/"
    rows = qr.matrix(url)
    n = len(rows)
    q = 2
    size = n - 2 * q
    version = (size - 17) // 4
    assert version >= 1
    assert all(len(row) == n for row in rows), "the matrix must be square"

    finder = ["1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111"]
    for ox, oy, name in ((0, 0, "top-left"), (size - 7, 0, "top-right"), (0, size - 7, "bottom-left")):
        assert all(
            rows[q + oy + y][q + ox + x] == (finder[y][x] == "1") for y in range(7) for x in range(7)
        ), f"{name} finder pattern is malformed"

    # Timing patterns alternate between the finders.
    timing_row = [rows[q + 6][q + x] for x in range(8, size - 8)]
    timing_col = [rows[q + y][q + 6] for y in range(8, size - 8)]
    assert all(timing_row[i] != timing_row[i + 1] for i in range(len(timing_row) - 1))
    assert all(timing_col[i] != timing_col[i + 1] for i in range(len(timing_col) - 1))

    # The single always-dark module.
    assert rows[q + 4 * version + 9][q + 8] is True


def test_qr_svg_is_well_formed_and_escapes_its_input():
    """The SVG goes into an <img src=data:…>, so it must be valid XML."""
    import xml.etree.ElementTree as ET

    from local_file_sharer import qr

    hostile = 'http://x/"><script>alert(1)</script>'
    svg = qr.svg(hostile)
    root = ET.fromstring(svg)  # raises if the markup is broken
    assert root.tag.endswith("svg")
    assert "<script>" not in svg, "the payload must be encoded as modules, never emitted as markup"


# ── narrow terminals (a phone is not a desktop) ──────────────────────────────
@pytest.mark.parametrize("columns", [20, 28, 38, 41, 46, 80])
def test_panels_fit_a_narrow_terminal(monkeypatch, columns):
    """A Termux session is ~40 columns; shrink-wrapped panels wrapped mid-word."""
    from local_file_sharer.ui import console as console_mod

    monkeypatch.setattr(console_mod, "screen_width", lambda default=100: columns)
    body = [("mode", "⬇️ Download"), ("files", "1 file · 1 folder")]
    buffer = io.StringIO()
    Console(file=buffer, width=columns, force_terminal=False, no_color=True).print(
        console_mod.panel(Group(*[Text(f"{k:>8}  {v}") for k, v in body]), title="share ready")
    )
    for line in buffer.getvalue().splitlines():
        assert len(line) <= columns, f"{len(line)} cols in a {columns}-col terminal: {line!r}"


@pytest.mark.parametrize("width", [24, 34, 41, 60])
def test_commands_wrap_on_argument_boundaries(width):
    """Never split a flag: a line starting `- -O` is not the command it appears to be."""
    from local_file_sharer.ui.console import wrap_command

    command = "curl -L --fail --progress-bar -C - -O http://192.168.1.42:8080/download/all.zip"
    lines = wrap_command(command, width=width)
    assert len(lines) > 1, "this command cannot fit, so it must wrap"
    for line in lines:
        # A single argument longer than the width (the URL) cannot be split on a
        # space; everything else must fit. Rich soft-wraps the URL itself.
        word_max = max(len(word) for word in command.split(" ")) + 3
        assert len(line) <= max(width, word_max), f"{line!r} is {len(line)} cols, limit {width}"
    # Continuation lines are marked, and joining them back restores the command.
    assert all(line.rstrip().endswith("\\") for line in lines[:-1])
    rebuilt = " ".join(line.strip().removesuffix("\\").strip() for line in lines)
    assert rebuilt == command


def test_wrap_command_keeps_a_flag_with_its_value():
    """``-C -`` must not split, or the pasted command loses its argument."""
    from local_file_sharer.ui.console import wrap_command

    lines = wrap_command("curl -L --fail -C - -O http://192.168.1.42:8080/", width=34)
    joined = " ".join(line.strip().rstrip("\\") for line in lines)
    assert "-C -" in joined
    assert not any(line.strip() == "-" for line in lines)
    assert all(len(line) <= 34 for line in lines)


def test_wrap_command_leaves_a_short_command_alone():
    from local_file_sharer.ui.console import wrap_command

    assert wrap_command("lfs doctor", width=80) == ["   lfs doctor"]


# ── picker layout on a phone-width terminal ─────────────────────────────────
def _render_lines(renderable, width: int) -> list[str]:
    import io

    from rich.console import Console

    buffer = io.StringIO()
    console = Console(file=buffer, width=width, force_terminal=False, legacy_windows=False)
    console.print(renderable)
    return buffer.getvalue().splitlines()


@pytest.mark.parametrize("width", [40, 48, 60, 80])
def test_footer_hints_never_truncate_a_label(width):
    """`Space pi…` tells the user nothing; whole pairs or none."""
    from local_file_sharer.ui.components import footer_keys

    hints = [("j/k", "move"), ("Space", "pick"), ("Enter", "open"), ("s", "share"), ("q", "quit")]
    lines = _render_lines(footer_keys(hints, width=width), width)
    text = "\n".join(lines)
    for _key, label in hints:
        assert label in text, f"{label!r} was truncated or dropped at {width} cols"
    assert "…" not in text


@pytest.mark.parametrize("width", [40, 48, 60, 80])
def test_picker_rows_and_hints_fit_the_width(width, tmp_path):
    """Nothing the picker prints may be wider than the terminal."""
    from local_file_sharer.ui.picker_ui import PickerState, render

    for index in range(6):
        (tmp_path / f"file-{index}.txt").write_text("x", encoding="utf-8")
    state = PickerState(cwd=tmp_path)
    state.reload(icons=True)
    lines = _render_lines(render(state, width=width, height=30), width)
    over = [line for line in lines if len(line) > width]
    assert not over, f"lines wider than {width}: {[line[:60] for line in over]}"


def test_speed_survives_a_zero_byte_flush():
    """A flush with nothing new to add must not freeze the reported speed.

    The sender flushes every 150 ms and a flush can legitimately carry 0 bytes.
    The old sampler only appended a sample when ``delta`` was truthy, so the
    newest running total stayed at an old value while time kept passing — the
    speed shown on the page decayed towards zero mid-transfer even though data
    was still flowing.
    """
    tracker = TransferTracker()
    for _ in range(10):
        tracker.add_bytes(1024 * 1024)
        tracker.add_bytes(0)  # the timer fired with nothing new
    assert tracker.bytes_sent == 10 * 1024 * 1024
    speed = tracker.speed()
    assert speed > 0, "speed collapsed to zero while bytes were sent"


def test_speed_is_not_absurd_for_two_samples_in_the_same_instant():
    """Two samples microseconds apart must not produce terabytes per second.

    Dividing a large delta by a near-zero elapsed time gives nonsense; the last
    honest measurement is a better answer than 3 TB/s.
    """
    tracker = TransferTracker()
    tracker.add_bytes(50 * 1024 * 1024)
    tracker.add_bytes(50 * 1024 * 1024)
    from local_file_sharer.transfer.stats import MAX_PLAUSIBLE_SPEED

    # clamped to the ceiling rather than reaching terabytes per second
    assert tracker.speed() <= MAX_PLAUSIBLE_SPEED, "speed must stay believable"


def test_reset_clears_the_speed_history():
    tracker = TransferTracker()
    tracker.add_bytes(5 * 1024 * 1024)
    tracker.add_bytes(5 * 1024 * 1024)
    tracker.speed()
    tracker.reset()
    assert tracker.speed() == 0.0
    assert tracker.bytes_sent == 0


def test_dashboard_fits_a_short_termux_window(monkeypatch):
    """The panel must never be taller than the window.

    A panel that does not fit makes rich scroll instead of repaint, so a phone
    with a short window showed a stack of half-cut dashboards that nobody can
    scroll back through. Every size from a tiny window up must fit.
    """
    import io

    from rich.console import Console

    from local_file_sharer.ui.dashboard import DashboardOptions, render_dashboard

    tracker = TransferTracker()
    tracker.start_file("ALL OG COLLECTION.zip", "ALL OG COLLECTION.zip", 3_600_000_000)
    tracker.add_bytes(991_000_000, file_id="ALL OG COLLECTION.zip")
    tracker.register_request("10.0.0.5", "Android", path="/f/x")

    options = DashboardOptions(
        title="serve · 3.6 GB",
        footer="press Ctrl+C to stop",
        links=[("LAN", "http://10.0.0.5:8080/")],
        show_devices=True,
    )
    for height in (10, 12, 15, 18, 24, 40):
        buf = io.StringIO()
        con = Console(width=66, height=height, file=buf, legacy_windows=False, force_terminal=False)
        monkeypatch.setattr("local_file_sharer.ui.dashboard.screen_height", lambda default=20, h=height: h)
        con.print(render_dashboard(tracker, options))
        rows = buf.getvalue().count("\n")
        assert rows <= height, f"panel is {rows} rows in a {height}-row window"


def test_progress_ignores_finished_files():
    """Old downloads must not drag the percentage down.

    The browser tracks the file it is fetching; the dashboard used to sum every
    file ever requested, so it reported 42% while the browser said 98%.
    """
    tracker = TransferTracker()
    tracker.start_file("old.mkv", "old.mkv", 1_000_000_000)
    tracker.add_bytes(1_000_000_000, file_id="old.mkv")
    tracker.finish_file("old.mkv")
    tracker.start_file("new.zip", "new.zip", 100 * 1024 * 1024)
    tracker.add_bytes(98 * 1024 * 1024, file_id="new.zip")
    sent, total = tracker.progress()
    assert total == 100 * 1024 * 1024
    assert round(sent / total * 100) == 98


def test_parallel_ranges_of_one_file_do_not_reset_progress():
    """Sixteen range requests are sixteen requests for ONE file.

    Each used to re-register the file with sent=0, so the dashboard's
    percentage restarted constantly and never matched the browser.
    """
    tracker = TransferTracker()
    size, parts = 3_600_000_000, 16
    chunk = size // parts
    key = "ALL OG COLLECTION.zip"
    for i in range(parts):
        tracker.start_file(key, key, size)  # every range starts the file
        if i < 4:
            tracker.add_bytes(chunk, file_id=key)  # ...and four complete
            tracker.finish_file(key)
    sent, total = tracker.progress()
    assert total == size, "total must stay one file, not sixteen"
    assert round(sent / total * 100) == 25


def test_a_quiet_device_still_counts_as_connected():
    """A device sending 16 streams goes silent between ranges; it is not gone."""
    tracker = TransferTracker()
    device = tracker.register_request("10.0.0.5", "Android", path="/f/x")
    assert tracker.snapshot()["device_count"] == 1
    device.last_seen -= 30  # half a minute of silence, mid-download
    assert tracker.snapshot()["device_count"] == 1
    device.last_seen -= 120  # really gone
    assert tracker.snapshot()["device_count"] == 0
