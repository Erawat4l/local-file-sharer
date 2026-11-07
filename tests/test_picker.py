"""Picker model: listing, fuzzy search, selection parsing, folder analysis."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import unreadable_dir

from local_file_sharer.errors import PickerError
from local_file_sharer.picker import (
    analyze,
    collect_files,
    common_root,
    fuzzy_filter,
    fuzzy_score,
    icon_for,
    list_entries,
    parse_selection,
    resolve_user_path,
    shareable_reasons,
)


def test_list_entries_sorts_folders_first(share_tree):
    entries = list_entries(share_tree)
    names = [e.name for e in entries]
    assert names.index("empty") < names.index("clip.mp4")
    assert names.index("photos") < names.index("hello.txt")
    assert all(e.icon for e in entries)


def test_list_entries_files_only(share_tree):
    entries = list_entries(share_tree, files_only=True)
    assert all(not e.is_dir for e in entries)
    assert {e.name for e in entries} == {"hello.txt", "data.bin", "clip.mp4"}


def test_list_entries_hidden(tmp_path):
    (tmp_path / ".secret").write_text("x")
    (tmp_path / "visible.txt").write_text("y")
    assert [e.name for e in list_entries(tmp_path)] == ["visible.txt"]
    assert ".secret" in [e.name for e in list_entries(tmp_path, show_hidden=True)]


def test_list_entries_missing_folder(tmp_path):
    with pytest.raises(PickerError) as excinfo:
        list_entries(tmp_path / "nope")
    assert "not a folder" in str(excinfo.value)
    assert excinfo.value.all_hints()


def test_icons():
    # single-cell glyphs only: emoji are double width and mis-measure on phones
    assert icon_for(Path("movie.mkv"), icons=True) == ">"
    assert icon_for(Path("song.mp3"), icons=True) == "~"
    assert icon_for(Path("a.py"), icons=True) == ":"
    assert icon_for(Path("Unknown"), icons=True) == "-"
    for name in ("movie.mkv", "song.mp3", "a.py", "Unknown", "shots.png"):
        assert len(icon_for(Path(name), icons=True)) == 1
    assert icon_for(Path("movie.mkv"), icons=False) == ""


def test_fuzzy_score_ordering():
    assert fuzzy_score("hlo", "hello.txt") > 0
    assert fuzzy_score("zzz", "hello.txt") == 0
    assert fuzzy_score("hello", "hello.txt") > fuzzy_score("hlo", "hello.txt")
    assert fuzzy_score("", "anything") > 0


def test_fuzzy_filter(share_tree):
    entries = list_entries(share_tree)
    matches = fuzzy_filter(entries, "clp")
    assert matches
    assert matches[0].name == "clip.mp4"
    # glob syntax is supported
    assert fuzzy_filter(entries, "*.txt")[0].name == "hello.txt"
    assert fuzzy_filter(entries, "") == entries


@pytest.mark.parametrize(
    ("text", "count", "expected"),
    [
        ("6", 10, [5]),
        ("6,7,8", 10, [5, 6, 7]),
        ("6-8", 10, [5, 6, 7]),
        ("8-6", 10, [5, 6, 7]),
        ("1", 3, [0]),
        ("all", 4, [0, 1, 2, 3]),
        ("a", 2, [0, 1]),
        ("", 5, []),
        ("none", 5, []),
        ("3, 3", 5, [2]),
        ("1,2,3", 3, [0, 1, 2]),
    ],
)
def test_parse_selection(text, count, expected):
    assert parse_selection(text, count) == expected


@pytest.mark.parametrize("text", ["0", "11", "abc", "5-2-1", "1,,x"])
def test_parse_selection_errors(text):
    with pytest.raises(PickerError):
        parse_selection(text, 10)


def test_analyze_folder(share_tree):
    summary = analyze([share_tree, share_tree / "photos"])
    assert summary.files >= 5
    assert summary.folders >= 3
    assert summary.total_bytes > 250_000
    assert summary.shareable
    assert "file" in summary.headline()
    assert summary.as_dict()["shareable"] is True


def test_analyze_missing_path(tmp_path):
    summary = analyze([tmp_path / "ghost.txt"])
    assert summary.unreadable
    assert not summary.shareable
    assert summary.warnings


def test_analyze_empty_folder(share_tree):
    summary = analyze([share_tree / "empty"])
    assert any("empty" in warning for warning in summary.warnings)


def test_shareable_reasons(tmp_path):
    ok = tmp_path / "fine.txt"
    ok.write_text("x")
    assert shareable_reasons(ok) == []
    assert shareable_reasons(Path("/dev/null")) if sys.platform != "win32" else True


def test_collect_files(share_tree):
    files = collect_files([share_tree])
    names = {f.name for f in files}
    assert {"hello.txt", "data.bin", "clip.mp4", "one.jpg", "two.png", "three.txt"} <= names
    assert len(files) == 6


def test_common_root(share_tree):
    root = common_root([share_tree / "hello.txt", share_tree / "photos" / "one.jpg"])
    assert root == share_tree
    assert common_root([share_tree / "photos"]) == share_tree / "photos"


def test_resolve_user_path(share_tree):
    resolved = resolve_user_path("hello.txt", base=share_tree)
    assert resolved == share_tree / "hello.txt"
    quoted = resolve_user_path(f'"{share_tree / "hello.txt"}"')
    assert quoted == share_tree / "hello.txt"
    assert resolve_user_path("~/x").name == "x"


def test_going_up_into_a_locked_folder_keeps_the_picker_open(tmp_path):
    """`b` at /data/data/com.termux lands on /data/data, which Android hides.

    That raised out of the key loop and closed the picker, losing the user's
    selection and showing a full error panel for pressing "up".
    """
    from local_file_sharer.ui.picker_ui import PickerState, enter_dir

    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    (inner / "note.txt").write_text("hi")
    locked = tmp_path / "locked"
    locked.mkdir()

    state = PickerState(cwd=inner)
    state.reload()
    assert [e.name for e in state.visible()] == ["note.txt"]

    unreadable_dir(locked)
    try:
        moved = enter_dir(state, locked)
        assert moved is False, "a locked folder must not be entered"
        assert state.cwd == inner, "the picker must stay where it was"
        assert [e.name for e in state.visible()] == ["note.txt"], "listing must survive"
        assert "Permission denied" in state.message
    finally:
        locked.chmod(0o755)

    # and a readable folder still navigates normally
    assert enter_dir(state, outer) is True
    assert state.cwd == outer


def test_listing_does_not_scan_each_subfolder(tmp_path, monkeypatch):
    """Listing N folders must not list each of them again.

    Building a row used to probe every directory with a second ``scandir`` to
    decide whether it was readable. On Android storage that is a real read per
    folder per listing, and the picker lists on every navigation — it was most
    of the "the CLI is laggy" feeling.
    """
    import os

    root = tmp_path / "many"
    root.mkdir()
    for i in range(25):
        (root / f"dir{i}").mkdir()

    scans: list[str] = []
    real = os.scandir

    def counting_scandir(path="."):
        scans.append(str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", counting_scandir)
    entries = list_entries(root)
    monkeypatch.undo()

    assert len(entries) == 25
    # exactly one scan: the folder being listed
    assert len(scans) == 1, f"expected 1 scandir, got {len(scans)}: {scans[:3]}"


def test_unreadable_folder_is_still_flagged(tmp_path):
    """The red 'locked' cue survives the speed fix — it just costs no I/O."""
    import os

    root = tmp_path / "perm"
    locked = root / "locked"
    locked.mkdir(parents=True)
    (root / "open").mkdir()
    unreadable_dir(locked)
    try:
        if os.access(locked, os.R_OK):  # running as root: the mode is ignored
            pytest.skip("permissions are not enforced for this user")
        by_name = {e.name: e for e in list_entries(root)}
        assert by_name["locked"].readable is False
        assert by_name["open"].readable is True
    finally:
        locked.chmod(0o755)
