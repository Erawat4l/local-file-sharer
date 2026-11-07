"""`lfs uninstall` — the half of the lifecycle that has to be most careful.

Every test here is about *not* doing damage: not deleting a directory that is
not ours, not leaving a dangling symlink, not removing the tree the process is
running from.
"""

from __future__ import annotations

from pathlib import Path

from conftest import lfs_exe_name, make_symlink, on_path
from local_file_sharer import uninstall as un
from local_file_sharer.uninstall import _is_checkout, make_plan


def _fake_checkout(root: Path) -> Path:
    """A directory that looks exactly like what install-lfs.py unpacks."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "src" / "local_file_sharer").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text('[project]\nname = "local-file-sharer"\n', encoding="utf-8")
    (root / "run").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "setup").write_text("#!/bin/sh\n", encoding="utf-8")
    venv = root / ".venv" / "bin"
    venv.mkdir(parents=True, exist_ok=True)
    launcher = venv / lfs_exe_name()
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    # A real installed command is executable; find_on_path() skips files that
    # are not, which is correct for the product and would silently make this
    # fixture invisible.
    launcher.chmod(0o755)
    return root


# ── the dangerous test: what may be deleted ─────────────────────────────────
def test_only_a_real_checkout_may_be_deleted(tmp_path):
    """Deleting a directory tree is the one irreversible thing here.

    "Contains a file called run" is not evidence -- plenty of projects have
    one -- so the test is the package itself: a pyproject naming this project
    and the src/local_file_sharer directory.
    """
    real = _fake_checkout(tmp_path / "real")
    assert _is_checkout(real) is True

    unrelated = tmp_path / "someone-elses"
    unrelated.mkdir()
    (unrelated / "run").write_text("", encoding="utf-8")
    (unrelated / "setup").write_text("", encoding="utf-8")
    assert _is_checkout(unrelated) is False, "a folder with a run script is not ours"

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "holiday.zip").write_text("x", encoding="utf-8")
    assert _is_checkout(downloads) is False

    empty = tmp_path / "empty"
    empty.mkdir()
    assert _is_checkout(empty) is False


def test_a_project_that_is_merely_named_similar_is_not_ours(tmp_path):
    """pyproject names the *package*, not a directory that merely exists."""
    decoy = tmp_path / "decoy"
    (decoy / "src" / "local_file_sharer").mkdir(parents=True)
    (decoy / "pyproject.toml").write_text('[project]\nname = "something-else"\n', encoding="utf-8")
    assert _is_checkout(decoy) is False


# ── the plan ────────────────────────────────────────────────────────────────
def test_the_checkout_is_deferred_not_deleted_in_process():
    """rmtree of the running tree unloads the interpreter's own imports.

    The first version of this command deleted its checkout, then tried to
    print the result, and died with ModuleNotFoundError: No module named
    'local_file_sharer.doctor' -- half removed and no explanation.
    """
    source = Path(un.__file__).read_text(encoding="utf-8")
    assert "after this exits" in source
    assert "_defer_removal" in source
    # and the deferral must actually detach, or it dies with the parent
    assert "start_new_session=True" in source


def test_a_symlink_on_path_is_not_mistaken_for_the_running_copy(tmp_path, monkeypatch):
    """find_duplicates() skips it -- it resolves to the running binary.

    But it is a *separate file* that outlives the venv, so it has to be
    removed or the shell is left with a dangling link that answers "no such
    file" for every command.
    """
    fake_home = tmp_path / "home"
    checkout = _fake_checkout(fake_home / "local-file-sharer")
    binary = checkout / ".venv" / "bin" / lfs_exe_name()
    bindir = fake_home / ".local" / "bin"
    bindir.mkdir(parents=True)
    link = bindir / lfs_exe_name()
    make_symlink(link, binary)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("PATH", on_path(bindir))
    monkeypatch.setattr(un, "find_duplicates", list)
    monkeypatch.setattr(un, "is_pipx_install", lambda _p: False)
    monkeypatch.setattr(un, "_own_tree", lambda: checkout)
    monkeypatch.setattr(un.sys, "argv", [str(binary)])

    plan = make_plan()
    paths = {step.path for step in plan.steps}
    assert str(link) in paths, "the symlink the shell runs must be removed"


def test_pipx_copies_are_delegated_not_deleted(tmp_path, monkeypatch):
    """Deleting a pipx shim by hand leaves the venv behind with no launcher."""
    monkeypatch.setattr(un, "_own_tree", lambda: None)
    monkeypatch.setattr(un.sys, "argv", ["/usr/bin/lfs"])
    monkeypatch.setattr(un, "find_duplicates", lambda: [{"name": "lfs", "path": "/x/lfs", "kind": "pipx"}])
    monkeypatch.setattr(un, "is_pipx_install", lambda _p: True)

    plan = make_plan()
    assert plan.commands == ["pipx uninstall local-file-sharer"]
    assert not any(step.path == "/x/lfs" for step in plan.steps)


def test_history_is_kept_unless_asked(tmp_path, monkeypatch):
    """A reinstall should not silently destroy the share history."""
    monkeypatch.setattr(un, "_own_tree", lambda: None)
    monkeypatch.setattr(un.sys, "argv", [])
    monkeypatch.setattr(un, "find_duplicates", list)
    monkeypatch.setattr(un.platforms, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(un.platforms, "config_dir", lambda: tmp_path / "config")

    kept = make_plan()
    assert not any("cache" in step.what for step in kept.steps)
    assert any("cache" in note for note in kept.kept)

    purged = make_plan(purge_data=True)
    assert any("cache" in step.what for step in purged.steps)


def test_keep_checkout_removes_the_command_but_leaves_the_folder(tmp_path, monkeypatch):
    checkout = _fake_checkout(tmp_path / "co")
    monkeypatch.setattr(un, "_own_tree", lambda: checkout)
    monkeypatch.setattr(un.sys, "argv", [str(checkout / ".venv" / "bin" / lfs_exe_name())])
    monkeypatch.setattr(un, "find_duplicates", list)
    monkeypatch.setattr(un.platforms, "config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(un.platforms, "cache_dir", lambda: tmp_path / "cache")

    plan = make_plan(keep_checkout=True)
    assert not any(step.path == str(checkout) for step in plan.steps)
    assert any("--keep-checkout" in note for note in plan.kept)


# ── the CLI ─────────────────────────────────────────────────────────────────
def test_uninstall_is_a_real_subcommand():
    """It was listed among the recognised words but had no implementation, so
    `lfs uninstall` answered with the \"Unknown command\" panel."""
    from local_file_sharer.cli import build_command_parsers, resolve_subcommand

    assert resolve_subcommand("uninstall") == "uninstall"
    assert "uninstall" in build_command_parsers()


