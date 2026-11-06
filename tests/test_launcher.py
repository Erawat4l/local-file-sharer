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
        # A real interpreter can import pyexpat/zlib/ssl; `run` now checks that
        # before doing anything else, so a fake that cannot would be testing a
        # scenario no device has. The heredoc probe reads stdin as `-`, which
        # these case statements do not model.
        body = script.replace(
            'case "$*" in',
            'case "$*" in\n'
            '  "-") exit 0 ;;',
            1,
        )
        target.write_text("#!/bin/sh\n" + body, encoding="utf-8")
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
        # coreutils must stay reachable: the script uses rm/tail/sed.
        PATH=f"{fake}:/usr/bin:/bin",
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


# ── a Python whose own C extensions cannot load ──────────────────────────────
def test_broken_pyexpat_is_named_before_pip_runs(checkout, tmp_path):
    """Seen on Termux + Python 3.14:

        ImportError: dlopen failed: cannot locate symbol
        "XML_GetCurrentLineNumber64" referenced by .../pyexpat...so

    The old code handed that to pip, which printed 64 lines of its own
    internals and then said "installing build dependencies did not run
    successfully". The cause — a stale libexpat — was never mentioned.
    """
    fake = tmp_path / "bin"
    fake_python(
        fake,
        'case "$*" in\n'
        '  *--version*) echo "Python 3.14.10" ;;\n'
        '  *) exit 0 ;;\n'
        'esac\n',
    )
    # A sitecustomize on PYTHONPATH is what the probe will actually run, so
    # plant the failure there via the fake interpreter's environment.
    (fake / "sitecustomize.py").write_text(
        "import sys\n"
        "class B:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name != 'pyexpat':\n"
        "            return None\n"
        "        raise ImportError('dlopen failed: cannot locate symbol \"XML_GetCurrentLineNumber64\"')\n"
        "sys.meta_path.insert(0, B())\n",
        encoding="utf-8",
    )
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "XML_GetCurrentLineNumber64" in source or "pyexpat" in source
    assert "libexpat" in source, "the package to fix must be named"
    assert "pkg list-installed" in source, "a foreign repo is a real cause"


def test_the_probe_covers_the_modules_pip_needs(checkout):
    """Not just pyexpat: pip also needs zlib, ssl and ctypes to run at all."""
    source = (checkout / "run").read_text(encoding="utf-8")
    for module in ("pyexpat", "zlib", "sqlite3", "ssl", "ctypes"):
        assert module in source, f"{module} must be probed"


def test_a_healthy_python_is_not_flagged(checkout):
    """The probe returning 0 means healthy. Inverting that broke every install."""
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "return 1   # imports fine" in source
    assert "return 0       # broken" in source


def test_install_links_this_checkout_and_warns_about_a_stale_one(checkout, tmp_path):
    """The reported symptom: `lfs` showed 5 menu entries, `./run` showed 12.

    A copy installed weeks ago was earlier on PATH. `run` never used it — it
    always runs .venv/bin/lfs — so the two disagreed, and the old one did not
    even know the `configure` command. `./run install` exists to fix that, and
    it must say when another lfs still shadows it.
    """
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "install|link" in source
    assert "your shell still runs a different lfs" in source


def test_install_points_the_winning_copy_at_this_checkout(checkout):
    """Linking into $PREFIX/bin changed nothing on Termux.

    `~/bin/lfs` came first on PATH, so every command still ran the old copy
    and every fix since looked like it had not shipped. Linking alone is not
    enough -- the directory the shell actually picks has to be pointed at this
    checkout too, or the user has to be told to remove the winner.
    """
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "pointed $first_dir/lfs at this copy" in source
    assert "your shell now runs this copy" in source


def test_a_stale_lfs_is_not_used_by_run(checkout):
    """`run` must exec its own venv, never whatever `lfs` resolves to."""
    source = (checkout / "run").read_text(encoding="utf-8")
    assert 'exec "$LFS"' in source
    assert 'exec lfs' not in source


def test_plain_run_creates_the_lfs_command(checkout):
    """`./run` built everything and never created `lfs`.

    A newcomer runs the first command in the README -- `./run` -- and gets a
    menu that says "`lfs <path>` shares straight away". They type `lfs` and
    fish answers "Unknown command: lfs". The linking lived only in the
    `install` branch, so the advertised command had never been created.
    """
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "link_command() {" in source
    dispatch = source[source.index("# ── 3. dispatch") :]
    no_arg = dispatch[dispatch.index('""') :]
    no_arg = no_arg[: no_arg.index("esac")]
    assert "link_command" in no_arg, "`./run` on its own must link the command"
    assert 'exec "$LFS"' in no_arg


def test_plain_run_is_quiet_once_the_command_exists(checkout):
    """It runs on every launch, so it must not narrate when nothing changed.

    But the first run has to speak, or the install still looks like a folder
    that happens to open a menu -- which is the bug above.
    """
    source = (checkout / "run").read_text(encoding="utf-8")
    assert "link_command quiet" in source
    assert 'command -v lfs 2>/dev/null' in source
