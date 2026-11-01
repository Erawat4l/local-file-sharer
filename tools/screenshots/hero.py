"""Hand-authored README hero: gradient wordmark + tagline + platform chips."""

from __future__ import annotations

from pathlib import Path

from paths import ASSETS, FONT_DIR
from webshot import (
    ACCENT,
    ACCENT2,
    ACCENT3,
    DIM,
    FAINT,
    TEXT,
    Canvas,
)

W, H = 1280, 340


def main() -> None:
    canvas = Canvas(W, H)
    canvas.defs.append(
        "<style>@font-face{font-family:'Inter';font-weight:800;"
        "src:url(data:font/ttf;base64,"
        + _b64(str(FONT_DIR / "Inter-ExtraBold.ttf"))
        + ") format('truetype');}"
        "@font-face{font-family:'Inter';font-weight:600;"
        "src:url(data:font/ttf;base64,"
        + _b64(str(FONT_DIR / "Inter-SemiBold.ttf"))
        + ") format('truetype');}"
        "@font-face{font-family:'Inter';font-weight:400;"
        "src:url(data:font/ttf;base64," + _b64(str(FONT_DIR / "Inter-Regular.ttf")) + ") format('truetype');}"
        "@font-face{font-family:'JetBrains Mono';font-weight:700;"
        "src:url(data:font/ttf;base64,"
        + _b64(str(FONT_DIR / "JetBrainsMono-Bold.ttf"))
        + ") format('truetype');}"
        "</style>"
    )

    # backdrop
    canvas.rect(0, 0, W, H, fill=canvas.linear([(0, "#0b1120"), (1, "#05080f")], x1=0, y1=0, x2=0, y2=1))
    for cx, cy, rx, ry, color, op in (
        (150, -60, 620, 340, ACCENT, 0.20),
        (1080, -30, 520, 300, ACCENT2, 0.18),
        (640, H + 60, 700, 260, ACCENT3, 0.10),
    ):
        g = canvas.radial([(0, color), (1, "rgba(5,8,15,0)")])
        canvas.parts.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{g}" opacity="{op}"/>')
    # grid + edge frame
    for x in range(0, W, 40):
        canvas.line(x, 0, x, H, stroke="rgba(148,163,184,0.045)")
    for y in range(0, H, 40):
        canvas.line(0, y, W, y, stroke="rgba(148,163,184,0.045)")
    frame = canvas.linear(
        [(0, "rgba(34,211,238,0.55)"), (0.55, "rgba(167,139,250,0.35)"), (1, "rgba(34,211,238,0.12)")]
    )
    canvas.rect(0.8, 0.8, W - 1.6, H - 1.6, fill="none", stroke=frame, sw=1.6, r=0)

    # logo tile
    tile = canvas.linear([(0, "rgba(34,211,238,0.26)"), (1, "rgba(167,139,250,0.26)")])
    canvas.glow_rect(64, 60, 74, 74, r=20, fill=tile, glow=ACCENT, spread=48, opacity=0.5)
    canvas.rect(64, 60, 74, 74, fill="none", stroke="rgba(34,211,238,0.5)", sw=1.4, r=20)
    canvas.text(101, 106, "LFS", family="mono", weight=700, size=24, fill="#c8f7ff", anchor="middle")

    # wordmark
    grad = canvas.linear([(0, ACCENT), (0.5, "#7dd3fc"), (1, ACCENT2)], x1=0, y1=0, x2=1, y2=0)
    canvas.text(164, 122, "local-file-sharer", weight=800, size=58, fill=grad)

    # tagline
    canvas.rich(
        167,
        172,
        [
            ("Share files between your devices ", {"weight": 600, "fill": "#c7d2fe"}),
            ("at maximum speed.", {"weight": 600, "fill": TEXT}),
        ],
        size=25,
    )
    canvas.text(
        167,
        208,
        "One command, an interactive menu, a QR code, and a real HTTP engine that saturates your Wi-Fi.",
        size=15.5,
        fill=DIM,
    )

    # chips
    x = 167
    for label, color, border in (
        ("TERMUX", "#fbbf24", "rgba(251,191,36,0.45)"),
        ("ANDROID", "#34d399", "rgba(52,211,153,0.45)"),
        ("LINUX", "#e2e8f0", "rgba(226,232,240,0.35)"),
        ("macOS", "#a78bfa", "rgba(167,139,250,0.45)"),
        ("WINDOWS", "#60a5fa", "rgba(96,165,250,0.45)"),
    ):
        from webshot import Measure

        width = Measure("sans", 600, 12).width(label) + 26
        canvas.rect(x, 236, width, 28, fill="rgba(15,23,42,0.75)", stroke=border, r=14)
        canvas.text(x + width / 2, 255, label, weight=600, size=12, fill=color, anchor="middle")
        x += width + 9
    canvas.text(x + 8, 255, "pure Python · rich + qrcode · MIT", size=13, fill=FAINT)

    # right-hand command card
    cx, cy, cw, ch = 872, 96, 344, 148
    gl = canvas.linear([(0, "rgba(34,211,238,0.10)"), (1, "rgba(167,139,250,0.10)")])
    canvas.rect(cx, cy, cw, ch, fill="rgba(9,14,24,0.86)", stroke="rgba(148,163,184,0.20)", r=18)
    canvas.rect(cx, cy, cw, ch, fill=gl, r=18)
    canvas.circle(cx + 20, cy + 22, 5, fill="#ff5f57")
    canvas.circle(cx + 36, cy + 22, 5, fill="#febc2e")
    canvas.circle(cx + 52, cy + 22, 5, fill="#28c840")
    canvas.line(cx + 1, cy + 40, cx + cw - 1, cy + 40, stroke="rgba(148,163,184,0.16)")
    canvas.text(cx + 22, cy + 76, "$ start", family="mono", weight=700, size=18, fill=ACCENT3)
    canvas.text(cx + 22, cy + 104, "→ pick files, scan, download", family="mono", size=14, fill=DIM)
    canvas.text(cx + 22, cy + 128, "→ 72 MB/s on the same Wi-Fi", family="mono", size=14, fill=DIM)

    # PNG only: the SVG would need 4.7 MB of embedded fonts
    import resvg_py

    png = resvg_py.svg_to_bytes(
        svg_string=canvas.svg(),
        font_files=[
            str(FONT_DIR / "Inter-Regular.ttf"),
            str(FONT_DIR / "Inter-SemiBold.ttf"),
            str(FONT_DIR / "Inter-ExtraBold.ttf"),
            str(FONT_DIR / "JetBrainsMono-Bold.ttf"),
        ],
        skip_system_fonts=True,
        zoom=1.5,
    )
    (ASSETS / "banner.png").write_bytes(png)


def _b64(path: str) -> str:
    import base64

    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


if __name__ == "__main__":
    main()
