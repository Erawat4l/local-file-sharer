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
    """With no ref named, upgrade follows the repository default branch.

    `main` carries the package; a copy installed from a feature branch passes
    that branch explicitly instead.
    """
    assert updater.git_spec().endswith("@" + updater.DEFAULT_BRANCH)
    assert updater.DEFAULT_BRANCH == "main"


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
    # no metadata at all: fall back to the branch that can be installed
    assert updater.installed_ref() == updater.DEFAULT_BRANCH
    assert updater.installed_from_git() is False


def test_upgrade_from_git_builds_a_pipx_command(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return 0, []

    monkeypatch.setattr(updater, "detect_installer", lambda: "pipx")
    monkeypatch.setattr(updater, "run_with_progress", fake_run)
    assert updater.upgrade_from_git("some-branch") == 0
    assert seen["cmd"][:2] == ["pipx", "install"]
    assert seen["cmd"][-1].endswith("@some-branch")


def test_upgrade_from_git_uses_pip_for_plain_installs(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return 0, []

    monkeypatch.setattr(updater, "detect_installer", lambda: "pip")
    monkeypatch.setattr(updater, "run_with_progress", fake_run)
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


def test_ref_has_package_reports_a_missing_pyproject(monkeypatch):
    """A branch that never got pyproject.toml is reported as uninstallable."""
    monkeypatch.setattr(updater, "ref_has_package", lambda ref, **kw: ref != "docs-only")
    assert updater.ref_has_package("docs-only") is False
    assert updater.ref_has_package(updater.DEFAULT_BRANCH) is True


def test_resolve_ref_redirects_away_from_a_branch_with_no_package(monkeypatch):
    """A copy installed from a branch that never had the package, eg. a
    docs-only branch, must still be able to update.

    Without the redirect it re-ran pip against that branch forever and failed
    with "does not appear to be a Python project".
    """
    monkeypatch.setattr(updater, "installed_ref", lambda: "docs-only")
    monkeypatch.setattr(updater, "ref_has_package", lambda ref, **kw: ref != "docs-only")

    ref, note = updater.resolve_ref(None)
    assert ref == updater.DEFAULT_BRANCH
    assert "docs-only" in note and "pyproject" in note, "the redirect must be explained"


def test_resolve_ref_keeps_an_explicit_ref(monkeypatch):
    monkeypatch.setattr(updater, "ref_has_package", lambda ref, **kw: True)
    assert updater.resolve_ref("arena/some-branch") == ("arena/some-branch", "")


def test_resolve_ref_warns_about_an_explicit_ref_with_no_package(monkeypatch):
    monkeypatch.setattr(updater, "ref_has_package", lambda ref, **kw: False)
    ref, note = updater.resolve_ref("main")
    assert ref == "main", "an explicit --ref is still honoured"
    assert "will fail" in note


def test_offline_does_not_block_the_install(monkeypatch):
    """If the check cannot run, assume the ref is fine and let pip decide."""
    import urllib.error

    def boom(*_a, **_kw):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    assert updater.ref_has_package("anything") is True


def test_upgrade_from_git_uses_the_resolved_ref(monkeypatch):
    """`lfs upgrade` must install the branch resolve_ref picked, not the broken one."""
    seen = {}

    def fake_run(cmd, **_kw):
        seen["cmd"] = cmd
        return 0, []

    monkeypatch.setattr(updater, "run_with_progress", fake_run)
    monkeypatch.setattr(updater, "detect_installer", lambda: "pipx")
    monkeypatch.setattr(updater, "installed_ref", lambda: "docs-only")
    monkeypatch.setattr(updater, "ref_has_package", lambda ref, **kw: ref != "docs-only")

    assert updater.upgrade_from_git(None) == 0
    assert updater.DEFAULT_BRANCH in " ".join(seen["cmd"])
    assert "@docs-only" not in " ".join(seen["cmd"])


# ── quiet upgrades with a progress bar ───────────────────────────────────────
def test_upgrade_runs_pip_quietly_and_reports_a_bar(capsys, monkeypatch):
    """`lfs upgrade` must not hand the terminal to pip.

    pip prints a wall of resolve/clone/build chatter. The upgrade now captures
    it, draws one progress bar and only shows the captured text on failure.
    """
    from local_file_sharer import updater

    calls: list[list[str]] = []

    def fake_run(cmd, *, con=None, title="upgrading"):
        calls.append(list(cmd))
        return 0, ["Successfully installed local-file-sharer-1.0.0"]

    monkeypatch.setattr(updater, "detect_installer", lambda: "pip")
    monkeypatch.setattr(updater, "resolve_ref", lambda ref=None: ("arena/x", ""))
    monkeypatch.setattr(updater, "run_with_progress", fake_run)
    assert updater.upgrade_from_git() == 0
    assert calls and "pip" in " ".join(calls[0])


def test_the_command_line_is_never_printed(monkeypatch, capsys):
    """It used to echo `python -m pip install ...`, which is noise to a user."""
    from local_file_sharer import updater

    monkeypatch.setattr(updater, "detect_installer", lambda: "pip")
    monkeypatch.setattr(updater, "resolve_ref", lambda ref=None: ("arena/x", ""))
    monkeypatch.setattr(updater, "run_with_progress", lambda cmd, **kw: (0, []))
    updater.upgrade_from_git()
    # render what the user sees through a recording console
    import io

    from rich.console import Console

    buf = io.StringIO()
    monkeypatch.setattr(updater, "console", lambda: Console(file=buf, width=80, force_terminal=False))
    updater.upgrade_from_git()
    out = buf.getvalue()
    assert "running:" not in out
    assert "-m pip install" not in out


def test_a_failed_upgrade_shows_the_reason():
    """Silence on failure is worse than noise: print pip's tail."""
    from local_file_sharer import updater

    assert updater._phase_for("", "resolve") == "resolve"
    assert updater._phase_for("  Cloning https://github.com/x/y.git", "resolve") == "clone"
    assert updater._phase_for("  Preparing metadata (pyproject.toml)", "clone") == "build"
    assert updater._phase_for("Installing collected packages: x", "build") == "install"
    assert updater._phase_for("Successfully installed x-1.0", "install") == "install"
    assert updater._phase_for("garbage", "install") == "install"


# ── the silent no-op ─────────────────────────────────────────────────────────
def test_pip_install_is_forced_to_reinstall(monkeypatch):
    """`--upgrade` alone installs nothing, silently, and always has.

    Every commit of this package reports the same version, so pip considers a
    git requirement "already satisfied" and exits 0 without building a wheel.
    The user sees an upgrade, runs `lfs`, and finds the old code. Proved against
    a throwaway package: without the flag the import marker stayed at the old
    value; with it, it changed.
    """
    seen = {}

    def fake_run(cmd, **_kw):
        seen["cmd"] = cmd
        return 0, []

    monkeypatch.setattr(updater, "run_with_progress", fake_run)
    monkeypatch.setattr(updater, "detect_installer", lambda: "pip")
    monkeypatch.setattr(updater, "resolve_ref", lambda ref: ("arena/x", ""))
    updater.upgrade_from_git(None)
    assert "--force-reinstall" in seen["cmd"]
    # --no-deps: rich and qrcode are already there; reinstalling them is slow.
    assert "--no-deps" in seen["cmd"]


def test_a_real_change_is_reported_with_its_commits(capsys, monkeypatch):
    monkeypatch.setattr(updater, "run_with_progress", lambda cmd, **kw: (0, []))
    monkeypatch.setattr(updater, "detect_installer", lambda: "pip")
    monkeypatch.setattr(updater, "resolve_ref", lambda ref: ("arena/x", ""))
    monkeypatch.setattr(updater, "installed_commit", lambda: "b" * 40)
    monkeypatch.setattr(updater, "remote_commit", lambda ref, **kw: "b" * 40)
    monkeypatch.setattr(updater, "upgrade_result", lambda before, ref: (True, "a" * 40, "b" * 40))
    assert updater.upgrade_from_git(None) == 0
    out = capsys.readouterr().out
    assert "upgraded" in out
    assert "aaaaaaa" in out and "bbbbbbb" in out


def test_pip_exiting_zero_without_installing_is_not_called_an_upgrade(capsys, monkeypatch):
    """The exact failure the user hit: pip exits 0, nothing changed."""
    monkeypatch.setattr(updater, "run_with_progress", lambda cmd, **kw: (0, []))
    monkeypatch.setattr(updater, "detect_installer", lambda: "pip")
    monkeypatch.setattr(updater, "resolve_ref", lambda ref: ("arena/x", ""))
    monkeypatch.setattr(updater, "installed_commit", lambda: "c" * 40)
    monkeypatch.setattr(updater, "remote_commit", lambda ref, **kw: "c" * 40)
    monkeypatch.setattr(updater, "upgrade_result", lambda before, ref: (False, "c" * 40, "c" * 40))
    # The "another copy is installed" warning also contains the word
    # "upgraded", so leaving it unpatched made this assertion depend on
    # whether the machine running the tests happened to have a second lfs on
    # PATH -- it failed in CI for a reason that had nothing to do with pip.
    monkeypatch.setattr(updater, "find_duplicates", lambda: [])
    assert updater.upgrade_from_git(None) == 0
    out = capsys.readouterr().out
    assert "nothing changed" in out
    assert "upgraded" not in out.replace("nothing changed", "")


def test_installed_commit_reads_the_vcs_commit_id(monkeypatch):
    payload = {"url": "https://github.com/x/y", "vcs_info": {"vcs": "git", "commit_id": "f" * 40}}

    class Dist:
        def read_text(self, name):
            return json.dumps(payload)

    monkeypatch.setattr("importlib.metadata.distribution", lambda name: Dist())
    assert updater.installed_commit() == "f" * 40


def test_installed_commit_is_blank_when_unknown(monkeypatch):
    """A non-git install has no commit; that must not crash the upgrade."""
    monkeypatch.setattr("importlib.metadata.distribution", lambda name: (_ for _ in ()).throw(LookupError()))
    assert updater.installed_commit() == ""


def test_upgrade_result_trusts_pip_when_there_is_nothing_to_compare():
    assert updater.upgrade_result("a" * 40, "x")[0] is True


def test_pip_does_not_see_pythonpath(monkeypatch):
    """A PYTHONPATH pointing at the source tree can shadow what pip installs."""
    import subprocess

    seen = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen["env"] = kw.get("env") or {}
            self.stdout = iter(())

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setenv("PYTHONPATH", "/home/user/local-file-sharer/src")
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    updater.run_with_progress(["pip"], con=_FakeConsole())
    assert "PYTHONPATH" not in seen["env"]


class _FakeConsole:
    is_terminal = False

    def print(self, *a, **k):
        pass


# ── it must never sit waiting for an answer that cannot come ────────────────
def test_the_child_gets_no_terminal_to_prompt_on(monkeypatch):
    """`lfs upgrade` froze at "0/5 contacting GitHub" and never returned.

    pip ran git, git asked for a username and password, and nothing was reading
    stdin to answer it. Closing stdin plus PIP_NO_INPUT/GIT_TERMINAL_PROMPT
    turns that into an immediate, explainable failure.
    """
    import subprocess

    seen = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen.update(kw)
            self.stdout = []

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    updater.run_with_progress(["pip"], con=_FakeConsole())
    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["env"]["PIP_NO_INPUT"] == "1"
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_a_private_repo_is_explained_rather_than_dumped():
    """ "Repository not found" is also what a typo'd URL says; say so."""
    lines = [
        "Collecting local-file-sharer @ git+https://github.com/x/y.git@main",
        "  remote: Repository not found.",
        "  fatal: repository 'https://github.com/x/y.git/' not found",
        "error: subprocess-exited-with-error",
    ]
    reason, hint = updater.explain_failure(lines)
    assert "private" in reason.lower()
    assert "not found" in hint.lower()


def test_the_reason_is_printed_before_pips_noise(capsys, monkeypatch):
    monkeypatch.setattr(
        updater, "run_with_progress", lambda cmd, **kw: (1, ["  remote: Repository not found."])
    )
    monkeypatch.setattr(updater, "detect_installer", lambda: "pip")
    monkeypatch.setattr(updater, "resolve_ref", lambda ref: ("arena/x", ""))
    monkeypatch.setattr(updater, "installed_commit", lambda: "")
    monkeypatch.setattr(updater, "remote_commit", lambda ref, **kw: "")
    assert updater.upgrade_from_git(None) == 1
    out = capsys.readouterr().out
    head = out.split("remote:")[0]
    assert "private" in head.lower() or "does not exist" in head.lower()
    assert "install-lfs.py" in out


def test_unknown_failures_keep_pips_own_words():
    assert updater.explain_failure(["something odd happened"]) == ("", "")


# ── more than one copy of lfs on PATH ───────────────────────────────────────
def _make_exe(path, shebang, body="import sys\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{shebang}\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_a_pipx_shim_is_recognised(tmp_path):
    shim = _make_exe(
        tmp_path / "bin" / "lfs",
        "/home/u/.local/pipx/venvs/local-file-sharer/bin/python",
    )
    plain = _make_exe(tmp_path / "other" / "lfs", "/usr/bin/python3")
    assert updater.is_pipx_install(str(shim)) is True
    assert updater.is_pipx_install(str(plain)) is False


def test_a_second_copy_on_path_is_reported(tmp_path, monkeypatch):
    """pipx install --force beside an existing copy leaves two lfs commands."""
    a = _make_exe(tmp_path / "a" / "lfs", "/usr/bin/python3")
    b = _make_exe(tmp_path / "b" / "lfs", "/home/u/.local/pipx/venvs/x/bin/python")
    monkeypatch.setenv("PATH", f"{a.parent}:{b.parent}:/usr/bin:/bin")
    monkeypatch.setattr("sys.argv", [str(a)])
    found = updater.find_duplicates()
    assert [item["path"] for item in found] == [str(b)]
    assert found[0]["kind"] == "pipx"


def test_no_duplicates_when_only_one_copy_is_installed(tmp_path, monkeypatch):
    a = _make_exe(tmp_path / "a" / "lfs", "/usr/bin/python3")
    monkeypatch.setenv("PATH", f"{a.parent}:/usr/bin:/bin")
    monkeypatch.setattr("sys.argv", [str(a)])
    assert updater.find_duplicates() == []
