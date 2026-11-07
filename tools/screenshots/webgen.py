"""Draw the download station, file browser and media player screenshots."""

from __future__ import annotations

from paths import ASSETS
from webshot import (
    ACCENT,
    DIM,
    FAINT,
    LINE,
    LINE_STRONG,
    TEXT,
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
    # .logo is a solid white tile with black glyphs, not a grey gradient
    shot.rect(M + 18, y + 19, 38, 38, fill="#ffffff", stroke="#ffffff", r=10)
    shot.text(M + 37, y + 44, "LFS", family="mono", weight=700, size=13, fill="#000000", anchor="middle")
    shot.text(M + 70, y + 36, "LOCAL FILE SHARER", weight=700, size=14, fill=TEXT, spacing=0.2)
    shot.text(M + 70, y + 54, subtitle.upper(), weight=500, size=10, fill=FAINT, spacing=0.9)
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
        shot.text(W - M - 18, y + 36, "1.0.0", family="mono", weight=500, size=11.5, fill=FAINT, anchor="end")
        shot.text(W - M - 18, y + 54, title, weight=500, size=10, fill=FAINT, spacing=0.9, anchor="end")
    return y + h


# ── 1. download station ─────────────────────────────────────────────────────# ── 1. download station ─────────────────────────────────────────────────────
def downloader() -> Shot:
    shot = Shot(W, H, url="http://192.168.1.42:8080/")
    backdrop(shot)
    # server.html is the page this draws. Chips are exactly the ones the
    # template emits: brand, "Live", "Mode download", "<n> Devices", version.
    # "expires in 29m" is gone from the shot because it is now conditional --
    # an open-ended share has no countdown, and drawing one was copy the page
    # cannot produce for a share that never expires.
    y = topbar(
        shot,
        title="Download Station",
        subtitle="Download Station",
        chips=[("Live", True), ("Mode download", False), ("3 Devices", False)],
    )

    hero_y = y + 26
    hero_h = 306
    shot.card(M, hero_y, W - M * 2 - 320, hero_h, r=20)
    shot.rich(
        M + 34,
        hero_y + 58,
        [
            ("Download ", {"weight": 800, "fill": TEXT}),
            ("Everything", {"weight": 800, "fill": ACCENT}),
            (", Fast.", {"weight": 800, "fill": TEXT}),
        ],
        size=38,
    )

    # The lead is one line in the template now: the stat row underneath already
    # says files, size and connections, so the sentence that repeated them was
    # removed from the page and has to leave the picture too.
    shot.text(M + 34, hero_y + 88, "8 files · 4.0 GB shared from aurora-laptop.", size=14, fill=DIM)

    stats = [
        ("Files", "8", ""),
        ("Total Size", "4.0 GB", ""),
        ("Connections", "16", "parallel"),
        ("Speed", "72 MB/s", ""),
    ]
    sx = M + 34
    for label, value, note in stats:
        shot.text(sx, hero_y + 128, label, size=10.5, fill=FAINT, spacing=1.1)
        shot.text(sx, hero_y + 156, value, weight=700, size=22, fill=TEXT)
        if note:
            shot.text(sx + 30, hero_y + 156, note, weight=500, size=11, fill=FAINT)
        sx += 152

    # actions: .btn ghost for the two outlines, .btn for the solid primary
    bx = M + 34
    by = hero_y + 182
    shot.button(bx, by, "Download All", w=150, h=38, size=12.5, icon="download")
    bx += 162
    shot.button(bx, by, "Everything As .zip", w=168, h=38, size=12.5, variant="ghost")
    bx += 180
    shot.button(bx, by, "Copy curl One-liner", w=168, h=38, size=12.5, variant="ghost")
    bx += 180
    shot.button(bx, by, "aria2c -x16", w=118, h=38, size=12.5, variant="ghost")

    shot.progress(M + 34, hero_y + 240, W - M * 2 - 320 - 68, 0.62, h=6)
    shot.text(M + 34, hero_y + 268, "2.5 GB / 4.0 GB · 3 files", family="mono", size=11, fill=DIM)
    shot.text(
        W - M - 354, hero_y + 268, "ETA 21s · 72 MB/s", family="mono", size=11, fill=FAINT, anchor="end"
    )

    # QR card. "scan to open on another device" is the template's own caption.
    qx = W - M - 296
    shot.card(qx, hero_y, 296, hero_h, r=20)
    shot.qr(qx + 60, hero_y + 24, 176, seed="http://192.168.1.42:8080/")
    shot.pill_input(qx + 18, hero_y + 212, 182, "192.168.1.42:8080")
    shot.button(qx + 208, hero_y + 212, "Copy", w=70, h=34, size=12.5, variant="ghost")
    shot.text(
        qx + 148, hero_y + 272, "scan to open on another device", size=11.5, fill=FAINT, anchor="middle"
    )

    # file grid
    grid_y = hero_y + hero_h + 24
    shot.text(M, grid_y + 8, "Shared Files", weight=700, size=19, fill=TEXT)
    shot.text(M + 122, grid_y + 8, "8 items · 4.0 GB", size=12.5, fill=FAINT)
    # the search box sits on the section-head baseline, next to the count
    shot.pill_input(W - M - 300, grid_y - 4, 300, "Search name or type — try mp4", h=32, mono=False)
    chip_x = M
    chip_y = grid_y + 32
    for label, on in (
        ("All", True),
        ("Video", False),
        ("Images", False),
        ("Audio", False),
        ("Documents", False),
        ("Archives", False),
    ):
        chip_x += shot.chip(chip_x, chip_y, label, accent=on) + 8

    # Cards as ``_entry_cards()`` builds them: card-icon (monochrome SVG) ·
    # name + "size · kind" · one outlined button. No emoji, no "Download →"
    # link: the page draws .btn.ghost buttons, and the shot drew a coloured
    # Twemoji icon next to a text arrow that exists nowhere in the markup.
    files = [
        ("folder", "roms", "1,701 files · 4.0 GB"),
        ("video", "Aurora-Film-Festival-Keynote-4K.mkv", "3.6 GB · video"),
        ("text", "Termux-notes.txt", "4.1 KB · text"),
        ("audio", "live-set-2026.flac", "402 MB · audio"),
        ("image", "moodboard-final.png", "17.6 MB · image"),
        ("pdf", "report.pdf", "2.8 MB · pdf"),
        ("archive", "holiday-photos.zip", "612 MB · archive"),
        ("image", "IMG_20261101_143210.jpg", "4.9 MB · image"),
    ]
    col_w = (W - M * 2 - 20) // 2
    row_h = 66
    for index, (kind, name, meta) in enumerate(files[:6]):
        col, row = index % 2, index // 2
        cx = M + col * (col_w + 20)
        cy = chip_y + 36 + row * (row_h + 10)
        shot.card(cx, cy, col_w, row_h, r=14, fill="rgba(20,20,20,0.86)")
        shot.rect(cx + 14, cy + 13, 40, 40, fill="#161616", stroke=LINE, r=10)
        shot.icon(cx + 24, cy + 23, kind, 20, color=DIM)
        shot.text(cx + 66, cy + 30, name, weight=600, size=13.5, fill=TEXT)
        shot.text(cx + 66, cy + 48, meta, family="mono", size=11, fill=FAINT)
        # folder cards say "Download zip", files say "Get" (or "Stream")
        label = "Download zip" if kind == "folder" else ("Stream" if kind in ("video", "audio") else "Get")
        bw = 96 if kind != "folder" else 108
        shot.button(cx + col_w - bw - 14, cy + 17, label, w=bw, h=32, size=11.5, variant="ghost")

    # footer: brand + version left, the two-word protocol note right
    fy = H - 22
    shot.line(M, fy - 16, W - M, fy - 16, stroke=LINE)
    shot.text(M, fy, "LOCAL FILE SHARER 1.0.0", size=11.5, fill=FAINT)
    shot.text(W - M, fy, "Resume · Range", size=11.5, fill=FAINT, anchor="end")
    return shot


# ── 2. web file browser (serve mode) ────────────────────────────────────────
def server() -> Shot:
    shot = Shot(W, H, url="http://192.168.1.42:8080/d/3e4524dd/")
    backdrop(shot)
    # Exact chip set from server.html: breadcrumb "All Files", "Serving",
    # "<n> files · <size>", "Download Folder", "Download Station".
    y = topbar(
        shot,
        title="Web File Server",
        subtitle="Web File Server",
        chips=[("All Files", False), ("Serving", True), ("1,708 files · 4 GB", False)],
    )
    head_y = y + 24
    shot.card(M, head_y, W - M * 2, 84, r=18)
    shot.rich(
        M + 26,
        head_y + 44,
        [
            ("Browse ", {"weight": 800, "fill": TEXT}),
            ("Folder", {"weight": 800, "fill": ACCENT}),
        ],
        size=25,
    )
    # The explanatory sentence under the heading was removed from the page: it
    # said what the section heading already said, one line further down.
    shot.button(
        W - M - 26 - 160, head_y + 24, "Download Selected (0)", w=160, h=36, size=12, variant="primary"
    )
    shot.button(W - M - 26 - 160 - 10 - 84, head_y + 26, "Select All", w=84, h=32, size=11.5, variant="ghost")
    form_w = 272
    form_x = W - M - 26 - 160 - 10 - 84 - 10 - form_w
    shot.pill_input(form_x, head_y + 26, form_w - 58, "Filter in this folder…", h=32, mono=False)
    shot.button(form_x + form_w - 51, head_y + 26, "Filter", w=51, h=32, size=11.5, variant="ghost")

    strip_y = head_y + 98
    cards = [
        ("Files", "1,708"),
        ("Folder Size", "4 GB"),
        ("Share Total", "4 GB"),
        ("Transferred", "28.5 MB"),
        ("Speed", "—"),
    ]
    cw = (W - M * 2 - 10 * 4) // 5
    for index, (label, value) in enumerate(cards):
        cx = M + index * (cw + 10)
        shot.card(cx, strip_y, cw, 68, r=14)
        shot.text(cx + 16, strip_y + 24, label, size=10, fill=FAINT, spacing=1.0)
        shot.text(cx + 16, strip_y + 50, value, family="mono", size=18, fill=TEXT)

    # list head + rows, folder first. The shot previously drew eight loose files
    # from a flat listing; the page groups by folder now, which is the whole
    # point of the fix, so the picture has to show a folder row.
    t = strip_y + 84
    shot.rect(M, t, W - M * 2, 600, fill="rgba(13,13,13,0.9)", stroke=LINE, r=18)
    shot.text(M + 20, t + 22, "NAME", size=10, fill=FAINT, spacing=1.4)
    shot.text(W - M - 74, t + 22, "SIZE", size=10, fill=FAINT, spacing=1.4, anchor="end")
    shot.line(M, t + 34, W - M, t + 34, stroke=LINE)

    rows = [
        ("folder", "FREE FIRE MAX/", "1701 files · 4 GB"),
        ("video", "index_287.m3u8", "229 KB"),
        ("archive", "Agon-1.4.58.apk", "2.4 MB"),
        ("archive", "Agon-1.4.60.apk", "2.4 MB"),
        ("video", "KRATOS_vs_ISHAN.mp4", "19 MB"),
        ("video", "KRATOS_vs_ISHAN_stacked.mp4", "14 MB"),
        ("video", "VID-20261105-WA0002.mp4", "3.2 MB"),
        ("video", "VID-20261105-WA0006.mp4", "3.7 MB"),
    ]
    row_h = 54
    for index, (kind, name, size) in enumerate(rows):
        ry = t + 34 + index * row_h
        # .row:hover and .row.folder both tint; the folder row is the one row on
        # the page that is a container rather than a file
        if kind == "folder":
            shot.rect(M + 1, ry, W - M * 2 - 2, row_h, fill="rgba(255,255,255,0.045)")
        else:
            shot.line(M + 18, ry, W - M - 18, ry, stroke="rgba(255,255,255,0.07)")
        shot.icon(M + 24, ry + 18, kind, 17, color=DIM)
        shot.text(M + 54, ry + 32, name, weight=600 if kind == "folder" else 500, size=13, fill=TEXT)
        shot.text(
            W - M - 74,
            ry + 32,
            size,
            family="mono",
            size=11.5,
            fill=FAINT if kind == "folder" else DIM,
            anchor="end",
        )
        shot.icon(W - M - 42, ry + 18, "download", 17, color=ACCENT)

    fy = H - 22
    shot.line(M, fy - 16, W - M, fy - 16, stroke=LINE)
    shot.text(M, fy, "LOCAL FILE SHARER 1.0.0", size=11.5, fill=FAINT)
    shot.text(W - M, fy, "Streaming · Resume · Range", size=11.5, fill=FAINT, anchor="end")
    return shot


# ── 3. media player (stream mode) ───────────────────────────────────────────
def player(*, chrome: bool = True) -> Shot:
    """The stream-mode player.

    ``chrome=False`` draws the same page with the title bar and the controls
    gone -- what it looks like a moment after playback starts, when
    ``.is-playing:not(.show-controls)`` has faded both out. ``web_gif.py``
    animates between the two, because that is the one player behaviour that a
    still cannot show.

    The layout is the page's: full-bleed stage, no page gutter, no card around
    the film, and the title and transport laid on top of the picture over
    scrims -- the same treatment the page applies in fullscreen, now applied
    everywhere. The shot used to draw the shared site header, a rounded card
    holding the film, and a warning banner underneath; none of those exist.
    """
    shot = Shot(W, H, url="http://192.168.1.42:8080/s/b45d56a6")

    stage_y = 64
    stage_h = H - stage_y
    sidebar_w = 340
    vw = W - sidebar_w

    shot.rect(0, stage_y, vw, stage_h, fill="#050505")
    band = shot.linear([(0, "rgba(255,255,255,0.04)"), (1, "rgba(5,5,5,0)")], x1=0, y1=0, x2=1, y2=1)
    shot.parts.append(f'<rect x="0" y="{stage_y}" width="{vw}" height="{stage_h}" fill="{band}"/>')

    # ── the film ────────────────────────────────────────────────────────────
    # An all-black stage reads as a broken player, which is the exact failure
    # mode this page has an overlay for, so the shot shows a low-key frame: a
    # low sun over ridges, in greys, the way a monochrome still of a colour
    # film would look.
    film_y = stage_y + stage_h * 0.14
    film_h = stage_h * 0.68
    sky = shot.linear([(0, "#2b2b2b"), (0.42, "#1b1b1b"), (1, "#0b0b0b")], x1=0, y1=0, x2=0, y2=1)
    shot.parts.append(f'<rect x="0" y="{film_y:.0f}" width="{vw}" height="{film_h:.0f}" fill="{sky}"/>')
    sun_x, sun_y = vw * 0.63, film_y + film_h * 0.52
    shot.parts.append(
        f'<circle cx="{sun_x:.0f}" cy="{sun_y:.0f}" r="150" '
        f'fill="{shot.radial([(0, "rgba(245,245,245,0.16)"), (1, "rgba(245,245,245,0)")])}"/>'
    )
    shot.parts.append(f'<circle cx="{sun_x:.0f}" cy="{sun_y:.0f}" r="42" fill="#e8e8e8"/>')
    horizon = film_y + film_h * 0.66
    shot.line(0, horizon, vw, horizon, stroke="rgba(255,255,255,0.18)", sw=1.2)
    land = (
        f"M0 {horizon:.0f} L0 {horizon - 40:.0f} L{vw * 0.12:.0f} {horizon - 96:.0f} "
        f"L{vw * 0.26:.0f} {horizon - 44:.0f} L{vw * 0.40:.0f} {horizon - 128:.0f} "
        f"L{vw * 0.58:.0f} {horizon - 36:.0f} L{vw * 0.72:.0f} {horizon - 84:.0f} "
        f"L{vw * 0.86:.0f} {horizon - 30:.0f} L{vw:.0f} {horizon - 66:.0f} L{vw:.0f} {horizon:.0f} Z"
    )
    shot.parts.append(f'<path d="{land}" fill="#0a0a0a"/>')
    for index in range(7):
        ry = horizon + 8 + index * 9
        rw = 40 + index * 16
        shot.parts.append(
            f'<rect x="{sun_x - rw / 2:.0f}" y="{ry}" width="{rw}" height="3" '
            f'fill="rgba(245,245,245,{0.22 - index * 0.026:.3f})" rx="1.5"/>'
        )
    vig = shot.radial([(0, "rgba(0,0,0,0)"), (1, "rgba(0,0,0,0.55)")], r=0.72)
    shot.parts.append(f'<rect x="0" y="{film_y:.0f}" width="{vw}" height="{film_h:.0f}" fill="{vig}"/>')

    if chrome:
        _player_chrome(shot, stage_y=stage_y, stage_h=stage_h, vw=vw)

    # ── the playlist rail ───────────────────────────────────────────────────
    # Its own scroll pane in the page, so it does not fade with the picture.
    sx = vw
    shot.rect(sx, stage_y, sidebar_w, stage_h, fill="rgba(6,6,6,0.92)")
    shot.line(sx, stage_y, sx, H, stroke=LINE)
    shot.text(sx + 18, stage_y + 32, "PLAYLIST · 1", size=11, fill=FAINT, spacing=1.2)
    shot.rect(
        sx + 16, stage_y + 46, sidebar_w - 32, 54, fill="#161616", stroke="rgba(255,255,255,0.16)", r=10
    )
    shot.text(sx + 28, stage_y + 68, "Aurora-Film…mkv", weight=600, size=12.5, fill=TEXT)
    shot.text(sx + 28, stage_y + 86, "3.6 GB", family="mono", size=11, fill=FAINT)
    shot.text(sx + sidebar_w - 28, stage_y + 86, "3.6 GB", family="mono", size=11, fill=FAINT, anchor="end")

    stats = [
        ("buffered", "38%"),
        ("transferred", "2.5 GB"),
        ("devices", "3"),
        ("format", "video/x-matroska"),
        ("shape", "16:9"),
    ]
    sy = stage_y + 132
    shot.line(sx + 16, sy - 14, sx + sidebar_w - 16, sy - 14, stroke=LINE)
    for label, value in stats:
        shot.text(sx + 18, sy, label, size=11.5, fill=DIM)
        shot.text(sx + sidebar_w - 18, sy, value, family="mono", size=11.5, fill=TEXT, anchor="end")
        sy += 26

    sy += 10
    shot.line(sx + 16, sy - 12, sx + sidebar_w - 16, sy - 12, stroke=LINE)
    shot.text(sx + 18, sy + 8, "Streaming with HTTP Range", size=11.5, fill=FAINT)
    shot.text(sx + 18, sy + 26, "seek anywhere instantly", size=11.5, fill=FAINT)
    return shot


def _player_chrome(shot: Shot, *, stage_y: int, stage_h: int, vw: int) -> None:
    """Title bar and transport, over the picture. This is the part that fades."""
    scrim = shot.linear([(0, "rgba(0,0,0,0.90)"), (1, "rgba(0,0,0,0)")], x1=0, y1=0, x2=0, y2=1)
    shot.parts.append(f'<rect x="0" y="{stage_y}" width="{vw}" height="120" fill="{scrim}"/>')
    shot.rect(20, stage_y + 16, 34, 34, fill="#111111", stroke=LINE_STRONG, r=10)
    shot.text(37, stage_y + 38, "LFS", family="mono", weight=700, size=11, fill=TEXT, anchor="middle")
    shot.text(66, stage_y + 36, "Aurora-Film-Festival-Keynote-4K.mkv", weight=650, size=14, fill=TEXT)
    shot.chip(66 + 340, stage_y + 24, "3.6 GB", size=10.5)
    shot.chip(66 + 340 + 76, stage_y + 24, "72 MB/s", size=10.5)
    shot.text(vw - 20, stage_y + 36, "1.0.0", family="mono", size=10.5, fill=FAINT, anchor="end")
    shot.parts.append(
        f'<path d="M{vw - 84} {stage_y + 24}l-5 6 5 6" stroke="{DIM}" stroke-width="1.8" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    shot.parts.append(
        f'<circle cx="{vw - 76}" cy="{stage_y + 30}" r="15" fill="none" stroke="{LINE}" stroke-width="1"/>'
    )

    bottom = stage_y + stage_h
    cscrim = shot.linear([(0, "rgba(0,0,0,0)"), (0.42, "rgba(0,0,0,0.88)"), (1, "rgba(0,0,0,0.95)")])
    shot.parts.append(f'<rect x="0" y="{bottom - 170}" width="{vw}" height="170" fill="{cscrim}"/>')

    pad = 26
    seek_y = bottom - 132
    shot.text(pad, seek_y + 4, "0:41:12", family="mono", size=11, fill=DIM)
    shot.progress(pad + 62, seek_y - 3, vw - pad * 2 - 124, 0.38, h=6)
    shot.circle(pad + 62 + (vw - pad * 2 - 124) * 0.38, seek_y, 7, fill="#ffffff")
    shot.text(vw - pad, seek_y + 4, "1:48:20", family="mono", size=11, fill=DIM, anchor="end")

    cy = bottom - 74
    shot.circle(pad + 21, cy, 21, fill="#ffffff")
    shot.parts.append(
        f'<path d="M{pad + 17} {cy - 9} L{pad + 30} {cy} L{pad + 17} {cy + 9} Z" fill="#000000"/>'
    )
    for offset, label in ((72, "10"), (128, "10")):
        shot.circle(pad + offset, cy, 17, fill="#0a0a0a")
        shot.parts.append(
            f'<circle cx="{pad + offset}" cy="{cy}" r="17" fill="none" stroke="{LINE_STRONG}" stroke-width="1"/>'
        )
        shot.text(pad + offset, cy + 4, label, weight=700, size=10, fill=DIM, anchor="middle")
    mx = pad + 184
    shot.circle(mx, cy, 17, fill="#0a0a0a")
    shot.parts.append(
        f'<circle cx="{mx}" cy="{cy}" r="17" fill="none" stroke="{LINE_STRONG}" stroke-width="1"/>'
    )
    shot.parts.append(f'<path d="M{mx - 7} {cy - 3}h4l5-4v14l-5-4h-4z" fill="{DIM}"/>')
    shot.parts.append(
        f'<path d="M{mx + 4} {cy - 4}a5 5 0 0 1 0 8" stroke="{DIM}" stroke-width="1.4" fill="none"/>'
    )
    vx = mx + 30
    shot.line(vx, cy, vx + 74, cy, stroke="rgba(255,255,255,0.20)", sw=5)
    shot.line(vx, cy, vx + 56, cy, stroke="#ffffff", sw=5)
    shot.circle(vx + 56, cy, 6, fill="#ffffff")

    rx = vw - pad - 17 - 3 * 44
    for kind in ("rate", "pip", "fit", "full"):
        shot.circle(rx, cy, 17, fill="#0a0a0a")
        shot.parts.append(
            f'<circle cx="{rx}" cy="{cy}" r="17" fill="none" stroke="{LINE_STRONG}" stroke-width="1"/>'
        )
        _transport_glyph(shot, rx, cy, kind)
        rx += 44


def _transport_glyph(shot, cx: float, cy: float, kind: str) -> None:
    """The symbol inside a round control, in the page's own geometry."""
    if kind == "rate":
        # speedometer: an arc with a needle, exactly the path player.html uses
        shot.parts.append(
            f'<path d="M{cx - 8} {cy + 5}a8.5 8.5 0 1 1 15.6 0" fill="none" stroke="{DIM}" '
            'stroke-width="1.5" stroke-linecap="round"/>'
        )
        shot.parts.append(
            f'<path d="M{cx} {cy + 3}l4-4" stroke="{DIM}" stroke-width="1.5" stroke-linecap="round"/>'
        )
        shot.circle(cx, cy + 4, 1.4, fill=DIM)
    elif kind == "pip":
        shot.parts.append(
            f'<rect x="{cx - 8}" y="{cy - 6}" width="16" height="12" rx="2" fill="none" '
            f'stroke="{DIM}" stroke-width="1.4"/>'
        )
        shot.parts.append(f'<rect x="{cx + 1}" y="{cy}" width="6" height="5" rx="1" fill="{DIM}"/>')
    elif kind == "fit":
        shot.parts.append(
            f'<rect x="{cx - 8}" y="{cy - 6}" width="16" height="12" rx="2" fill="none" '
            f'stroke="{DIM}" stroke-width="1.4"/>'
        )
        shot.parts.append(f'<path d="M{cx - 4} {cy}h8M{cx} {cy - 4}v8" stroke="{DIM}" stroke-width="1.4"/>')
    else:  # fullscreen: four corners
        for dx, dy, sx, sy in ((-8, -6, 1, 1), (8, -6, -1, 1), (-8, 6, 1, -1), (8, 6, -1, -1)):
            shot.parts.append(
                f'<path d="M{cx + dx} {cy + dy + 4 * sy}V{cy + dy}H{cx + dx + 4 * sx}" fill="none" '
                f'stroke="{DIM}" stroke-width="1.4" stroke-linecap="round"/>'
            )


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
