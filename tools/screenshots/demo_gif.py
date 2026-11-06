"""Build docs/assets/demo.gif from the *real* banner animation frames."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from PIL import Image
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import ASSETS, FONT_DIR, INPUTS  # noqa: E402
from termsvg import Cell, to_grid, to_svg  # noqa: E402

from local_file_sharer import menu  # noqa: E402
from local_file_sharer.config import load_config  # noqa: E402
from local_file_sharer.state import State  # noqa: E402
from local_file_sharer.ui import banner  # noqa: E402
from local_file_sharer.ui.console import set_console, set_theme  # noqa: E402
from local_file_sharer.ui.theme import build_theme  # noqa: E402

WIDTH = 96
OUT = ASSETS / "demo.gif"


def console_for(width: int) -> Console:
    con = Console(
        file=io.StringIO(),
        width=width,
        force_terminal=True,
        color_system="truecolor",
        emoji=True,
        highlight=False,
        soft_wrap=False,
    )
    set_console(con)
    return con


def main() -> None:
    os.environ.setdefault("TERM", "xterm-256color")
    theme = build_theme("dark", "cyan")
    set_theme(theme)
    state = State.load(INPUTS / "shot-state.json")
    state.data["totals"] = {"bytes_sent": 128_849_018_880, "files_sent": 412, "shares": 37, "downloads": 96}
    menu._STATE = state  # noqa: SLF001
    config = load_config(respect_env=False)
    ctx = menu.MenuContext(config=config, state=state, animate=False)

    frames: list[Image.Image] = []

    ROWS = 30
    # menu is 28 rows + 1 trailing blank; the footer wraps at 96 cols, so grab 30

    def grab(renderable, width: int = WIDTH) -> Image.Image:  # noqa: ANN001
        console_for(width)
        rows = to_grid(renderable, width=width)
        # pad every frame to the same grid so the GIF never changes size
        rows = rows[:ROWS] + [
            [Cell(" ", "#e6edf3", None, False, False)] * width for _ in range(ROWS - len(rows))
        ]
        svg = to_svg(rows, title="", chrome=True, cols=WIDTH, scale=1.0)
        buffer = io.BytesIO()
        import resvg_py

        png = resvg_py.svg_to_bytes(
            svg_string=svg,
            font_files=[
                str(FONT_DIR / "JetBrainsMono-Regular.ttf"),
                str(FONT_DIR / "JetBrainsMono-Bold.ttf"),
            ],
            skip_system_fonts=True,
            monospace_family="JetBrains Mono",
            zoom=1.25,
        )
        buffer.write(png)
        buffer.seek(0)
        return Image.open(buffer).convert("RGB")

    # 1. the banner shimmer, 14 real frames
    for index in range(14):
        offset = index / 14
        frame = banner._frame(theme, offset, WIDTH)  # noqa: SLF001 - the animation itself
        frames.append(grab(frame))

    # 2. hold the banner, then fade in the menu
    menu_frame = grab(menu.render_menu(ctx))
    frames.append(frames[-1])
    frames.append(menu_frame)
    frames.append(menu_frame)
    frames.append(menu_frame)

    # Record the menu as text beside the gif. A gif is pixels: nothing in a
    # diff or a test can read it, so this one silently advertised
    # "start <path>" and "switch dark / light / neon" for months after the
    # program said "lfs <path>" and "light / mono". The test renders the menu
    # with these same console settings and compares, so the animation cannot
    # drift again. (Rich swaps emoji for text when not a terminal, which would
    # otherwise make the recording depend on how it was captured.)
    plain = Console(file=io.StringIO(), width=WIDTH, force_terminal=False,
                    emoji=True, highlight=False, soft_wrap=False, color_system=None)
    plain.print(menu.render_menu(ctx))
    (ASSETS / "demo-menu.txt").write_text(plain.file.getvalue(), encoding="utf-8")

    durations = [70] * 14 + [400, 60, 900, 2200]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print("wrote", OUT, OUT.stat().st_size // 1024, "KB", frames[0].size)


if __name__ == "__main__":
    main()
