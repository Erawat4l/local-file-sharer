"""Keep the device awake for as long as a share is running.

Why this exists
---------------
Android suspends the CPU a minute or two after the screen goes off. The Wi-Fi
radio follows, and an in-progress download simply stops: the browser keeps the
connection open and waits, the bytes never come, and the transfer reads as
"it was fast and then it just froze". Nothing in the server logs an error,
because nothing failed — the process was frozen, not interrupted.

``termux-wake-lock`` asks Android to keep the device up. It is part of the
``termux-api`` package, which may not be installed, so every call here is
best-effort and reports whether it worked instead of raising.

On every other platform this does nothing: a desktop or laptop serving a file
does not idle itself into a suspended state the same way.
"""

from __future__ import annotations

import atexit
import subprocess
import sys

from . import platforms

#: Set once a lock is held, so a second call does not spawn a second process.
_held = False
#: Set once acquisition has been attempted and failed, so it is not retried on
#: every share.
_unavailable = False


def _termux_api_installed() -> bool:
    return platforms.find_tool("termux-wake-lock") is not None


def acquire() -> tuple[bool, str]:
    """Ask Android to stay awake. Returns ``(held, message)``.

    ``message`` is for the dashboard/CLI: it is empty on success and explains
    what to install when the helper is missing.
    """
    global _held, _unavailable
    if _held:
        return True, ""
    if _unavailable or not platforms.TERMUX:
        return False, ""
    if not _termux_api_installed():
        _unavailable = True
        return False, (
            "Android may sleep the device and stall long downloads; "
            "install the Termux:API app to keep it awake"
        )
    try:
        proc = subprocess.run(  # noqa: S603
            ["termux-wake-lock"],
            capture_output=True,
            timeout=platforms.QUICK_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _unavailable = True
        return False, ""
    if proc.returncode != 0:
        _unavailable = True
        return False, ""
    _held = True
    atexit.register(release)
    return True, ""


def release() -> None:
    """Drop the lock. Safe to call when nothing was acquired."""
    global _held
    if not _held:
        return
    _held = False
    try:
        subprocess.run(  # noqa: S603
            ["termux-wake-unlock"],
            capture_output=True,
            timeout=platforms.QUICK_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def is_held() -> bool:
    return _held


def describe() -> str:
    """One line for `lfs doctor`."""
    if not platforms.TERMUX:
        return "not needed on this platform"
    if _held:
        return "held - Android will not sleep while the share is running"
    if _unavailable:
        return "unavailable - install the Termux:API app to keep the device awake"
    return "not held yet - taken when a share starts"


if sys.platform.startswith("win"):  # pragma: no cover - documented no-op
    # Windows has no equivalent helper; the module is a no-op there.
    pass
