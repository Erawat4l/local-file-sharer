"""Finding the installed command on PATH, on both separator and extension rules.

`lfs doctor` reports duplicate copies and `lfs uninstall` removes launchers, and
both walk PATH themselves. A walk that looks only for a file literally called
`lfs` finds nothing on Windows, where the command is `lfs.exe` -- so doctor
said "one copy" while a second one sat there, and uninstall left the shell
pointing at a deleted venv.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from local_file_sharer import platforms


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.skipif(os.name == "nt", reason="constructs a POSIX PATH")
def test_a_plain_file_on_path_is_found(tmp_path, monkeypatch):
    exe = _exe(tmp_path / "bin" / "lfs")
    monkeypatch.setenv("PATH", os.pathsep.join([str(exe.parent), "/usr/bin"]))
    assert platforms.find_on_path("lfs") == [str(exe)]


@pytest.mark.skipif(os.name == "nt", reason="constructs a POSIX PATH")
def test_a_file_that_is_not_executable_is_not_a_command(tmp_path, monkeypatch):
    """A stray file called `lfs` that cannot be run is not a second copy."""
    stray = tmp_path / "bin" / "lfs"
    stray.parent.mkdir(parents=True)
    stray.write_text("not a program\n", encoding="utf-8")
    stray.chmod(0o644)
    monkeypatch.setenv("PATH", str(stray.parent))
    assert platforms.find_on_path("lfs") == []


def test_every_directory_is_searched_and_duplicates_are_reported(tmp_path, monkeypatch):
    """A second copy is the whole reason this function exists."""
    first = _exe(tmp_path / "a" / "lfs")
    second = _exe(tmp_path / "b" / "lfs")
    monkeypatch.setenv("PATH", os.pathsep.join([str(first.parent), str(second.parent)]))
    assert platforms.find_on_path("lfs") == [str(first), str(second)]


def test_only_the_first_match_in_a_directory_counts(tmp_path, monkeypatch):
    """The shell runs one of them; listing both would report a copy that never runs."""
    winner = _exe(tmp_path / "bin" / "lfs")
    _exe(tmp_path / "bin" / "lfs.exe")
    monkeypatch.setenv("PATH", str(winner.parent))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    assert platforms.find_on_path("lfs") == [str(winner)]


def test_windows_finds_the_dot_exe(monkeypatch, tmp_path):
    """The bug: PATH is walked by hand, so PATHEXT has to be applied by hand."""
    exe = tmp_path / "bin" / "lfs.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(exe.parent))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setattr(platforms, "WINDOWS", True)
    assert platforms.find_on_path("lfs") == [str(exe)]


def test_a_name_that_already_has_an_extension_is_left_alone(tmp_path):
    """`lfs.exe` must not become `lfs.exe.EXE`."""
    assert platforms.path_candidates("lfs.exe", str(tmp_path)) == [tmp_path / "lfs.exe"]


def test_an_empty_path_entry_is_skipped(monkeypatch, tmp_path):
    """An empty entry means "current directory", not "look in the cwd for a copy"."""
    monkeypatch.setenv("PATH", f"{os.pathsep}{tmp_path}")
    assert platforms.find_on_path("lfs") == []
