"""Draw the download station, file browser and media player screenshots."""

from __future__ import annotations

from paths import ASSETS
from webshot import (
    ACCENT,
    ACCENT2,
    ACCENT3,
    DIM,
    FAINT,
    LINE,
    LINE_STRONG,
    TEXT,
    WARN,
    Shot,
)

W, H = 1440, 900
M = 40  # page gutter


def backdrop(shot: Shot) -> None:
    """Faint 48px grid, masked towards the top like css body::before."""
    for x in range(0, W, 48):
        shot.line(x, 0, x, H, stroke="rgba(148,163,184,0.055)")
    for y in range(0, H, 48):
        shot.line(0, y, W, y, stroke="rgba(148,163,184,0.055)")


def topbar(shot: Shot, *, title: str, subtitle: str, chips: list[tuple[str, bool]], y: float = 96) -> float:
    h = 76
    shot.card(M, y, W - M * 2, h, r=18)
    # logo tile
    g = shot.linear([(0, "rgba(34,211,238,0.22)"), (1, "rgba(167,139,250,0.22)")])
    shot.rect(M + 18, y + 19, 38, 38, fill=g, stroke="rgba(34,211,238,0.4)", r=12)
    shot.text(M + 37, y + 43, "LFS", family="mono", weight=700, size=13, fill="#c8f7ff", anchor="middle")
    shot.text(M + 70, y + 34, "local-file-sharer", weight=700, size=15.5, fill=TEXT)
    shot.text(M + 70, y + 53, subtitle, weight=500, size=11, fill=FAINT, spacing=0.8)
    x = M + 350
    for label, accent in chips:
        x += shot.chip(x, y + 25, label, accent=accent) + 10
    shot.text(W - M - 18, y + 33, "1.0.0", weight=600, size=12, fill=FAINT, anchor="end")
    shot.text(W - M - 18, y + 51, title, weight=500, size=11, fill=FAINT, anchor="end")
    return y + h


