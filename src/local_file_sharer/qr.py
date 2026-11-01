"""QR code rendering for terminals of any width.

Uses the ``qrcode`` package to build the matrix and prints it with half blocks
(``▀ ▄ █``) so a QR fits in ~30 rows on a phone. Includes a quiet zone, compact
and large modes, and a data-URL helper for the web UI.
"""

from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass

import qrcode
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_M

from .ui import console as console_mod
from .ui.theme import Theme

RESET = "\x1b[0m"


@dataclass
class QROptions:
    box_size: int = 1
    border: int = 2
    error_correction: int = ERROR_CORRECT_M
    compact: bool = True


def matrix(data: str, *, error_correction: int = ERROR_CORRECT_M, border: int = 2) -> list[list[bool]]:
    """Return the QR module matrix (True = dark) including the quiet zone."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=error_correction,
        box_size=1,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return [[bool(cell) for cell in row] for row in qr.get_matrix()]


def to_ascii(data: str, *, error_correction: int = ERROR_CORRECT_M, border: int = 2) -> str:
    """Full-block QR (two characters per module) — the most compatible form."""
    rows = matrix(data, error_correction=error_correction, border=border)
    lines = []
    for row in rows:
        lines.append("".join("██" if cell else "  " for cell in row))
    return "\n".join(lines)


def to_half_blocks(
    data: str,
    *,
    error_correction: int = ERROR_CORRECT_M,
    border: int = 2,
    dark: str = "█",
    light: str = " ",
    upper: str = "▀",
    lower: str = "▄",
) -> str:
    """Compact QR using half blocks: two module rows share one text row."""
    rows = matrix(data, error_correction=error_correction, border=border)
    # Pad to an even number of rows so both halves always pair up.
    if len(rows) % 2:
        rows.append([False] * len(rows[0]))
    lines: list[str] = []
    for index in range(0, len(rows), 2):
        top = rows[index]
        bottom = rows[index + 1]
        chars: list[str] = []
        for t, b in zip(top, bottom):  # noqa: B905 - strict= is 3.10+
            if t and b:
                chars.append(dark)
            elif t:
                chars.append(upper)
            elif b:
                chars.append(lower)
            else:
                chars.append(light)
        lines.append("".join(chars))
    return "\n".join(lines)


def colored(
    data: str,
    theme: Theme,
    *,
    error_correction: int = ERROR_CORRECT_M,
    border: int = 2,
    compact: bool = True,
) -> str:
    """Half-block QR tinted with the theme accent (still scans on dark themes)."""
    body = (
        to_half_blocks(data, error_correction=error_correction, border=border)
        if compact
        else to_ascii(data, error_correction=error_correction, border=border)
    )
    accent = theme.accent
    # ``accent`` may be a rich name or a hex value; wrap the dark modules.
    lines = []
    for line in body.splitlines():
        if compact:
            transformed = (
                line.replace("█", f"[{accent}]█[/]")
                .replace("▀", f"[{accent}]▀[/]")
                .replace("▄", f"[{accent}]▄[/]")
            )
        else:
            transformed = line.replace("██", f"[{accent}]██[/]")
        lines.append(transformed)
    return "\n".join(lines)


def print_qr(
    data: str,
    *,
    theme: Theme | None = None,
    compact: bool = True,
    caption: str = "",
    error_correction: int = ERROR_CORRECT_M,
) -> None:
    """Render the QR to the console with an optional caption."""
    con = console_mod.console()
    theme = theme or console_mod.current_theme()
    from rich.text import Text  # noqa: PLC0415

    body = (
        to_half_blocks(data, error_correction=error_correction)
        if compact
        else to_ascii(data, error_correction=error_correction)
    )
    text = Text()
    for line in body.splitlines():
        for char in line:
            if char == " ":
                text.append(" ")
            else:
                text.append(char, style=theme.accent)
        text.append("\n")
    con.print(text, end="")
    if caption:
        con.print(Text(caption, style=theme.muted, justify="left"))


def _png(matrix_rows: list[list[bool]], scale: int) -> bytes:
    """A minimal 1-bit greyscale PNG — no Pillow, no qrcode-pil plugin."""
    cells = len(matrix_rows)
    width = cells * scale
    height = width
    raw = bytearray()
    for row in matrix_rows:
        line = bytearray(b"\x00")  # filter type 0
        for value in row:
            line.extend((b"\x00" if value else b"\xff") * scale)  # 0 = black
        # Repeat the scanline `scale` times, once per pixel row.
        raw.extend(bytes(line) * scale)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit greyscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def svg(data: str, *, error_correction: int = ERROR_CORRECT_M, border: int = 2) -> str:
    """Inline SVG markup for QR rendering (no Pillow needed)."""
    rows = matrix(data, error_correction=error_correction, border=border)
    size = len(rows)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR code">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g fill="#000000">',
    ]
    for y, row in enumerate(rows):
        x = 0
        while x < size:
            if not row[x]:
                x += 1
                continue
            run = 0
            while x + run < size and row[x + run]:
                run += 1
            parts.append(f'<rect x="{x}" y="{y}" width="{run}" height="1"/>')
            x += run
    parts.append("</g></svg>")
    return "".join(parts)


def svg_data_url(data: str, *, error_correction: int = ERROR_CORRECT_M) -> str:
    payload = base64.b64encode(svg(data, error_correction=error_correction).encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"


def size_hint(url: str) -> tuple[int, int]:
    """Approximate terminal footprint ``(columns, rows)`` for a QR."""
    rows = matrix(url)
    cols = len(rows[0]) if rows else 0
    return cols, (len(rows) + 1) // 2


def fits(url: str, *, width: int, height: int, reserved: int = 12) -> bool:
    cols, rows = size_hint(url)
    return cols <= width and rows + reserved <= height


def choose_error_correction(url: str, *, width: int) -> int:
    """Prefer high correction; fall back to medium on narrow terminals."""
    cols, _ = size_hint(url)
    if cols > max(30, width - 6):
        return ERROR_CORRECT_M
    return ERROR_CORRECT_H
