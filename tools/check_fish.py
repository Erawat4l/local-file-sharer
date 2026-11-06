#!/usr/bin/env python3
"""Cheap structural checks for the fish functions.

``fish --no-execute`` is the real test (CI runs it). This script exists so
contributors without fish still catch the common mistakes: unbalanced
``end``/``function`` blocks and quoting errors.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
#: The functions were renamed start.fish -> lfs.fish; this list did not follow,
#: so CI's "fish structural check" failed on two missing files and had been red
#: ever since. Point at what the repo actually ships, and find them rather than
#: hard-code, so the next rename does not silently disable the check.
FILES = sorted(
    [
        *(REPO / "shell" / "fish").glob("*.fish"),
        *(REPO / "shell" / "windows").glob("*.fish"),
    ]
)


def check(path: Path) -> list[str]:
    problems: list[str] = []
    depth = 0
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split()[0]
        if head in ("function", "if", "for", "while", "switch", "begin"):
            depth += 1
        elif head == "end":
            depth -= 1
            if depth < 0:
                problems.append(f"{path}:{number}: `end` without a matching block")
                depth = 0
        if raw.count('"') % 2 and not raw.rstrip().endswith("\\"):
            problems.append(f"{path}:{number}: odd number of double quotes")
    if depth:
        problems.append(f"{path}: {depth} unclosed block(s)")
    return problems


def main() -> int:
    problems: list[str] = []
    for path in FILES:
        if not path.exists():
            problems.append(f"missing {path}")
            continue
        problems.extend(check(path))
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"ok — {len(FILES)} fish files look balanced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