def test_uninstall_has_the_flags_it_documents():
    from local_file_sharer.cli import build_command_parsers

    parser = build_command_parsers()["uninstall"]
    args = parser.parse_args(["--dry-run", "--purge-data", "--keep-checkout", "--yes"])
    assert args.dry_run and args.purge_data and args.keep_checkout and args.yes


def test_a_launcher_link_whose_target_is_gone_is_still_removed(tmp_path, monkeypatch):
    """The state an interrupted upgrade leaves: a link into a deleted venv.

    Path.resolve() on a dangling link reports the missing target, and on some
    Python versions raises instead; either way the link is a file on PATH that
    has to go, or every `lfs` answers "no such file or directory".
    """
    fake_home = tmp_path / "home"
    checkout = _fake_checkout(fake_home / "local-file-sharer")
    bindir = fake_home / ".local" / "bin"
    bindir.mkdir(parents=True)
    link = bindir / lfs_exe_name()
    make_symlink(link, checkout / ".venv" / "bin" / "already-deleted")

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("PATH", on_path(bindir))
    monkeypatch.setattr(un, "find_duplicates", list)
    monkeypatch.setattr(un, "is_pipx_install", lambda _p: False)
    monkeypatch.setattr(un, "_own_tree", lambda: checkout)
    monkeypatch.setattr(un.sys, "argv", [str(checkout / ".venv" / "bin" / lfs_exe_name())])

    paths = {step.path for step in make_plan().steps}
    assert str(link) in paths, "a dangling launcher leaves the shell pointing at nothing"
