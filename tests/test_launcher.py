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
    """Put a `python3` *and* a `python` on PATH, both shell scripts.

    Both, because `run` falls back to `python` when `python3` fails its probe —
    a real interpreter in the sandbox would then answer for the fake one and
    the test would silently pass for the wrong reason.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("python3", "python"):
        target = bin_dir / name
        target.write_text("#!/bin/sh\n" + script, encoding="utf-8")
        target.chmod(0o755)


def run_launcher(
    checkout: Path, *args: str, path_prefix: Path | None = None
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("LFS_BRANCH", None)
    env["NO_COLOR"] = "1"
    if path_prefix is not None:
        # The fake directory comes first and defines both `python3` and
        # `python`; /usr/bin stays behind it for coreutils (rm, tail, sed).
        env["PATH"] = f"{path_prefix}:/usr/bin:/bin"
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
    result = run_launcher(checkout, "setup", path_prefix=fake)
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


# ── Termux: python without pip, and pip as a separate package ────────────────
def test_termux_is_recognised_by_prefix(checkout):
    """`$PREFIX=…com.termux…` is the reliable signal; nothing else is."""
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "com.termux" in source
    assert "python-pip" in source, "the package that actually provides pip must be named"


def test_termux_does_not_repeat_the_package_it_already_installed(checkout):
    """The loop: told to run `pkg install python-pip`, ran it, told again.

    If the interpreter can already see pip, offering that package again is
    useless advice, so the message has to change.
    """
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "pip is installed but unusable" in source
    assert "system_has_pip" in source


def test_missing_pip_on_termux_installs_python_pip(checkout, tmp_path):
    """Termux splits pip out: `python` has venv, `python-pip` has pip.

    The old message said `pkg install python`, which installs nothing new and
    leaves the user exactly where they started.
    """
    fake = tmp_path / "bin"
    fake_python(
        fake,
        'case "$*" in\n'
        '  *"import sys, venv; sys.exit"*) exit 0 ;;\n'
        '  *"import sys, venv, ensurepip"*) exit 1 ;;\n'
        '  *"-m pip"*) exit 1 ;;\n'
        '  *"-m venv"*) echo "Error: could not create venv" >&2; exit 1 ;;\n'
        '  *--version*) echo "Python 3.14.10" ;;\n'
        '  *) exit 1 ;;\n'
        'esac\n',
    )
    pkg = fake / "pkg"
    pkg.write_text(
        f'#!/bin/sh\necho "$@" >> {tmp_path}/pkgs\necho installed\n',
        encoding="utf-8",
    )
    pkg.chmod(0o755)

    env = dict(os.environ)
    env.update(
        PATH=f"{fake}:/bin-shim",
        NO_COLOR="1",
        ANDROID_ROOT="/system",
        PREFIX="/data/data/com.termux/files/usr",
    )
    result = subprocess.run(  # noqa: S603
        ["/bin/sh", str(checkout / "run"), "setup"],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert "python-pip" in combined
    calls = (tmp_path / "pkgs").read_text(encoding="utf-8") if (tmp_path / "pkgs").exists() else ""
    assert "install" in calls and "python-pip" in calls, "it must actually install it"


def test_a_failed_venv_shows_what_went_wrong(checkout, tmp_path):
    """The old code guessed the reason from a case statement, and guessed wrong.

    Whatever the interpreter said must reach the user, not be replaced by a
    stock suggestion.
    """
    fake = tmp_path / "bin"
    fake_python(
        fake,
        'case "$*" in\n'
        '  *"import sys, venv; sys.exit"*) exit 0 ;;\n'
        '  *"import sys, venv, ensurepip"*) exit 1 ;;\n'
        '  *"-m pip"*) exit 1 ;;\n'
        '  *"-m venv"*) echo "Permission denied: /system/lib64" >&2; exit 1 ;;\n'
        '  *--version*) echo "Python 3.14.10" ;;\n'
        '  *) exit 1 ;;\n'
        'esac\n',
    )
    result = run_launcher(checkout, "setup", path_prefix=fake)
    combined = result.stdout + result.stderr
    assert "Permission denied" in combined, "the real error, not a guess"
    assert "attempts made were" in combined


def test_silent_pip_failure_prints_a_command_to_run(checkout):
    """pip can fail with nothing on stderr; "output above" would be a lie."""
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "pip failed without printing anything" in source
    assert "pip install --editable" in source


def test_python_without_ensurepip_is_not_reported_as_missing(checkout, tmp_path):
    """A usable interpreter must not be rejected for lacking ensurepip.

    find_python used to import venv *and* ensurepip, so a Termux python was
    declared absent before setup could repair it.
    """
    fake = tmp_path / "bin"
    fake_python(
        fake,
        'case "$*" in\n'
        '  *"import sys, venv, ensurepip"*) exit 1 ;;\n'
        '  *"import sys, venv; sys.exit"*) exit 0 ;;\n'
        '  *"-m pip"*) exit 1 ;;\n'
        '  *"-m venv"*) exit 1 ;;\n'
        '  *--version*) echo "Python 3.14.10" ;;\n'
        '  *) exit 1 ;;\n'
        'esac\n',
    )
    result = run_launcher(checkout, "setup", path_prefix=fake)
    combined = result.stdout + result.stderr
    assert "no Python 3.9+" not in combined
    assert "3.14.10" in combined, "it found the interpreter and used it"
