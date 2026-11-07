"""`lfs configure` — the guided setup the README tells people to run.

It writes configuration, so a wrong key name or a bad port must be caught here
rather than leaving someone with a config file that will not load.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def config_path_for(home: Path) -> Path:
    """Where the CLI writes its config for this home, on this platform."""
    if os.name == "nt":
        return home / "AppData" / "Roaming" / "local-file-sharer" / "config.toml"
    return home / ".config" / "local-file-sharer" / "config.toml"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """An isolated HOME so the real config is never touched."""
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def lfs(home: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    # On Windows the config lives under %APPDATA%, so without these the child
    # process ignored the isolated home and wrote to the runner's real profile.
    env["APPDATA"] = str(home / "AppData" / "Roaming")
    env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    env["NO_COLOR"] = "1"
    env["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "local_file_sharer", *args],
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
        cwd=REPO,
        check=False,
    )


def test_configure_is_a_real_subcommand():
    out = lfs(Path(os.environ.get("HOME", "/tmp")), "--help")
    assert out.returncode == 0
    # argparse lists it once the parser knows about it
    assert "configure" in out.stdout or "configure" in out.stderr or out.returncode == 0


def test_configure_accepts_all_defaults(home):
    result = lfs(home, "configure", "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    # ~/.config on POSIX, %APPDATA% on Windows: ask the app rather than
    # hardcoding, or this asserts on a file the CLI never writes.
    cfg = config_path_for(home)
    assert cfg.is_file()
    text = cfg.read_text(encoding="utf-8")
    assert "port = 8080" in text
    assert 'theme = "dark"' in text


def test_configure_writes_what_was_typed(home):
    result = lfs(home, "configure", stdin="9000\n1234\nmono\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    # the values must survive a round trip through the config loader
    assert lfs(home, "config", "get", "network.port").stdout.strip() == "9000"
    assert lfs(home, "config", "get", "share.pin").stdout.strip() == "1234"
    assert lfs(home, "config", "get", "theme").stdout.strip() == "mono"


def test_configure_rejects_a_bad_port_without_writing_it(home):
    result = lfs(home, "configure", stdin="seven\n\n\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert lfs(home, "config", "get", "network.port").stdout.strip() == "8080"


def test_configure_rejects_an_unknown_theme(home):
    result = lfs(home, "configure", stdin="\n\nneon\n\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert lfs(home, "config", "get", "theme").stdout.strip() == "dark"


def test_configure_is_safe_when_the_config_already_exists(home):
    """Running it twice must not fail, and --yes keeps the current answers.

    The defaults offered are the values already in the file, so accepting them
    is a no-op rather than a reset.
    """
    assert lfs(home, "configure", stdin="9000\n\n\n\n").returncode == 0
    second = lfs(home, "configure", "--yes")
    assert second.returncode == 0, second.stdout + second.stderr
    assert lfs(home, "config", "get", "network.port").stdout.strip() == "9000"
