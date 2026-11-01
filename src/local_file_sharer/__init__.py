"""local-file-sharer — share files between your devices at maximum speed.

A premium terminal file sharer for Termux/Android, Linux and Windows.

Public entry point: ``local_file_sharer.cli:main`` (installed as ``start``).
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "Erawat4l"
APP_NAME = "local-file-sharer"
APP_TAGLINE = "Share files between your devices at maximum speed"
REPO_SLUG = "Erawat4l/local-file-sharer"
REPO_URL = f"https://github.com/{REPO_SLUG}"

__all__ = [
    "APP_NAME",
    "APP_TAGLINE",
    "REPO_SLUG",
    "REPO_URL",
    "__author__",
    "__version__",
]
