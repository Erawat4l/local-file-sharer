"""Build docs/assets/web-demo.gif: the three web pages, in order, in greys.

``demo.gif`` is the terminal -- the banner shimmering into the menu. This file
is the web UI, and it exists because one thing about the player cannot be shown
in a still: the picture is the page, and the title and controls fade out over it
while it plays. A screenshot of that looks like a film still; the fade is what
makes it read as a video player.

Every frame is drawn by ``webgen``'s own page builders -- the same functions
that produce the committed PNGs -- so the gif cannot claim copy the templates do
not contain. Only the two player states differ, and ``player(chrome=False)`` is
a real state of the real page (``.is-playing:not(.show-controls)``).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import ASSETS, FONT_DIR  # noqa: E402
from webgen import downloader, player, server  # noqa: E402
from webshot import Shot  # noqa: E402

OUT = ASSETS / "web-demo.gif"

#: 1.15x: three 2160x1350 frames at 2x would be tens of megabytes, and a README
#: gif has to load on a phone.
SCALE = 0.9

#: A monochrome page quantises to greys without banding, so no dithering is
#: needed -- and dithering is what makes a gif of a dark UI look 3x bigger.
COLORS = 40

HOLD = 2200
#: Two steps, not five: a crossfade of two dark pages is five near-identical
#: frames, and a gif pays for every one of them. Two reads as a cut with a
#: breath in it.
FADE_STEPS = 3


def _raster(shot: Shot) -> Image.Image:
    import resvg_py

    fonts = [
        FONT_DIR / name
        for name in (
            "Inter-Regular.ttf",
            "Inter-Medium.ttf",
            "Inter-SemiBold.ttf",
            "Inter-Bold.ttf",
            "Inter-ExtraBold.ttf",
            "JetBrainsMono-Regular.ttf",
            "JetBrainsMono-Bold.ttf",
        )
    ]
    png = resvg_py.svg_to_bytes(
        svg_string=shot.svg(),
        font_files=[str(p) for p in fonts],
        skip_system_fonts=True,
        zoom=SCALE,
    )
    return Image.open(io.BytesIO(png)).convert("RGB")


def main() -> None:
    frames: list[Image.Image] = []
    durations: list[int] = []

    def hold(image: Image.Image, ms: int = HOLD) -> None:
        frames.append(image)
        durations.append(ms)

    def crossfade(before: Image.Image, after: Image.Image) -> None:
        for step in range(1, FADE_STEPS):
            frames.append(Image.blend(before, after, step / FADE_STEPS))
            durations.append(120)

    download = _raster(downloader())
    serve = _raster(server())
    play = _raster(player())
    playing = _raster(player(chrome=False))

    hold(download)
    crossfade(download, serve)
    hold(serve)
    crossfade(serve, play)
    hold(play)

    # The one behaviour a still cannot carry: press play and the chrome goes.
    # Short steps, because this is a state change, not a long animation.
    for step in range(1, 7):
        frames.append(Image.blend(play, playing, step / 6))
        durations.append(90)
    hold(playing, 900)
    # Bring the chrome back -- the still the README links to is the one with
    # controls, since that is what the page looks like when you arrive.
    crossfade(playing, play)
    hold(play)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Quantise the whole sequence to one shared grey ramp before saving:
    # per-frame palettes would spend bits re-deriving the same 64 greys and
    # would still dither, which on flat black doubles the file.
    palette_frames = [f.convert("L").quantize(colors=COLORS, method=Image.Quantize.MEDIANCUT) for f in frames]
    palette_frames[0].save(
        OUT,
        save_all=True,
        append_images=palette_frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print("wrote", OUT, OUT.stat().st_size // 1024, "KB", frames[0].size, len(frames), "frames")


if __name__ == "__main__":
    main()
