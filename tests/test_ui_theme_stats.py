"""Themes, banner, QR rendering, transfer stats and error rendering."""

from __future__ import annotations

import io
import time

import pytest
from rich.console import Console

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
    assert set(THEME_NAMES) == {"dark", "light", "neon"}
    for name in THEME_NAMES:
        theme = build_theme(name, "cyan")
        assert theme.name == name
        assert theme.accent
        assert theme.gradient_stops


def test_cycle_theme():
    assert cycle_theme("dark") == "light"
    assert cycle_theme("light") == "neon"
    assert cycle_theme("neon") == "dark"
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
    theme = build_theme("neon", "cyan")
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
    assert "local-file-sharer" in out


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


def test_qr_png_bytes():
    from local_file_sharer import qr

    data = qr.png_bytes("hello", box_size=2)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


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

    assert "theme" in render(theme_preview(build_theme("neon", "lime")))
