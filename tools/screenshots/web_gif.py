"""Build docs/assets/web-demo.gif: the three web pages, in order, in greys.

``demo.gif`` is the terminal -- the banner shimmering into the menu. This file
is the web UI, and it exists because one thing about the player cannot be shown
in a still: the picture is the page, and the title and controls fade out over it
while it plays. A screenshot of that looks like a film still; the fade is what
makes it read as a video player.

Every frame is a **screenshot of the running page**, taken by ``webshot_live``'s
Chromium driver against a real ``ShareServer``. Nothing here is drawn: the gif
cannot show copy or a layout the product does not have, because each frame is
what the browser rendered.

Only the two player states differ, and the chrome-less one is a real state of
the real page -- ``.stage.is-playing:not(.show-controls)``, which is what the
page does while a video plays and nobody has moved the mouse.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import ASSETS  # noqa: E402

OUT = ASSETS / "web-demo.gif"

#: The captures are 2880px wide at 2x; a README gif has to load on a phone, so
#: each frame is halved before it goes into the palette.
SIZE = (1080, 675)

#: A monochrome page quantises to greys without banding, so no dithering is
#: needed -- and dithering is what makes a gif of a dark UI look 3x bigger.
COLORS = 40

HOLD = 2200
#: Two steps, not five: a crossfade of two dark pages is five near-identical
#: frames, and a gif pays for every one of them. Two reads as a cut with a
#: breath in it.
FADE_STEPS = 3


def _frames() -> tuple[Image.Image, Image.Image, Image.Image, Image.Image]:
    """Shoot the three pages, plus the player with its chrome hidden."""
    import webshot_live  # noqa: PLC0415 - needs websocket-client + a browser

    chrome = _chrome()
    root = _demo_tree()
    browser = webshot_live.Browser(chrome)
    pages = webshot_live.page_urls(root, "my-laptop")
    shots: dict[str, Image.Image] = {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for name, (_server, url) in pages.items():
                path = Path(tmp) / f"{name}.png"
                browser.capture(url, path, size=(1440, 900))
                shots[name] = Image.open(path).convert("RGB").resize(SIZE, Image.LANCZOS)
            playing = Path(tmp) / "playing.png"
            browser.capture(
                pages["web-player"][1],
                playing,
                size=(1440, 900),
                after=(
                    "(() => { const s = document.querySelector('.stage');"
                    " s.classList.add('is-playing');"
                    " s.classList.remove('show-controls'); return true; })()"
                ),
            )
            shots["playing"] = Image.open(playing).convert("RGB").resize(SIZE, Image.LANCZOS)
    finally:
        browser.close()
        for server, _url in pages.values():
            server.stop()
    return shots["web-download"], shots["web-server"], shots["web-player"], shots["playing"]


def _chrome() -> str:
    """The browser binary: ``$LFS_CHROME``, then the usual names on PATH."""
    import os  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    wanted = os.environ.get("LFS_CHROME")
    if wanted:
        return wanted
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("no Chromium found -- set LFS_CHROME=/path/to/chromium")


def _demo_tree() -> Path:
    """The folder the pages show: the same demo tree the stills use."""
    from paths import DEMO_PICKER  # noqa: PLC0415

    if not DEMO_PICKER.is_dir():
        raise SystemExit(f"the demo tree is missing: {DEMO_PICKER}")
    return DEMO_PICKER


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

    download, serve, play, playing = _frames()

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
