"""The CI annotation reporter; without it a red run names no reason."""

from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("report_failures", ROOT / "tools" / "report_failures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_one_annotation_per_failure_with_its_reason():
    text = (
        "..........\n"
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_config.py::test_dump_round_trip - UnicodeDecodeError: 'charmap'\n"
        "FAILED tests/test_a.py::test_b - AssertionError: boom\n"
        "1 failed, 10 passed in 0.2s\n"
    )
    assert _load().annotations(text) == [
        "::error title=tests/test_config.py::test_dump_round_trip::UnicodeDecodeError: 'charmap'",
        "::error title=tests/test_a.py::test_b::AssertionError: boom",
    ]


def test_a_failure_without_a_reason_still_gets_an_annotation():
    """pytest omits the reason for some failures; the title must not be empty."""
    assert _load().annotations("FAILED tests/test_a.py::test_b\n") == [
        "::error title=tests/test_a.py::test_b::failed"
    ]


def test_a_run_with_no_failures_says_nothing():
    assert _load().annotations("12 passed in 0.3s\n") == []


def test_a_missing_log_does_not_crash_the_step(tmp_path, capsys):
    """The step runs with `|| true`, but it still has to fail loudly, not quietly."""
    load = _load()
    assert load.main(["report_failures.py", str(tmp_path / "gone.txt")]) == 0
    assert "collection or install error" in capsys.readouterr().out
