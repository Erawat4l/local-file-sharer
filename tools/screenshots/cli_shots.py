"""Generate the README terminal screenshots from the real renderables."""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

from rich.align import Align
from rich.console import Console, RenderableType
from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import ASSETS, DEMO_DIR, INPUTS  # noqa: E402
from termsvg import svg_to_png, to_grid, to_svg  # noqa: E402

from local_file_sharer import (  # noqa: E402
    menu,  # noqa: E402
)
from local_file_sharer.config import load_config  # noqa: E402
from local_file_sharer.state import State  # noqa: E402
from local_file_sharer.transfer.stats import TransferTracker  # noqa: E402
from local_file_sharer.ui import banner, dashboard, picker_ui  # noqa: E402
from local_file_sharer.ui.components import info_panel  # noqa: E402
from local_file_sharer.ui.console import panel, set_console, set_theme  # noqa: E402
from local_file_sharer.ui.theme import build_theme  # noqa: E402

ASSETS = ASSETS
DEMO = DEMO_DIR
WIDTH = 104
HEIGHT = 44


def _console(width: int = WIDTH, height: int = HEIGHT) -> Console:
    os.environ.setdefault("TERM", "xterm-256color")
    con = Console(
        file=io.StringIO(),
        width=width,
        height=height,
        force_terminal=True,
        color_system="truecolor",
        emoji=True,
        highlight=False,
        soft_wrap=False,
        _environ={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
    )
    set_console(con)
    set_theme(build_theme("dark", "cyan"))
    return con


def shot(renderable: RenderableType, name: str, *, title: str, width: int = WIDTH) -> None:
    con = _console(width=width)
    con.print(renderable)
    raw = con.file.getvalue()  # type: ignore[union-attr]
    (INPUTS / "frames").mkdir(parents=True, exist_ok=True)
    (INPUTS / "frames" / f"{name}.ansi").write_text(raw)
    rows = to_grid(renderable, width=width)
    svg = to_svg(rows, title=title)
    ASSETS.mkdir(parents=True, exist_ok=True)
    svg_to_png(svg, ASSETS / f"{name}.png")
    print(f"{name}: {len(rows)} rows")


def demo_state() -> State:
    state = State.load(INPUTS / "shot-state.json")
    state.data["totals"] = {
        "bytes_sent": 128_849_018_880,
        "files_sent": 412,
        "shares": 37,
        "downloads": 96,
        "started": 1_700_000_000,
    }
    state.data["install_id"] = "3f1c0a5b9d6e4f27"
    return state


def main() -> None:
    (INPUTS / "frames").mkdir(parents=True, exist_ok=True)
    config = load_config(respect_env=False)
    config.network.public_ip_lookup = False
    state = demo_state()

    # ── 1. banner (hero) ────────────────────────────────────────────────────
    menu._STATE = state  # noqa: SLF001
    _console()
    hero = banner.banner_renderable(build_theme("dark", "cyan"), offset=0.18)
    shot(Align.center(hero, width=90), "cli-banner", title="start — banner", width=90)

    # ── 2. main menu ────────────────────────────────────────────────────────
    ctx = menu.MenuContext(config=config, state=state, animate=False)
    shot(menu.render_menu(ctx), "cli-menu", title="start")

    # ── 3. picker ───────────────────────────────────────────────────────────
    picker = picker_ui.PickerState(cwd=DEMO, page_size=12)
    picker.reload()
    for entry in picker.visible():
        if entry.name in ("Aurora-Film-Festival-Keynote-4K.mkv", "live-set-2026.flac"):
            picker.selected[str(entry.path)] = entry.path
    picker.cursor = 0
    for index, entry in enumerate(picker.visible()):
        if entry.name == "live-set-2026.flac":
            picker.cursor = index
    picker.message = "3 files selected · 4.2 GB · ready to share"
    shot(picker_ui.render(picker, width=WIDTH - 2, height=40), "cli-picker", title="start — pick files")

    # ── 4. dashboard ────────────────────────────────────────────────────────
    tracker = TransferTracker()
    tracker.start_file("b45d56a6", "Aurora-Film-Festival-Keynote-4K.mkv", 3_842_119_680)
    tracker.start_file("75a8de01", "live-set-2026.flac", 421_003_264)
    tracker.start_file("befb3d7a", "IMG_20261101_143210.jpg", 5_112_004)
    tracker.start_file("3e4524dd", "moodboard-final.png", 18_412_339)
    device = tracker.register_request("192.168.1.77", "curl/8.5.0")
    phone = tracker.register_request("192.168.1.144", "Android/14 OkHttp/4.12")
    tracker.add_bytes(3_842_119_680, device=device, file_id="b45d56a6")
    tracker.finish_file("b45d56a6")
    tracker.add_bytes(260_000_000, device=phone, file_id="75a8de01")
    tracker.add_bytes(5_112_004, device=phone, file_id="befb3d7a")
    tracker.finish_file("befb3d7a")
    tracker.add_bytes(11_400_000, device=device, file_id="3e4524dd")
    tracker.peak_speed = 78_643_200.0
    # realistic 72 MB/s window so the speed badge reads like a real Wi-Fi link
    now = time.time()
    with tracker.lock:
        tracker._samples.clear()  # noqa: SLF001 - shaping the sample window for the shot
        for step in range(24):
            tracker._samples.append((now - 3.0 + step * 0.125, step * 9_437_184))  # noqa: SLF001
        tracker._last_sample = now  # noqa: SLF001
    tracker.started_at = time.time() - 214
    options = dashboard.DashboardOptions(
        title="download · demo",
        goal=52428800,
        links=[
            ("LAN", "http://192.168.1.42:8080/"),
            ("public", "http://103.21.54.19:8080/"),
            ("tailscale", "http://aurora.tail1234.ts.net/"),
        ],
        footer="c copy links · o open browser · p QR · q stop     (nothing was cleared)",
    )
    shot(dashboard.render_dashboard(tracker, options), "cli-dashboard", title="start — live transfer")

    # ── 5. share ready (lan + qr + one-liners) ──────────────────────────────
    from rich.console import Group

    from local_file_sharer.ui.components import mode_badge

    theme = build_theme("dark", "cyan")
    ready = info_panel(
        [
            ("mode", mode_badge("download")),
            ("files", "8 files · 3 folders"),
            ("size", "4.0 GB"),
            ("expires", "30 minutes"),
            ("PIN", "4821"),
        ],
        title="share ready",
    )
    lan = info_panel(
        [
            ("📶 wifi", "http://192.168.1.42:8080/  (192.168.1.42)"),
            ("local", "http://127.0.0.1:8080/  (this device only)"),
            ("🔌 ethernet", "http://10.0.0.7:8080/"),
        ],
        title="direct (fastest — same Wi-Fi)",
        border=theme.accent,
    )
    rows: list = []
    for label, command in (
        ("curl", "curl -L --fail --progress-bar -C - -O http://192.168.1.42:8080/download/all.zip"),
        ("wget", "wget -c --show-progress http://192.168.1.42:8080/download/all.zip"),
        ("aria2c", "aria2c -x16 -s16 -k1M --file-allocation=none http://192.168.1.42:8080/download/all.zip"),
    ):
        rows.append(Text(f" {label}", style=theme.muted))
        rows.append(Text(f"   {command}", style=theme.secondary))
    oneliners = panel(Group(*rows), title="copy-paste one-liners (LAN)", border=theme.border)
    oneliners = Group(
        oneliners,
        Text(
            " press c to copy all links · o opens the browser · p re-prints the QR · q stops",
            style=theme.faint,
        ),
    )
    shot(Group(ready, Text(""), lan, Text(""), oneliners), "cli-ready", title="start — share ready")

    # ── 6. doctor ───────────────────────────────────────────────────────────
    from local_file_sharer import doctor

    report = doctor.run_checks(config=config, skip_network=True)
    shot(doctor.render_report(report), "cli-doctor", title="start doctor", width=132)


if __name__ == "__main__":
    main()
