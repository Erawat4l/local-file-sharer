"""Formatting helpers: sizes, speeds, durations, text fitting, progress bars."""

from __future__ import annotations

import math

import pytest

from local_file_sharer.fmt import (
    bar,
    cell_len,
    clock,
    fit,
    human_count,
    human_size,
    human_speed,
    human_time,
    pad,
    percent,
    strip_ansi,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (1, "1 B"),
        (1023, "1023 B"),
        (1024, "1 KB"),
        (1536, "1.5 KB"),
        (1024**2, "1 MB"),
        (1024**3, "1 GB"),
        (1.5 * 1024**3, "1.5 GB"),
        (1024**4, "1 TB"),
        (None, "—"),
    ],
)
def test_human_size(value, expected):
    assert human_size(value) == expected


def test_human_size_negative_and_garbage():
    assert human_size(-2048) == "-2 KB"
    assert human_size("nope") == "—"
    assert human_size(math.nan) == "—"
    assert human_size(math.inf) == "—"


def test_human_speed():
    assert human_speed(0) == "0 B/s"
    assert human_speed(None) == "—"
    assert human_speed(1024 * 1024) == "1 MB/s"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (1, "1s"),
        (59, "59s"),
        (60, "1m 00s"),
        (3599, "59m 59s"),
        (3600, "1h 00m"),
        (86400 + 3660, "1d 01h"),
        (None, "—"),
        (-5, "—"),
    ],
)
def test_human_time(seconds, expected):
    assert human_time(seconds) == expected


def test_human_count():
    assert human_count(1, "file") == "1 file"
    assert human_count(2, "file") == "2 files"
    assert human_count(0, "device", "devices") == "0 devices"


def test_cell_len_and_fit():
    assert cell_len("abc") == 3
    assert cell_len("日本") == 4  # wide characters count double
    assert cell_len("\x1b[31mred\x1b[0m") == 3
    assert strip_ansi("\x1b[1mbold\x1b[0m") == "bold"
    assert fit("hello", 10) == "hello"
    assert fit("hello world", 8) == "hello w…"
    assert fit("hello", 0) == ""
    assert "…" in fit("a/very/long/path/to/some/file.txt", 20, middle=True)
    assert cell_len(fit("日本語のテキストです", 5)) <= 5


def test_pad():
    assert pad("ab", 4) == "ab  "
    assert pad("ab", 4, align="right") == "  ab"
    assert pad("ab", 1) == "…"


def test_percent_and_bar():
    assert percent(50, 100) == 50.0
    assert percent(1, 0) == 0.0
    assert percent(200, 100) == 100.0
    assert bar(0, 100, 10) == "░" * 10
    assert bar(100, 100, 10) == "█" * 10
    assert len(bar(50, 100, 10)) == 10


def test_clock():
    assert clock(0) == "0:00"
    assert clock(65) == "1:05"
    assert clock(3661) == "1:01:01"