# ── 1. download station ─────────────────────────────────────────────────────
def downloader() -> Shot:
    shot = Shot(W, H, url="http://192.168.1.42:8080/")
    backdrop(shot)
    y = topbar(
        shot,
        title="download station",
        subtitle="DOWNLOAD STATION",
        chips=[("live", True), ("mode download", False), ("expires in 29m", False), ("8 devices", False)],
    )

    hero_y = y + 26
    hero_h = 322
    shot.card(M, hero_y, W - M * 2 - 330, hero_h, r=20)
    grad = shot.linear([(0, ACCENT), (1, ACCENT2)], x1=0, y1=0, x2=1, y2=0)
    shot.rich(
        M + 34,
        hero_y + 62,
        [
            ("Download ", {"weight": 800, "fill": TEXT}),
            ("everything", {"weight": 800, "fill": grad}),
            (", fast.", {"weight": 800, "fill": TEXT}),
        ],
        size=42,
    )

    lead1 = "8 files · 4.0 GB shared from aurora-laptop. Grab one file, several at once with"
    lead2 = "16 parallel connections, or pull the whole share as a single resumable .zip."
    shot.text(M + 34, hero_y + 96, lead1, size=14, fill=DIM)
    shot.text(M + 34, hero_y + 118, lead2, size=14, fill=DIM)

    # stat row
    stats = [
        ("files", "8", ""),
        ("total size", "4.0 GB", ""),
        ("connections", "16", "parallel"),
        ("speed", "72 MB/s", ""),
    ]
    sx = M + 34
    for label, value, note in stats:
        shot.text(sx, hero_y + 158, label, size=10.5, fill=FAINT, spacing=1.1)
        shot.text(sx, hero_y + 186, value, weight=700, size=24, fill=TEXT if label != "speed" else ACCENT)
        if note:
            shot.text(sx + 34, hero_y + 186, note, weight=500, size=11, fill=FAINT)
        sx += 156

    # actions
    bx = M + 34
    by = hero_y + 212
    bx += shot.button(bx, by, "Download all", icon="⬇️", badge="16×") + 12
    bx += shot.button(bx, by, "Everything as .zip", icon="🗜️", variant="alt") + 12
    bx += shot.button(bx, by, "Copy curl one-liner", icon="📋", variant="ghost") + 12
    bx += shot.button(bx, by, "aria2c -x16", icon="⚡", variant="ghost")

    # progress
    shot.progress(M + 34, hero_y + 274, W - M * 2 - 330 - 68, 0.62)
    shot.text(M + 34, hero_y + 302, "2.5 GB / 4.0 GB · 3 files", family="mono", size=11, fill=DIM)
    shot.text(
        W - M - 330 - 34, hero_y + 302, "ETA 21s · 72 MB/s", family="mono", size=11, fill=FAINT, anchor="end"
    )

    # QR card
    qx = W - M - 300
    shot.card(qx, hero_y, 300, hero_h, r=20)
    shot.qr(qx + 62, hero_y + 26, 176, seed="http://192.168.1.42:8080/")
    shot.pill_input(qx + 18, hero_y + 218, 186, "192.168.1.42:8080")
    shot.button(qx + 212, hero_y + 218, "Copy", w=70, h=34, size=12.5, variant="ghost")
    shot.text(
        qx + 150, hero_y + 278, "scan to open on another device", size=11.5, fill=FAINT, anchor="middle"
    )

    # file grid
    grid_y = hero_y + hero_h + 26
    shot.text(M, grid_y + 8, "Shared files", weight=700, size=19, fill=TEXT)
    shot.text(M + 128, grid_y + 8, "8 items · 4.0 GB", size=12.5, fill=FAINT)
    shot.pill_input(W - M - 300, grid_y - 14, 300, "Filter files…", h=34, mono=False)

    files = [
        ("🎬", "Aurora-Film-Festival-Keynote-4K.mkv", "3.6 GB · video"),
        ("📄", "Termux-notes.txt", "4.1 KB · text"),
        ("🎵", "live-set-2026.flac", "402 MB · audio"),
        ("🖼️", "moodboard-final.png", "17.6 MB · image"),
        ("📕", "report.pdf", "2.8 MB · pdf"),
        ("📦", "holiday-photos.zip", "612 MB · archive"),
    ]
    col_w = (W - M * 2 - 24) // 2
    row_h = 76
    for index, (icon, name, meta) in enumerate(files):
        col, row = index % 2, index // 2
        cx = M + col * (col_w + 24)
        cy = grid_y + 26 + row * (row_h + 12)
        shot.card(cx, cy, col_w, row_h, r=16, fill="rgba(17,26,43,0.72)")
        shot.rect(
            cx + 16, cy + 16, 44, 44, fill="rgba(34,211,238,0.10)", stroke="rgba(34,211,238,0.22)", r=13
        )
        shot.emoji(cx + 26, cy + 26, icon, 24)
        shot.text(cx + 74, cy + 34, name, weight=600, size=14, fill=TEXT)
        shot.text(cx + 74, cy + 54, meta, size=11.5, fill=FAINT)
        shot.text(cx + col_w - 20, cy + 44, "Download  →", weight=600, size=12.5, fill=ACCENT, anchor="end")

    shot.text(
        W / 2,
        H - 22,
        "local-file-sharer 1.0.0  ·  resumable · range requests · zero-copy sendfile",
        size=11.5,
        fill=FAINT,
        anchor="middle",
    )
    return shot


