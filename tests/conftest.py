"""Shared pytest fixtures: isolated config/state dirs and a tmp share tree."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Never touch the real ~/.config or clipboard during tests."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("NO_COLOR", "1")
    for key in list(os.environ):
        if key.startswith("LOCAL_FILE_SHARER_"):
            monkeypatch.delenv(key, raising=False)
    yield home


@pytest.fixture
def share_tree(tmp_path: Path) -> Path:
    """A small tree with files, nested folders and an empty folder."""
    root = tmp_path / "share"
    root.mkdir()
    (root / "hello.txt").write_text("hello world\n" * 100, encoding="utf-8")
    (root / "data.bin").write_bytes(bytes(range(256)) * 400)
    (root / "clip.mp4").write_bytes(b"\0" * 250_000)
    photos = root / "photos"
    photos.mkdir()
    (photos / "one.jpg").write_bytes(b"\xff\xd8" + b"x" * 5000)
    (photos / "two.png").write_bytes(b"\x89PNG" + b"y" * 3000)
    nested = photos / "2024"
    nested.mkdir()
    (nested / "three.txt").write_text("deep\n" * 50, encoding="utf-8")
    (root / "empty").mkdir()
    return root


def unreadable_dir(path: Path) -> None:
    """chmod a directory so it cannot be listed, or skip where that does nothing.

    Windows has no equivalent: ``chmod(0o000)`` leaves the folder perfectly
    readable, so tests that assert "this folder is not accessible" pass their
    setup and fail their assertion there. Skip instead of pretending.
    """
    path.chmod(0o000)
    try:
        next(iter(path.iterdir()), None)
    except (PermissionError, OSError):
        return
    path.chmod(0o755)
    pytest.skip("this platform ignores directory permissions (needs a POSIX mode)")


def on_path(*dirs: Path | str) -> str:
    """A PATH value for *dirs*, joined the way this platform joins it.

    Tests built PATH with ":" and it silently became one long directory name
    on Windows, so ``which()`` found nothing and the test failed on an empty
    list rather than on the behaviour it was checking.
    """
    return os.pathsep.join(str(d) for d in dirs)


def lfs_exe_name() -> str:
    """What the installed command is called here: ``lfs`` or ``lfs.exe``.

    ``shutil.which("lfs")`` on Windows only finds ``lfs.exe`` because it goes
    through PATHEXT, so a POSIX-shaped fixture is invisible to it.
    """
    return "lfs.exe" if os.name == "nt" else "lfs"


def make_symlink(link: Path, target: Path) -> None:
    """Create a symlink, or skip the test where the platform forbids it.

    Windows needs Developer Mode or admin rights for symlinks, so these tests
    raised ``OSError: [WinError 1314]`` in CI rather than skipping -- one of the
    reasons the whole Windows matrix was red.
    """
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform specific
        pytest.skip(f"this platform cannot create symlinks: {exc}")


@pytest.fixture
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
