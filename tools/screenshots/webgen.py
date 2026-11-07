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
    Measure,
    Shot,
)

W, H = 1440, 900
M = 40  # page gutter


def backdrop(shot: Shot) -> None:
    """Faint 48px grid, masked towards the top like css body::before."""
    for x in range(0, W, 48):
        shot.line(x, 0, x, H, stroke="rgba(161,161,161,0.055)")
    for y in range(0, H, 48):
        shot.line(0, y, W, y, stroke="rgba(161,161,161,0.055)")


def topbar(
    shot: Shot,
    *,
    title: str,
    subtitle: str,
    chips: list[tuple[str, bool]],
    y: float = 96,
    back_link: bool = False,
) -> float:
    h = 76
    shot.card(M, y, W - M * 2, h, r=18)
    # logo tile
    g = shot.linear([(0, "rgba(175,175,175,0.22)"), (1, "rgba(153,153,153,0.22)")])
    shot.rect(M + 18, y + 19, 38, 38, fill=g, stroke="rgba(175,175,175,0.4)", r=12)
    shot.text(M + 37, y + 43, "LFS", family="mono", weight=700, size=13, fill="#eeeeee", anchor="middle")
    shot.text(M + 70, y + 34, "local-file-sharer", weight=700, size=15.5, fill=TEXT)
    shot.text(M + 70, y + 53, subtitle, weight=500, size=11, fill=FAINT, spacing=0.8)
    x = M + 350
    for label, accent in chips:
        x += shot.chip(x, y + 25, label, accent=accent) + 10
    if back_link:
        # player.html:320 — a chevron back link to "/?list=1", then the build
        # badge. Right-align the badge first, then place the chevron to its left
        # so the two never overlap.
        build = "1.0.0 · all files"
        badge_x = W - M - 18
        shot.text(badge_x, y + 43, build, weight=500, size=11, fill=FAINT, anchor="end")
        chevron_x = badge_x - Measure("sans", 500, 11).width(build) - 16
        shot.parts.append(
            f'<path d="M{chevron_x} {y + 30}l-5 6 5 6" stroke="{DIM}" stroke-width="1.8" '
            f'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    else:
        shot.text(W - M - 18, y + 33, "1.0.0", weight=600, size=12, fill=FAINT, anchor="end")
        shot.text(W - M - 18, y + 51, title, weight=500, size=11, fill=FAINT, anchor="end")
    return y + h


# ── 1. download station ─────────────────────────────────────────────────────
def downloader() -> Shot:
    shot = Shot(W, H, url="http://192.168.1.42:8080/")
    backdrop(shot)
    y = topbar(
        shot,
        title="Download Station",
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
            ("Everything", {"weight": 800, "fill": grad}),
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
        ("Speed", "72 MB/s", ""),
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
    shot.text(M, grid_y + 8, "Shared Files", weight=700, size=19, fill=TEXT)
    shot.text(M + 128, grid_y + 8, "8 items · 4.0 GB", size=12.5, fill=FAINT)
    # The real section head has a search box whose placeholder is literally
    # "Search name or type — try mp4", followed by a row of kind chips
    # (All · Video · Images · Audio · Documents · Archives) with "All" on. The
    # shot used to draw a single "Filter files…" box and no chips at all, which
    # is markup that has never existed.
    shot.pill_input(W - M - 300, grid_y - 14, 300, "Search name or type — try mp4", h=34, mono=False)
    chip_x = M
    chip_y = grid_y + 30
    for label, on in (
        ("All", True),
        ("Video", False),
        ("Images", False),
        ("Audio", False),
        ("Documents", False),
        ("Archives", False),
    ):
        chip_x += shot.chip(chip_x, chip_y, label, accent=on) + 8

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
        cy = chip_y + 34 + row * (row_h + 12)
        shot.card(cx, cy, col_w, row_h, r=16, fill="rgba(25,25,25,0.72)")
        shot.rect(
            cx + 16, cy + 16, 44, 44, fill="rgba(175,175,175,0.10)", stroke="rgba(175,175,175,0.22)", r=13
        )
        shot.emoji(cx + 26, cy + 26, icon, 24)
        shot.text(cx + 74, cy + 34, name, weight=600, size=14, fill=TEXT)
        shot.text(cx + 74, cy + 54, meta, size=11.5, fill=FAINT)
        shot.text(cx + col_w - 20, cy + 44, "Download  →", weight=600, size=12.5, fill=ACCENT, anchor="end")

    fy = H - 24
    shot.line(M, fy - 16, W - M, fy - 16, stroke=LINE)
    shot.text(M, fy, "local-file-sharer 1.0.0", size=11.5, fill=FAINT)
    shot.text(M + 148, fy, "·", size=11.5, fill=FAINT)
    shot.text(M + 164, fy, "resumable · range requests · zero-copy sendfile", size=11.5, fill=FAINT)
    shot.text(W - M, fy, "powered by pure Python + rich", size=11.5, fill=FAINT, anchor="end")
    return shot


# ── 2. web file browser (serve mode) ────────────────────────────────────────
def server() -> Shot:
    shot = Shot(W, H, url="http://192.168.1.42:8080/d/3e4524dd/")
    backdrop(shot)
    y = topbar(
        shot,
        title="web file server",
        subtitle="web file server",
        chips=[("demo", False), ("8 files · 4.0 GB", False), ("Download Station", False)],
    )
    head_y = y + 26
    shot.card(M, head_y, W - M * 2, 92, r=20)
    shot.rich(
        M + 28,
        head_y + 46,
        [
            ("Browse ", {"weight": 800, "fill": TEXT}),
            (
                "Folder",
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
    # Real markup: <form> with a search input ("Filter in this folder…") and a
    # ghost "Filter" submit button, then actions "Select All" and
    # "Download Selected". The shot drew a lone "Filter…" pill and skipped the
    # Select all button entirely.
    shot.button(W - M - 28 - 168, head_y + 26, "Download Selected", w=168, h=40, variant="primary")
    shot.button(W - M - 28 - 168 - 12 - 92, head_y + 30, "Select All", w=92, h=32, size=12, variant="ghost")
    form_w = 300
    form_x = W - M - 28 - 168 - 12 - 92 - 12 - form_w
    shot.pill_input(form_x, head_y + 30, form_w - 74, "Filter in this folder…", h=32, mono=False)
    shot.button(form_x + form_w - 68, head_y + 30, "Filter", w=68, h=32, size=12, variant="ghost")

    # stat strip
    strip_y = head_y + 110
    cards = [
        ("Folder", "demo", ACCENT),
        ("Size", "4.0 GB", TEXT),
        ("Share Total", "4.0 GB", TEXT),
        ("Transferred", "2.5 GB", ACCENT3),
        ("Speed", "72 MB/s", ACCENT),
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
            shot.rect(M, ry, W - M * 2, row_h - 4, fill="rgba(161,161,161,0.035)", r=12)
        shot.rect(M + 14, ry + 9, 30, 30, fill="rgba(175,175,175,0.08)", r=9)
        shot.emoji(M + 20, ry + 14, icon, 17)
        shot.text(M + 58, ry + 26, name, weight=500, size=13.5, fill=TEXT)
        shot.text(W - M - 90, ry + 26, size, family="mono", size=12, fill=DIM, anchor="end")
        shot.text(W - M - 22, ry + 26, "↓", weight=700, size=14, fill=ACCENT, anchor="end")

    # Footer per server.html: brand + version + the manifest path on the left,
    # the aria2c tip pushed right. "powered by pure Python" is not in the page.
    fy = H - 24
    shot.line(M, fy - 16, W - M, fy - 16, stroke=LINE)
    shot.text(M, fy, "local-file-sharer 1.0.0", size=11.5, fill=FAINT)
    shot.text(M + 148, fy, "·", size=11.5, fill=FAINT)
    shot.text(M + 164, fy, "GET /api/manifest", family="mono", size=11.5, fill=DIM)
    shot.text(W - M, fy, "tip: aria2c -x16 -s16 for maximum speed", size=11.5, fill=FAINT, anchor="end")
    return shot


# ── 3. media player (stream mode) ───────────────────────────────────────────
def player() -> Shot:
    shot = Shot(W, H, url="http://192.168.1.42:8080/s/b45d56a6")
    backdrop(shot)
    # The player is a full-screen stage, not a site page: its own top line is
    # LFS · filename · size badge · speed badge · back link · build badge
    # (player.html:314-326). The shot used to draw the shared site header with
    # "live / HTTP Range seek / mode stream" chips, none of which the page has.
    y = topbar(
        shot,
        title="media player",
        subtitle="player",
        chips=[("3.6 GB", False), ("0 B/s", False)],
        back_link=True,
    )

    vx, vy = M, y + 26
    vw, vh = W - M * 2 - 360, 452
    # video stage
    shot.card(vx, vy, vw, vh, r=20, fill="#060606")
    stage = shot.radial([(0, "rgba(153,153,153,0.30)"), (1, "rgba(6,6,6,0)")], cx=0.5, cy=0.5, r=0.62)
    shot.parts.append(f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" rx="20" fill="{stage}"/>')
    shot.circle(vx + vw / 2, vy + vh / 2 - 12, 44, fill="rgba(175,175,175,0.92)")
    shot.parts.append(
        f'<path d="M{vx + vw / 2 - 12} {vy + vh / 2 - 34} L{vx + vw / 2 + 22} {vy + vh / 2 - 12} '
        f'L{vx + vw / 2 - 12} {vy + vh / 2 + 10} Z" fill="#1c1c1c"/>'
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
        # player.html:335 — the overlay shows mime · size and nothing else.
        "video/x-matroska · 3.6 GB",
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
    # Controls, in the order and shape the shipped page uses: back10, play,
    # fwd10, mute, volume slider, rate, pip. The shot used to draw emoji
    # transport glyphs (⏪ ▶️ ⏩ 🔊) and a fullscreen button — the real player has
    # no fullscreen button in this row and labels none of them with emoji.
    cy = vy + vh - 34
    cx = vx + 28
    for label, primary in (("10", False), ("▶", True), ("10", False), ("🔇", False)):
        r = 17 if not primary else 21
        shot.circle(cx + r, cy, r, fill="rgba(175,175,175,0.16)" if primary else "rgba(161,161,161,0.12)")
        if label == "🔇":
            shot.emoji(cx + r - 9, cy - 9, label, 18)
        elif label == "▶":
            shot.text(cx + r + 1, cy + 5, label, weight=700, size=15, fill="#eeeeee", anchor="middle")
        else:
            # back/fwd 10s: the number sits inside a round button
            shot.text(cx + r, cy + 4, label, weight=700, size=11, fill=DIM, anchor="middle")
        cx += r * 2 + 12
    # volume slider
    shot.line(cx, cy, cx + 66, cy, stroke=LINE_STRONG, sw=4)
    shot.line(cx, cy, cx + 40, cy, stroke=ACCENT, sw=4)
    cx += 82
    # playback speed
    shot.rect(cx, cy - 13, 46, 26, fill="rgba(161,161,161,0.10)", stroke=LINE_STRONG, r=13)
    shot.text(cx + 23, cy + 4, "1×", weight=600, size=12, fill=DIM, anchor="middle")
    cx += 58
    # pip — player.html:398 has it; the shot used to stop at the rate chip
    shot.circle(cx + 17, cy, 17, fill="rgba(161,161,161,0.12)")
    shot.rect(cx + 10, cy - 8, 14, 11, fill="none", stroke=DIM, r=2)
    shot.rect(cx + 14, cy - 4, 10, 8, fill="none", stroke=ACCENT, r=2)
    shot.text(vx + vw - 28, cy + 4, "buffered 38%", family="mono", size=11, fill=FAINT, anchor="end")

    # playlist / stats sidebar
    sx = vx + vw + 24
    sw = W - M - sx
    shot.card(sx, vy, sw, vh, r=20)
    shot.text(sx + 22, vy + 34, "Playlist · 1", weight=700, size=15, fill=TEXT)
    shot.line(sx + 18, vy + 48, sx + sw - 18, vy + 48, stroke=LINE)
    shot.rect(
        sx + 18, vy + 62, sw - 36, 62, fill="rgba(175,175,175,0.10)", stroke="rgba(175,175,175,0.3)", r=12
    )
    shot.emoji(sx + 32, vy + 78, "🎵", 20)
    shot.text(sx + 60, vy + 86, "Aurora-Film…mkv", weight=600, size=12.5, fill=TEXT)
    shot.text(sx + 60, vy + 104, "3.6 GB · playing", size=11, fill=FAINT)

    stats = [("Transferred", "2.5 GB"), ("Devices", "3"), ("Format", "matroska"), ("Speed", "72 MB/s")]
    sy = vy + 156
    for label, value in stats:
        shot.text(sx + 22, sy, label, size=10.5, fill=FAINT, spacing=1.0)
        shot.text(sx + sw - 22, sy, value, family="mono", weight=700, size=12.5, fill=ACCENT, anchor="end")
        sy += 30
    shot.line(sx + 18, sy, sx + sw - 18, sy, stroke=LINE)
    # player.html:426 renders this as one line of <kbd> hints, not two.
    shot.text(sx + 22, sy + 26, "space play · ← → ±10s · f fullscreen · m mute", size=11, fill=FAINT)

    # notice
    ny = vy + vh + 22
    shot.rect(M, ny, W - M * 2, 62, fill="rgba(193,193,193,0.08)", stroke="rgba(193,193,193,0.32)", r=16)
    shot.emoji(M + 22, ny + 20, "⚠️", 20)
    shot.text(
        M + 52,
        ny + 28,
        "If the preview stays black, the codec is not supported by this browser (common for MKV/HEVC).",
        weight=600,
        size=13,
        fill="#e4e4e4",
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
        "Streaming with HTTP Range — seek anywhere instantly.",
        size=11.5,
        fill=FAINT,
        anchor="middle",
    )
    return shot


def recorded_text() -> list[str]:
    """Every label the three screenshots draw, in draw order.

    These pages are hand-drawn SVG, not renders of the shipped HTML, so they
    can claim copy the templates never contained — and did: the download shot
    drew a "Filter files…" box against a page that has kind chips and a
    "Search name or type — try mp4" placeholder, and the server shot ended with
    "powered by pure Python", a line that exists in no template. Write the copy
    next to the PNGs so a test can hold it against the real markup.
    """
    out: list[str] = []
    for _name, builder in (("web-download", downloader), ("web-server", server), ("web-player", player)):
        shot = builder()
        out.extend(shot.labels)
    return out


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    labels: list[str] = []
    for name, builder in (("web-download", downloader), ("web-server", server), ("web-player", player)):
        shot = builder()
        shot.save(ASSETS / f"{name}.png", scale=1.5)
        labels.extend(shot.labels)
        print(name, "ok")
    (ASSETS / "web-text.txt").write_text("\n".join(labels) + "\n", encoding="utf-8")
    print(f"web-text.txt: {len(labels)} labels")


if __name__ == "__main__":
    main()
