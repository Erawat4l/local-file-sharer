"""Where the screenshot tools look for their inputs and write their outputs.

Fonts, Twemoji and the demo tree are *inputs*: they are large, separately
licensed and deliberately not committed. Point ``LFS_SHOT_INPUTS`` at a folder
laid out like::

    <inputs>/fonts/JetBrainsMono-{Regular,Bold}.ttf
    <inputs>/fonts/Inter-{Regular,Medium,SemiBold,Bold,ExtraBold}.ttf
    <inputs>/fonts/Noto-COLRv1.ttf        (only used for glyph previews)
    <inputs>/twemoji/<codepoint>.svg
    <inputs>/demo/…                        (see README.md)

Outputs always go to ``docs/assets/`` in the checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "docs" / "assets"
INPUTS = Path(os.environ.get("LFS_SHOT_INPUTS", REPO / "tools" / "screenshots" / "inputs"))
FONT_DIR = INPUTS / "fonts"
TWEMOJI_DIR = INPUTS / "twemoji"
DEMO_DIR = INPUTS / "demo"


def font(name: str) -> str:
    """Absolute path of a font file inside the inputs folder."""
    path = FONT_DIR / name
    if not path.exists():
        raise SystemExit(
            f"missing font: {path}\n"
            "See tools/screenshots/README.md — set LFS_SHOT_INPUTS or drop the fonts there."
        )
    return str(path)
