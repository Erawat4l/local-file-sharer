#!/bin/sh
# Everything CI checks that can be checked on one machine.
#
#   sh tools/precheck.sh
#
# Run this before pushing. It is deliberately the same commands the workflow
# uses, so a green run here means the only thing left is the OS matrix.
set -e
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

py=python3
[ -x .venv/bin/python ] && py=.venv/bin/python

"$py" -m ruff check src tests tools
"$py" -m ruff format --check src tests tools
"$py" tools/check_compat.py
"$py" tools/check_fish.py
"$py" tools/screenshots/check_assets.py
sh -n install.sh
"$py" -m pytest -q --tb=short 2>&1 | tail -5
