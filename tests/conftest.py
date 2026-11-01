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


@pytest.fixture
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
