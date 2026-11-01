"""Allow ``python -m local_file_sharer`` (and PyInstaller's entry point)."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
