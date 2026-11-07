"""README hero: the product name, one line of what it does, and the command.

Hand-authored rather than photographed, because there is nothing to photograph
yet — but every string here is copy the product itself prints. It used to say
"local-file-sharer" in a cyan-to-violet gradient with red/amber/green window
dots and coloured platform chips, which was a banner for a different product:
the app is black and white now and calls itself LOCAL FILE SHARER.

The layout is deliberately sparse. A hero that lists five platforms in five
colours is noise; the tagline is already in ``__init__.py`` and the command is
already ``lfs``, so the picture only has to say those two things plus the name.
"""

from __future__ import annotations

import base64
from pathlib import Path

import resvg_py
from paths import ASSETS, FONT_DIR
from webshot import DIM, FAINT, LINE, LINE_STRONG, TEXT, Canvas

from local_file_sharer import APP_NAME, APP_TAGLINE, __version__

W, H = 1280, 340

#: One weight of accent = contrast in a monochrome design. The wordmark is the
#: brightest thing on the canvas; the logo tile is the darkest raised surface.
PANEL = "#0d0d0d"


def _b64(path: str | Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _fonts() -> str:
    faces = []
    for weight, filename in (
        (800, "Inter-ExtraBold.ttf"),
        (600, "Inter-SemiBold.ttf"),
        (400, "Inter-Regular.ttf"),
    ):
        faces.append(
            f"@font-face{{font-family:'Inter';font-weight:{weight};"
            f"src:url(data:font/ttf;base64,{_b64(FONT_DIR / filename)}) format('truetype');}}"
        )
    faces.append(
        "@font-face{font-family:'JetBrains Mono';font-weight:700;"
        f"src:url(data:font/ttf;base64,{_b64(FONT_DIR / 'JetBrainsMono-Bold.ttf')}) format('truetype');}}"
    )
    faces.append(
        "@font-face{font-family:'JetBrains Mono';font-weight:400;"
        f"src:url(data:font/ttf;base64,{_b64(FONT_DIR / 'JetBrainsMono-Regular.ttf')}) format('truetype');}}"
    )
    return "<style>" + "".join(faces) + "</style>"


def main() -> None:
    canvas = Canvas(W, H)
    canvas.defs.append(_fonts())

    # ── backdrop ────────────────────────────────────────────────────────────
    # A single vertical wash from near-black to black. No radial glows: three
    # coloured ellipses were most of what made the old banner look like a
    # startup landing page.
    canvas.rect(0, 0, W, H, fill=canvas.linear([(0, "#0c0c0c"), (1, "#000000")], x1=0, y1=0, x2=0, y2=1))
    for x in range(0, W, 40):
        canvas.line(x, 0, x, H, stroke="rgba(255,255,255,0.028)")
    for y in range(0, H, 40):
        canvas.line(0, y, W, y, stroke="rgba(255,255,255,0.028)")
    # hairline frame, greys only
    canvas.rect(
        0.8,
        0.8,
        W - 1.6,
        H - 1.6,
        fill="none",
        stroke=canvas.linear([(0, "rgba(255,255,255,0.32)"), (1, "rgba(255,255,255,0.06)")]),
        sw=1.2,
        r=0,
    )

    # ── logo tile ───────────────────────────────────────────────────────────
    canvas.rect(64, 74, 76, 76, fill="#ffffff", stroke="#ffffff", sw=1.2, r=20)
    canvas.text(102, 122, "LFS", family="mono", weight=700, size=25, fill="#000000", anchor="middle")

    # ── wordmark ────────────────────────────────────────────────────────────
    canvas.text(166, 128, APP_NAME, weight=800, size=52, fill=TEXT, spacing=-0.6)

    # ── tagline ─────────────────────────────────────────────────────────────
    canvas.text(168, 172, APP_TAGLINE, weight=400, size=22, fill=DIM)

    # ── the one command ─────────────────────────────────────────────────────
    # The install triad is three commands, and a hero is not the place to teach
    # all three: the README does that. What someone needs at a glance is the
    # command they will type every day afterwards.
    pill_w = 262
    canvas.rect(166, 202, pill_w, 40, fill=PANEL, stroke=LINE_STRONG, sw=1, r=10)
    canvas.text(184, 229, "lfs", family="mono", weight=700, size=16, fill="#ffffff")
    canvas.text(216, 229, "<folder>", family="mono", weight=400, size=16, fill=FAINT)

    # ── right-hand card: what happens ───────────────────────────────────────
    # A triple: the three things the program does in order. Mono, no colour, no
    # window dots -- the dots were a macOS traffic light in red/amber/green on a
    # card that is not a window.
    cx, cy, cw, ch = 856, 88, 360, 164
    canvas.rect(cx, cy, cw, ch, fill="#0a0a0a", stroke=LINE, sw=1, r=16)
    canvas.text(cx + 22, cy + 30, "lfs 1.0.0", family="mono", weight=700, size=11, fill=FAINT)
    canvas.line(cx + 20, cy + 42, cx + cw - 20, cy + 42, stroke=LINE)
    steps = (
        ("1", "Pick Files", "any folder, any size"),
        ("2", "Scan The QR", "open it on any device"),
        ("3", "Download", "resumable, parallel"),
    )
    for index, (number, title, note) in enumerate(steps):
        row_y = cy + 74 + index * 32
        canvas.text(cx + 22, row_y, number, family="mono", weight=700, size=12, fill=FAINT)
        canvas.text(cx + 40, row_y, title, weight=600, size=13.5, fill=TEXT)
        canvas.text(cx + 156, row_y, note, weight=400, size=12.5, fill=FAINT)

    # ── footer line ─────────────────────────────────────────────────────────
    canvas.line(64, H - 44, W - 64, H - 44, stroke=LINE)
    canvas.text(64, H - 22, "Termux · Android · Linux · macOS · Windows", size=12.5, fill=FAINT)
    canvas.text(W - 64, H - 22, f"v{__version__} · MIT", family="mono", size=12, fill=FAINT, anchor="end")

    # PNG only: the SVG would need 4.7 MB of embedded fonts
    png = resvg_py.svg_to_bytes(
        svg_string=canvas.svg(),
        font_files=[
            str(FONT_DIR / "Inter-Regular.ttf"),
            str(FONT_DIR / "Inter-SemiBold.ttf"),
            str(FONT_DIR / "Inter-ExtraBold.ttf"),
            str(FONT_DIR / "JetBrainsMono-Bold.ttf"),
            str(FONT_DIR / "JetBrainsMono-Regular.ttf"),
        ],
        skip_system_fonts=True,
        zoom=1.5,
    )
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "banner.png").write_bytes(png)
    print("wrote", ASSETS / "banner.png", len(png) // 1024, "KB")


if __name__ == "__main__":
    main()
