"""The wake lock is what stops Android freezing a transfer mid-download.

Nothing here shells out: every case monkeypatches the platform flag and the
helper lookup, so the suite behaves the same on Linux CI, Windows CI and a
phone.
"""

from __future__ import annotations

from local_file_sharer import wakelock


class _Proc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


def _reset(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(wakelock, "_held", False)
    monkeypatch.setattr(wakelock, "_unavailable", False)


def test_a_desktop_never_calls_the_helper(monkeypatch):
    """Off Termux this must be a silent no-op, not an error at startup."""
    _reset(monkeypatch)
    monkeypatch.setattr(wakelock.platforms, "TERMUX", False)
    held, note = wakelock.acquire()
    assert held is False
    assert note == ""


def test_termux_without_the_api_reports_what_to_install(monkeypatch):
    """The phone must be told why long downloads stall, in plain words."""
    _reset(monkeypatch)
    monkeypatch.setattr(wakelock.platforms, "TERMUX", True)
    monkeypatch.setattr(wakelock, "_termux_api_installed", lambda: False)
    held, note = wakelock.acquire()
    assert held is False
    assert "Termux:API" in note


def test_a_held_lock_is_taken_once_and_released_once(monkeypatch):
    """A second share must not spawn a second helper process."""
    _reset(monkeypatch)
    monkeypatch.setattr(wakelock.platforms, "TERMUX", True)
    monkeypatch.setattr(wakelock, "_termux_api_installed", lambda: True)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ARG001
        calls.append(cmd)
        return _Proc(0)

    monkeypatch.setattr(wakelock.subprocess, "run", fake_run)
    monkeypatch.setattr(wakelock.atexit, "register", lambda fn: None)

    assert wakelock.acquire() == (True, "")
    assert wakelock.acquire() == (True, ""), "the lock is already held"
    assert calls == [["termux-wake-lock"]], calls

    wakelock.release()
    wakelock.release()
    assert calls == [["termux-wake-lock"], ["termux-wake-unlock"]], calls


def test_a_failing_helper_does_not_break_the_share(monkeypatch):
    """termux-api can be installed and still fail; a share must still start."""
    _reset(monkeypatch)
    monkeypatch.setattr(wakelock.platforms, "TERMUX", True)
    monkeypatch.setattr(wakelock, "_termux_api_installed", lambda: True)
    monkeypatch.setattr(wakelock.subprocess, "run", lambda cmd, **kw: _Proc(1))

    held, note = wakelock.acquire()
    assert held is False
    assert note == ""
    assert wakelock.is_held() is False


def test_doctor_can_say_what_state_it_is_in(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(wakelock.platforms, "TERMUX", False)
    assert wakelock.describe() == "not needed on this platform"
