"""Turn pytest's ``FAILED`` lines into one GitHub annotation per failure.

The only part of a CI run this project can read back from a workstation is the
annotation list: the log blob and the uploaded artifacts are served from a
host that is not always reachable. A multi-line annotation is truncated at its
first newline, so each failure gets its own, carrying the reason that
``--tb=line -rf`` puts after the dash.

Run directly on a captured pytest log:

    pytest -q --tb=line -rf | tee pytest-output.txt
    python tools/report_failures.py pytest-output.txt
"""

from __future__ import annotations

import pathlib
import sys


def annotations(text: str) -> list[str]:
    """One ``::error title=<test>::<reason>`` line per failure in ``text``."""
    found = []
    for line in text.splitlines():
        if not line.startswith("FAILED "):
            continue
        name, _, reason = line[len("FAILED ") :].partition(" - ")
        name = " ".join(name.split())
        found.append(f"::error title={name}::{reason.strip() or 'failed'}")
    return found


def main(argv: list[str]) -> int:
    path = pathlib.Path(argv[1] if len(argv) > 1 else "pytest-output.txt")
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    lines = annotations(text)
    if not lines:
        # A collection or install error names no test; say so rather than
        # leaving the job red with nothing to read.
        lines = ["::error::pytest failed without naming a test (collection or install error)"]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