# ── 2. web file browser (serve mode) ────────────────────────────────────────
def server() -> Shot:
    shot = Shot(W, H, url="http://192.168.1.42:8080/d/3e4524dd/")
    backdrop(shot)
    y = topbar(
        shot,
        title="web file server",
        subtitle="FILE BROWSER",
        chips=[("demo", False), ("8 files · 4.0 GB", False), ("download station", False)],
    )
    head_y = y + 26
    shot.card(M, head_y, W - M * 2, 92, r=20)
    shot.rich(
        M + 28,
        head_y + 46,
        [
            ("Browse ", {"weight": 800, "fill": TEXT}),
            (
                "folder",
                {"weight": 800, "fill": shot.linear([(0, ACCENT), (1, ACCENT2)], x1=0, y1=0, x2=1, y2=0)},
            ),
        ],
        size=26,
    )
    shot.text(
        M + 28,
        head_y + 70,
        "Every file is served with HTTP Range support — resume, seek and parallelise freely.",
        size=12.5,
        fill=DIM,
    )
    shot.button(W - M - 28 - 168, head_y + 26, "Download selected", w=168, h=40, variant="primary")
    shot.pill_input(W - M - 28 - 168 - 220 - 12, head_y + 30, 220, "Filter…", h=32, mono=False)

    # stat strip
    strip_y = head_y + 110
    cards = [
        ("folder", "demo", ACCENT),
        ("size", "4.0 GB", TEXT),
        ("share total", "4.0 GB", TEXT),
        ("transferred", "2.5 GB", ACCENT3),
        ("speed", "72 MB/s", ACCENT),
    ]
    cw = (W - M * 2 - 12 * 4) // 5
    for index, (label, value, color) in enumerate(cards):
        cx = M + index * (cw + 12)
        shot.card(cx, strip_y, cw, 78, r=16)
        shot.text(cx + 18, strip_y + 28, label, size=10.5, fill=FAINT, spacing=1.0)
        shot.text(cx + 18, strip_y + 56, value, weight=700, size=19, fill=color)

    # table header
    t = strip_y + 100
    shot.text(M + 22, t, "NAME", size=10.5, fill=FAINT, spacing=1.4)
    shot.text(W - M - 90, t, "SIZE", size=10.5, fill=FAINT, spacing=1.4, anchor="end")
    shot.line(M, t + 14, W - M, t + 14, stroke=LINE)

    rows = [
        ("🎬", "Aurora-Film-Festival-Keynote-4K.mkv", "3.6 GB"),
        ("📄", "Termux-notes.txt", "4.1 KB"),
        ("🎵", "live-set-2026.flac", "402 MB"),
        ("🖼️", "moodboard-final.png", "17.6 MB"),
        ("📕", "report.pdf", "2.8 MB"),
        ("🖼️", "IMG_20261101_143210.jpg", "4.9 MB"),
        ("🖼️", "2026-10-31-wedding-012.jpg", "4.9 MB"),
        ("📦", "holiday-photos.zip", "612 MB"),
    ]
    row_h = 52
    for index, (icon, name, size) in enumerate(rows):
        ry = t + 24 + index * row_h
        if index % 2 == 0:
            shot.rect(M, ry, W - M * 2, row_h - 4, fill="rgba(148,163,184,0.035)", r=12)
        shot.rect(M + 14, ry + 9, 30, 30, fill="rgba(34,211,238,0.08)", r=9)
        shot.emoji(M + 20, ry + 14, icon, 17)
        shot.text(M + 58, ry + 26, name, weight=500, size=13.5, fill=TEXT)
        shot.text(W - M - 90, ry + 26, size, family="mono", size=12, fill=DIM, anchor="end")
        shot.text(W - M - 22, ry + 26, "↓", weight=700, size=14, fill=ACCENT, anchor="end")

    shot.text(
        W / 2,
        H - 22,
        "HTTP Range · resumable · 0 gaps · powered by pure Python",
        size=11.5,
        fill=FAINT,
        anchor="middle",
    )
    return shot


