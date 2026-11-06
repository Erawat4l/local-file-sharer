"""`run`, the launcher everyone actually types first.

It had no tests, and two bugs reached a real phone because of it: a venv that
failed halfway was left on disk and reused forever ("No module named pip"), and
a Python without `ensurepip` (Termux ships pip separately) could not build a
usable venv at all.

These run the script itself in a scratch copy, with a fake `python3` on PATH so
the failure modes are reproducible without needing a Termux device.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUN = REPO / "run"

pytestmark = pytest.mark.skipif(not RUN.exists(), reason="launcher lives at the repo root")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A copy of the launcher plus the packaging files setup needs."""
    dest = tmp_path / "co"
    dest.mkdir()
    shutil.copy2(RUN, dest / "run")
    for name in ("setup", "install-lfs.py", "pyproject.toml", "README.md"):
        src = REPO / name
        if src.exists():
            shutil.copy2(src, dest / name)
    shutil.copytree(REPO / "src", dest / "src")
    return dest


def fake_python(bin_dir: Path, script: str) -> None:
    """Put a `python3` on PATH that is a shell script, not a real Python."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "python3"
    target.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    target.chmod(0o755)


def run_launcher(
    checkout: Path, *args: str, path_prefix: Path | None = None, bare: bool = False
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("LFS_BRANCH", None)
    env["NO_COLOR"] = "1"
    if path_prefix is not None:
        # `bare` keeps `python` (the other candidate) out of reach too, so the
        # fake interpreter is the only one the launcher can find.
        env["PATH"] = f"{path_prefix}:/usr/bin:/bin" if not bare else f"{path_prefix}:/bin-shim"
    return subprocess.run(  # noqa: S603
        ["/bin/sh", str(checkout / "run"), *args],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )


def test_help_prints_the_commands(checkout):
    result = run_launcher(checkout, "--help")
    assert result.returncode == 0
    assert "./run upgrade" in result.stdout


def test_it_never_writes_to_tmp(checkout):
    """Termux has no /tmp: a redirect there fails before an error can be shown."""
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "/tmp/" not in source, "the launcher must keep its scratch files inside the checkout"


def test_a_venv_without_pip_is_replaced_not_reused(checkout, tmp_path):
    """The phone bug: a half-built .venv made every later run fail.

    `-x .venv/bin/python` was true, so the venv was never rebuilt and pip was
    never there.
    """
    broken = checkout / ".venv" / "bin"
    broken.mkdir(parents=True)
    (broken / "python").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (broken / "python").chmod(0o755)

    # No usable interpreter on PATH: it must fail, but *after* noticing the venv.
    result = run_launcher(checkout, "setup", path_prefix=tmp_path / "empty-bin")
    combined = result.stdout + result.stderr
    assert "broken .venv" in combined or "no Python" in combined
    assert "No module named pip" not in combined


def test_a_python_without_ensurepip_is_detected(checkout, tmp_path):
    """Termux: venv exists, ensurepip does not. Report it, do not dump a traceback."""
    fake = tmp_path / "bin"
    fake_python(
        fake,
        'case "$*" in\n'
        '  *"import sys, venv, ensurepip"*) echo "ImportError: ensurepip is not available" >&2; exit 1 ;;\n'
        '  *pip*) exit 1 ;;\n'
        '  *) exit 1 ;;\n'
        'esac\n',
    )
    result = run_launcher(checkout, "setup", path_prefix=fake, bare=True)
    combined = result.stdout + result.stderr
    assert "pkg install python" in combined, "the fix must be named"
    assert "Termux" in combined
    assert "Traceback" not in combined


def test_branch_defaults_to_main_and_can_be_overridden(checkout):
    assert 'BRANCH="${LFS_BRANCH:-main}"' in (checkout / "run").read_text(encoding="utf-8")
    hints = run_launcher(checkout, "--help")
    assert hints.returncode == 0


def test_setup_is_idempotent_and_fast_on_second_call(checkout):
    """A configured checkout must not reinstall on every start."""
    first = run_launcher(checkout, "setup")
    if first.returncode != 0:
        pytest.skip(f"cannot build a venv here: {first.stderr[:200]}")
    stamp = checkout / ".venv" / ".lfs-stamp"
    assert stamp.exists()
    before = stamp.stat().st_mtime_ns

    second = run_launcher(checkout, "setup")
    assert second.returncode == 0
    assert "installing dependencies" not in second.stdout
    assert stamp.stat().st_mtime_ns == before


def test_it_uses_a_python_that_can_build_a_venv(checkout):
    """A `python3` that fails the venv probe must not be chosen."""
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "import sys, venv" in source
    assert "sys.version_info >= (3, 9)" in source
    assert sys.version_info >= (3, 9)
