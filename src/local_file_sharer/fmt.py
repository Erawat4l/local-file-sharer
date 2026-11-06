"""Human friendly formatting helpers (sizes, speeds, durations, text fitting).

All helpers are pure and unit-tested; they never raise on weird input.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(num: float | int | None, *, precision: int | None = None) -> str:
    """Format a byte count like ``1.4 GB``.

    Uses 1024-based units because that is what file managers show.
    """
    if num is None:
        return "—"
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "—"
    if math.isnan(value) or math.isinf(value):
        return "—"
    negative = value < 0
    value = abs(value)
    idx = 0
    while value >= 1024 and idx < len(_SIZE_UNITS) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        text = f"{round(value)} {_SIZE_UNITS[idx]}"
    else:
        digits = precision if precision is not None else (0 if value >= 100 else 1)
        text = f"{value:.{digits}f} {_SIZE_UNITS[idx]}"
    if text.endswith(".0 " + _SIZE_UNITS[idx]) and idx:
        text = text.replace(".0 ", " ")
    return f"-{text}" if negative else text


def human_speed(bytes_per_sec: float | None) -> str:
    if bytes_per_sec is None:
        return "—"
    if bytes_per_sec <= 0:
        return "0 B/s"
    return f"{human_size(bytes_per_sec)}/s"


def human_time(seconds: float | None) -> str:
    """Format a duration like ``1h 04m`` or ``12s`` (ETA friendly)."""
    if seconds is None or seconds < 0 or math.isnan(seconds) or math.isinf(seconds):
        return "—"
    seconds = round(seconds)
    if seconds < 1:
        return "0s"
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


def human_count(num: int | float, singular: str, plural: str | None = None) -> str:
    plural = plural or singular + "s"
    return f"{int(num)} {singular if int(num) == 1 else plural}"


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def cell_len(text: str) -> int:
    """Terminal cell width of *text* (wide CJK/emoji count as 2)."""
    width = 0
    for ch in strip_ansi(text):
        if unicodedata.combining(ch):
            continue
        east = unicodedata.east_asian_width(ch)
        width += 2 if east in ("W", "F") else 1
    return width


def fit(text: str, width: int, *, ellipsis: str = "…", middle: bool = False) -> str:
    """Truncate *text* to *width* terminal cells, keeping the ellipsis.

    ``middle=True`` keeps both ends (useful for long paths).
    """
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    if width <= cell_len(ellipsis):
        return ellipsis[:width]
    budget = width - cell_len(ellipsis)
    if not middle:
        out: list[str] = []
        used = 0
        for ch in text:
            w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            if used + w > budget:
                break
            out.append(ch)
            used += w
        return "".join(out) + ellipsis
    left = budget // 2
    right = budget - left
    head: list[str] = []
    used = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > left:
            break
        head.append(ch)
        used += w
    tail: list[str] = []
    used = 0
    for ch in reversed(text):
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > right:
            break
        tail.append(ch)
        used += w
    return "".join(head) + ellipsis + "".join(reversed(tail))


def pad(text: str, width: int, *, align: str = "left") -> str:
    """Pad *text* to an exact terminal-cell width."""
    text = fit(text, width)
    gap = max(0, width - cell_len(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def percent(done: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, done / total * 100.0))


def bar(done: float, total: float, width: int = 28, *, filled: str = "█", empty: str = "░") -> str:
    width = max(1, width)
    ratio = 0.0 if total <= 0 else max(0.0, min(1.0, done / total))
    n = round(ratio * width)
    return filled * n + empty * (width - n)


def local_timestamp(dt: datetime | None, *, with_seconds: bool = False) -> str:
    if dt is None:
        return "—"
    try:
        local = dt.astimezone()
    except (ValueError, OSError):
        local = dt
    fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
    return local.strftime(fmt)


def from_epoch(ts: float | None) -> datetime | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def clock(seconds: float) -> str:
    """mm:ss / h:mm:ss — used by web players and torrent hints."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── icons ────────────────────────────────────────────────────────────────────
# Some emoji-ish glyphs (⬇ ▶ ⚙ ⌨ ⚠ ✔ ⬆ ⏏ ✖) live in the "ambiguous width" range:
# rich measures them as one cell but most terminals (and every browser) draw
# them two cells wide, which shifts everything to their right. Presenting them
# with a trailing VS16 makes rich agree with the terminal, so layout stays put.
_WIDE_ICONS = "\u2197\u2328\u23cf\u23f8\u25b6\u2699\u26a0\u2714\u2716\u2b06\u2b07"


def icon(glyph: str) -> str:
    """Return *glyph* padded with VS16 when Rich would under-measure it."""
    if glyph and glyph[0] in _WIDE_ICONS and not glyph.endswith("\ufe0f"):
        return glyph + "\ufe0f"
    return glyph


def icon_width_safe(text: str) -> str:
    """Apply :func:`icon` to every ambiguous-width glyph inside *text*."""
    out = []
    for index, char in enumerate(text):
        if char in _WIDE_ICONS and text[index + 1 : index + 2] != "\ufe0f":
            out.append(char + "\ufe0f")
        else:
            out.append(char)
    return "".join(out)