# ── 3. media player (stream mode) ───────────────────────────────────────────
def player() -> Shot:
    shot = Shot(W, H, url="http://192.168.1.42:8080/s/b45d56a6")
    backdrop(shot)
    y = topbar(
        shot,
        title="media player",
        subtitle="STREAM",
        chips=[("live", True), ("HTTP Range seek", False), ("mode stream", False)],
    )

    vx, vy = M, y + 26
    vw, vh = W - M * 2 - 360, 452
    # video stage
    shot.card(vx, vy, vw, vh, r=20, fill="#03060c")
    stage = shot.radial([(0, "rgba(167,139,250,0.30)"), (1, "rgba(3,6,12,0)")], cx=0.5, cy=0.5, r=0.62)
    shot.parts.append(f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" rx="20" fill="{stage}"/>')
    shot.circle(vx + vw / 2, vy + vh / 2 - 12, 44, fill="rgba(34,211,238,0.92)")
    shot.parts.append(
        f'<path d="M{vx + vw / 2 - 12} {vy + vh / 2 - 34} L{vx + vw / 2 + 22} {vy + vh / 2 - 12} '
        f'L{vx + vw / 2 - 12} {vy + vh / 2 + 10} Z" fill="#04212b"/>'
    )
    shot.text(
        vx + vw / 2,
        vy + vh / 2 + 76,
        "Aurora-Film-Festival-Keynote-4K.mkv",
        weight=600,
        size=15,
        fill=TEXT,
        anchor="middle",
    )
    shot.text(
        vx + vw / 2,
        vy + vh / 2 + 98,
        "video/x-matroska · 3.6 GB · seeking works instantly",
        size=12,
        fill=FAINT,
        anchor="middle",
    )

    # timeline
    bar_y = vy + vh - 74
    shot.progress(vx + 28, bar_y, vw - 56, 0.38, h=6)
    for position in (0.0, 0.38, 1.0):
        shot.text(
            vx + 28 + (vw - 56) * position,
            bar_y + 26,
            "0:41:12" if position == 0.38 else ("0:00" if position == 0 else "1:48:20"),
            family="mono",
            size=11,
            fill=DIM if position == 0.38 else FAINT,
            anchor="start" if position == 0 else ("end" if position == 1 else "middle"),
        )
    # controls
    cy = vy + vh - 30
    controls = [("⏪", True), ("▶️", True), ("⏩", True), ("🔊", True), ("1×", False), ("⛶", True)]
    cx = vx + 28
    for glyph, is_emoji in controls:
        if is_emoji:
            shot.emoji(cx, cy - 14, glyph, 22)
            cx += 34
        else:
            shot.rect(cx, cy - 24, 44, 26, fill="rgba(148,163,184,0.10)", stroke=LINE_STRONG, r=13)
            shot.text(cx + 22, cy - 5, glyph, weight=600, size=12, fill=DIM, anchor="middle")
            cx += 56
    shot.text(vx + vw - 28, cy - 3, "buffered 38%", family="mono", size=11, fill=FAINT, anchor="end")

    # playlist / stats sidebar
    sx = vx + vw + 24
    sw = W - M - sx
    shot.card(sx, vy, sw, vh, r=20)
    shot.text(sx + 22, vy + 34, "Playlist · 1", weight=700, size=15, fill=TEXT)
    shot.line(sx + 18, vy + 48, sx + sw - 18, vy + 48, stroke=LINE)
    shot.rect(
        sx + 18, vy + 62, sw - 36, 62, fill="rgba(34,211,238,0.10)", stroke="rgba(34,211,238,0.3)", r=12
    )
    shot.emoji(sx + 32, vy + 78, "🎵", 20)
    shot.text(sx + 60, vy + 86, "Aurora-Film…mkv", weight=600, size=12.5, fill=TEXT)
    shot.text(sx + 60, vy + 104, "3.6 GB · playing", size=11, fill=FAINT)

    stats = [("transferred", "2.5 GB"), ("devices", "3"), ("format", "matroska"), ("speed", "72 MB/s")]
    sy = vy + 156
    for label, value in stats:
        shot.text(sx + 22, sy, label, size=10.5, fill=FAINT, spacing=1.0)
        shot.text(sx + sw - 22, sy, value, family="mono", weight=700, size=12.5, fill=ACCENT, anchor="end")
        sy += 30
    shot.line(sx + 18, sy, sx + sw - 18, sy, stroke=LINE)
    shot.text(sx + 22, sy + 26, "space play · ← → ±10s", size=11, fill=FAINT)
    shot.text(sx + 22, sy + 46, "f fullscreen · m mute", size=11, fill=FAINT)

    # notice
    ny = vy + vh + 22
    shot.rect(M, ny, W - M * 2, 62, fill="rgba(251,191,36,0.08)", stroke="rgba(251,191,36,0.32)", r=16)
    shot.emoji(M + 22, ny + 20, "⚠️", 20)
    shot.text(
        M + 52,
        ny + 28,
        "If the preview stays black, the codec is not supported by this browser (common for MKV/HEVC).",
        weight=600,
        size=13,
        fill="#fde68a",
    )
    shot.text(
        M + 52,
        ny + 46,
        "Download the file and play it locally — the transfer is lossless either way.",
        size=12.5,
        fill=WARN,
        opacity=0.85,
    )

    shot.text(
        W / 2,
        H - 22,
        "streaming with HTTP Range — seek anywhere instantly",
        size=11.5,
        fill=FAINT,
        anchor="middle",
    )
    return shot


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, builder in (("web-download", downloader), ("web-server", server), ("web-player", player)):
        shot = builder()
        shot.save(ASSETS / f"{name}.png", scale=1.5)
        print(name, "ok")


if __name__ == "__main__":
    main()
