"""LOCAL FILE SHARER — share files between your devices at maximum speed.

A premium terminal file sharer for Termux/Android, Linux and Windows.

Public entry point: ``local_file_sharer.cli:main`` (installed as ``lfs``).
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "Erawat4l"
APP_NAME = "LOCAL FILE SHARER"
#: The name as the banner writes it: the gif says "local-file-sharer v1.0.0",
#: while menus and reports use APP_NAME. Both are deliberate.
APP_SLUG = "local-file-sharer"
#: The command users type. Kept in one place because it appears in ~30 hints.
APP_CMD = "lfs"
APP_TAGLINE = "Share files between your devices at maximum speed"
#: Changes whenever the shipped web UI changes, so a browser tab can say which
#: build it is running. `__version__` cannot do this: it is 1.0.0 for every
#: commit, which is how "I upgraded and nothing changed" stayed invisible.
BUILD_STAMP = "2026-11-06.player3"

REPO_SLUG = "Erawat4l/local-file-sharer"
REPO_URL = f"https://github.com/{REPO_SLUG}"

__all__ = [
    "APP_CMD",
    "APP_NAME",
    "APP_SLUG",
    "APP_TAGLINE",
    "REPO_SLUG",
    "REPO_URL",
    "__author__",
    "__version__",
]
