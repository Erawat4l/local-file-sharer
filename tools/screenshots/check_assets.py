#!/usr/bin/env python3
"""Fail if a screenshot would ship with tofu boxes or a leaking path.

Run after regenerating the PNGs (CI does). It reads *the committed images*, not
the renderer's intentions, which is the only way to catch a silently missing
Twemoji tree: `to_svg` happily emits a fallback glyph instead of raising.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "docs" / "assets"
README = REPO / "README.md"

#: A path component that must never appear in a screenshot.
FORBIDDEN = ("/home/", "C:\\Users", "scratch", "inputs/sdcard")


def referenced_images() -> dict[str, bool]:
    """README image links as ``{name: exists}``."""
    text = README.read_text(encoding="utf-8")
    # The README uses HTML <img> tags for width control; accept Markdown too.
    refs = re.findall(r"<img[^>]*\ssrc=[\"'](?:\./)?docs/assets/([^\"'\s]+)", text)
    refs += re.findall(r"!\[[^\]]*\]\((?!https?:)(?:\./)?docs/assets/([^)\s]+)", text)
    return {name: (ASSETS / name).exists() for name in dict.fromkeys(refs)}


def image_problems() -> list[str]:
    problems: list[str] = []
    for name, exists in referenced_images().items():
        if not exists:
            problems.append(f"README references docs/assets/{name}, which does not exist")
    for path in sorted(ASSETS.glob("*.png")):
        if path.stat().st_size < 1000:
            problems.append(f"{path.name} is suspiciously small ({path.stat().st_size} bytes)")
    return problems


def png_text() -> str:
    """All text that a PNG asset could have baked into it.

    PNGs here are rendered from SVG, so the strings live in the image, not in a
    sidecar file — this only checks the file names and the README instead.
    """
    return "\n".join(path.name for path in ASSETS.iterdir())


def git_tracked_assets() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "docs/assets"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:  # pragma: no cover - git is optional
        return []
    return [line for line in out.splitlines() if line]


def uncovered_emoji() -> list[str]:
    """Emoji used by the terminal shots that Twemoji cannot draw.

    This is the cheap version of looking at the PNG: the renderer substitutes a
    fallback glyph and says nothing, so the only way to notice is to ask the
    same lookup the renderer asks.
    """
    for extra in (REPO / "src", REPO / "tools" / "screenshots"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    try:
        import termsvg  # noqa: PLC0415 - optional maintainer tool
    except ModuleNotFoundError as exc:  # pragma: no cover - CI without the toolchain
        print(f"! skipping the emoji check ({exc.name} is not installed)")
        return []

    # The Twemoji tree is gitignored (4009 SVG files, MIT/CC-BY, fetched by
    # hand). Without saying so, this check reported every glyph in the menu as
    # tofu on a clean clone -- which is most of them -- and CI failed on a
    # checkout that simply does not contain the inputs. Absent inputs are a
    # skip, not a defect: the PNGs are committed and check_assets cannot
    # regenerate them anyway.
    if not termsvg.TWEMOJI.exists():
        print(f"! skipping the emoji check ({termsvg.TWEMOJI} is not present —")
        print("  it is gitignored; see tools/screenshots/README.md)")
        return []

    from local_file_sharer.menu import ACTIONS  # noqa: PLC0415

    glyphs: set[str] = {"\U0001f4bb", "\U0001f4f1", "\U0001f680", "\u2705", "\u26a0"}
    for action in ACTIONS:
        glyphs.update(ch for ch in str(getattr(action, "icon", "")) if ord(ch) > 0x2000)

    return sorted(ch for ch in glyphs if termsvg._twemoji(ch) is None)


def main() -> int:
    problems = image_problems()
    for needle in FORBIDDEN:
        if needle in png_text():
            problems.append(f"a docs/assets file name contains {needle!r}")
    untracked = set(referenced_images()) - {Path(p).name for p in git_tracked_assets()}
    if git_tracked_assets():
        for name in sorted(untracked):
            problems.append(f"docs/assets/{name} is referenced but not committed")

    for glyph in uncovered_emoji():
        problems.append(f"no Twemoji SVG for {glyph!r} — it would render as tofu")

    for problem in problems:
        print(f"✖ {problem}")
    if problems:
        return 1
    count = len(referenced_images())
    print(f"✓ {count} README images all present and committed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
