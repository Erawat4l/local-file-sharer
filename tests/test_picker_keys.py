"""The interactive picker only works if its keys do.

These tests drive the real key reader through a pseudo-terminal, because that
is the only way to reproduce how a terminal actually delivers an escape
sequence — and the way it used to be broken: the bytes of an arrow key sat in
Python's buffered stdin, so the reader saw a lone ESC and the cursor never
moved. A unit test that feeds a string into a fake reader cannot catch that.
"""

from __future__ import annotations

import os
import pty
import select
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="needs POSIX pty")

READER = """\
import sys
from local_file_sharer.ui.picker_ui import _read_key

for _ in range({count}):
    key = _read_key()
    sys.stdout.write(key + "\\n")
    sys.stdout.flush()
    if key == "QUIT":
        break
"""


def _run(keys: list[bytes], count: int, *, gap: float = 0.0) -> list[str]:
    """Feed *keys* to a child in a pty and return what _read_key() reported."""
    script = READER.format(count=count)
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child process
        try:
            os.execvpe(
                sys.executable,
                [sys.executable, "-c", script],
                dict(os.environ),
            )
        finally:
            os._exit(127)

    out = b""

    def drain(seconds: float) -> None:
        nonlocal out
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                out += chunk

    try:
        drain(1.2)  # let the child install cbreak
        for key in keys:
            os.write(fd, key)
            drain(0.35 if not gap else gap)
        drain(0.4)
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        os.close(fd)
    return [line for line in out.decode("utf-8", "replace").splitlines() if line]


ARROWS = {
    b"\x1b[A": "UP",
    b"\x1b[B": "DOWN",
    b"\x1b[C": "RIGHT",
    b"\x1b[D": "LEFT",
    b"\x1b[5~": "PGUP",
    b"\x1b[6~": "PGDN",
    b"\x1b[H": "HOME",
    b"\x1b[F": "END",
}


@pytest.mark.parametrize("sequence,expected", sorted(ARROWS.items()))
def test_arrow_sequences_decode_to_one_key(sequence, expected):
    """ESC [ A is one keypress, not ESC, "[", "A"."""
    assert _run([sequence], 1) == [expected]


def test_arrow_keys_survive_a_slow_pty():
    """Each byte arriving on its own must not split the sequence."""
    keys = [b"\x1b", b"[", b"B"]
    assert _run(keys, 1, gap=0.12) == ["DOWN"]


def test_plain_keys_and_space_still_work():
    assert _run([b"a", b" ", b"q"], 3) == ["a", " ", "q"]


def test_a_lone_escape_is_escape_not_a_keypress_storm():
    """ESC with nothing behind it is the ESC key — and only ESC."""
    assert _run([b"\x1b"], 1) == ["ESC"]
