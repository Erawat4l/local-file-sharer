"""`lfs upgrade`: the package is not on PyPI and has no releases, so the only
useful update path is the repository itself."""

from __future__ import annotations

import json

import pytest

from local_file_sharer import updater


def test_git_spec_names_the_repository_and_ref():
    spec = updater.git_spec("arena/example")
    assert spec.startswith("local-file-sharer @ git+https://github.com/")
    assert spec.endswith("@arena/example")


def test_git_spec_defaults_to_main():
    assert updater.git_spec().endswith("@main")


def test_installed_ref_reads_direct_url(monkeypatch):
    payload = {
        "url": "https://github.com/Erawat4l/local-file-sharer.git",
        "vcs_info": {"vcs": "git", "requested_revision": "arena/d7f4713b-local-file-sharer"},
    }

    class Dist:
        def read_text(self, name):
            return json.dumps(payload) if name == "direct_url.json" else None

    monkeypatch.setattr("importlib.metadata.distribution", lambda _name: Dist())
    assert updater.installed_ref() == "arena/d7f4713b-local-file-sharer"
    assert updater.installed_from_git() is True


def test_installed_ref_falls_back_to_main(monkeypatch):
    def boom(_name):
        raise PackageNotFoundError

    from importlib.metadata import PackageNotFoundError

    monkeypatch.setattr("importlib.metadata.distribution", boom)
    assert updater.installed_ref() == "main"
    assert updater.installed_from_git() is False


def test_upgrade_from_git_builds_a_pipx_command(monkeypatch):
    seen = {}

    class Result:
        returncode = 0

    def fake_run(cmd, check=False):
        seen["cmd"] = cmd
        return Result()

    monkeypatch.setattr(updater, "detect_installer", lambda: "pipx")
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    assert updater.upgrade_from_git("some-branch") == 0
    assert seen["cmd"][:2] == ["pipx", "install"]
    assert seen["cmd"][-1].endswith("@some-branch")


def test_upgrade_from_git_uses_pip_for_plain_installs(monkeypatch):
    seen = {}

    class Result:
        returncode = 0

    monkeypatch.setattr(updater, "detect_installer", lambda: "pip")
    monkeypatch.setattr(updater.subprocess, "run", lambda cmd, check=False: seen.update(cmd=cmd) or Result())
    assert updater.upgrade_from_git("main") == 0
    assert seen["cmd"][1:4] == ["-m", "pip", "install"]
    assert "--upgrade" in seen["cmd"]


def test_upgrade_from_git_refuses_to_overwrite_a_standalone_exe(monkeypatch):
    monkeypatch.setattr(updater, "detect_installer", lambda: "exe")
    assert updater.upgrade_from_git("main") == 1


@pytest.mark.parametrize("ref", [None, "main", "arena/x"])
def test_every_spec_is_a_valid_requirement(ref):
    spec = updater.git_spec(ref)
    assert " " in spec and "@" in spec and spec.count("git+") == 1
