"""Picker model: listing, fuzzy search, selection parsing, folder analysis."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    assert icon_for(Path("movie.mkv"), icons=True) == "🎬"
    assert icon_for(Path("song.mp3"), icons=True) == "🎵"
    assert icon_for(Path("a.py"), icons=True) == "🐍"
    assert icon_for(Path("Unknown"), icons=True) == "📄"
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
