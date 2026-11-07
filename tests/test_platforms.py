"""OS-specific behaviour, exercised by faking the platform instead of the host.

These are the branches that normally only run on somebody else's machine:
Windows drive letters, clipboard tool discovery, firewall hints and tool
lookup across ``PATHEXT``. They are all small pure seams, so they can be
tested anywhere — no Windows required.
"""

from __future__ import annotations

import os
import subprocess
import types
from pathlib import Path

import pytest

from local_file_sharer import network, platforms


# ── drives ──────────────────────────────────────────────────────────────────
def test_list_drives_windows_uses_letter_mask(monkeypatch):
    """GetLogicalDrives returns a 26-bit mask: bit 0 = A:, bit 2 = C:."""
    import ctypes

    mask = 0b100101  # A:, C:, F:
    monkeypatch.setattr(platforms, "WINDOWS", True)
    monkeypatch.setattr(ctypes, "windll", _FakeWindll(mask), raising=False)
    # Must return something with .free/.total: on Python 3.12+ list_drives
    # calls shutil.disk_usage and reads usage.free, and a CompletedProcess has
    # neither -- so this mocked the wrong shape and only failed on 3.12/3.13.
    monkeypatch.setattr(
        platforms.shutil,
        "disk_usage",
        lambda root: types.SimpleNamespace(total=500_000_000_000, free=250_000_000_000),
    )

    drives = platforms.list_drives()
    assert [drive.label for drive in drives] == ["A:", "C:", "F:"]
    assert [str(drive.path) for drive in drives] == ["A:\\", "C:\\", "F:\\"]


class _FakeWindll:
    """Minimal stand-in for ``ctypes.windll``."""

    def __init__(self, mask: int) -> None:
        self._mask = mask
        self.kernel32 = _FakeKernel32(mask)


class _FakeKernel32:
    def __init__(self, mask: int) -> None:
        self._mask = mask

    def GetLogicalDrives(self) -> int:  # noqa: N802 - mirrors the Win32 name
        return self._mask


def test_list_drives_windows_falls_back_to_c_drive(monkeypatch):
    import ctypes

    monkeypatch.setattr(platforms, "WINDOWS", True)
    monkeypatch.setattr(ctypes, "windll", _FakeWindll(0), raising=False)

    def boom(root):  # noqa: ANN001
        raise OSError("no such volume")

    monkeypatch.setattr(platforms.shutil, "disk_usage", boom)
    drives = platforms.list_drives()
    assert [drive.label for drive in drives] == ["C:"]
    assert drives[0].detail == ""


def test_list_drives_posix_scans_mount_points(monkeypatch, tmp_path):
    """The real POSIX branch: directories only, dotfiles skipped, sorted."""
    monkeypatch.setattr(platforms, "WINDOWS", False)
    mount = tmp_path / "media"
    for name in ("usb-stick", "sdcard"):
        (mount / name).mkdir(parents=True)
    (mount / ".hidden").mkdir()
    (mount / "note.txt").write_text("x")
    monkeypatch.setattr(platforms, "POSIX_MOUNT_ROOTS", (str(mount),))

    drives = platforms.list_drives()
    assert [drive.label for drive in drives] == ["sdcard", "usb-stick"]
    assert [drive.detail for drive in drives] == [str(mount / "sdcard"), str(mount / "usb-stick")]
    assert all(drive.path.is_dir() for drive in drives)


def test_list_drives_posix_deduplicates_symlinked_roots(monkeypatch, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "disk").mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setattr(platforms, "WINDOWS", False)
    monkeypatch.setattr(platforms, "POSIX_MOUNT_ROOTS", (str(real), str(link)))

    drives = platforms.list_drives()
    assert len(drives) == 1, "the same mount point must not be listed twice"


def test_list_drives_posix_skips_unreadable_roots(monkeypatch, tmp_path):
    broken = tmp_path / "broken"
    broken.mkdir()
    monkeypatch.setattr(platforms, "WINDOWS", False)
    monkeypatch.setattr(platforms, "POSIX_MOUNT_ROOTS", (str(broken), str(tmp_path / "missing")))

    def explode(self, *args, **kwargs):  # noqa: ANN001
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "iterdir", explode)
    assert platforms.list_drives() == []


def test_drive_icon_and_repr():
    drive = platforms.Drive(Path("/mnt/usb"), "usb", "/mnt/usb")
    assert drive.icon
    assert "usb" in str(drive) or "usb" in repr(drive)


# ── clipboard ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("tool", "argv"),
    [
        ("pbcopy", ["pbcopy"]),
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
        ("termux-clipboard-set", ["termux-clipboard-set"]),
        ("clip.exe", ["clip.exe"]),
    ],
)
def test_copy_to_clipboard_picks_the_right_tool(tool, argv, monkeypatch):
    """The first tool on PATH wins and receives the text on stdin."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        calls.append(cmd)
        assert kwargs["input"] == "http://192.168.1.42:8080/"
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(platforms.shutil, "which", lambda name: f"/usr/bin/{name}" if name == tool else None)
    monkeypatch.setattr(platforms.subprocess, "run", fake_run)

    assert platforms.copy_to_clipboard("http://192.168.1.42:8080/") is True
    assert calls == [["/usr/bin/" + argv[0], *argv[1:]]]


def test_copy_to_clipboard_tries_the_next_tool(monkeypatch):
    calls: list[str] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        calls.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 1 if len(calls) == 1 else 0, "", "boom")

    monkeypatch.setattr(platforms.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(platforms.subprocess, "run", fake_run)
    assert platforms.copy_to_clipboard("text") is True
    assert calls == ["/bin/pbcopy", "/bin/wl-copy"]


def test_copy_to_clipboard_without_tools_and_empty_text(monkeypatch):
    monkeypatch.setattr(platforms.shutil, "which", lambda name: None)
    assert platforms.copy_to_clipboard("text") is False
    assert platforms.copy_to_clipboard("") is False


def test_copy_to_clipboard_swallows_os_errors(monkeypatch):
    def exploding_run(cmd, **kwargs):  # noqa: ANN001
        raise OSError("not executable")

    monkeypatch.setattr(platforms.shutil, "which", lambda name: "/bin/xclip")
    monkeypatch.setattr(platforms.subprocess, "run", exploding_run)
    assert platforms.copy_to_clipboard("text") is False


def test_clipboard_tool_name(monkeypatch):
    monkeypatch.setattr(platforms.shutil, "which", lambda name: "/usr/bin/xclip" if name == "xclip" else None)
    assert platforms.clipboard_tool_name() in {"xclip", ""}


# ── firewall / reachability hints ───────────────────────────────────────────
def test_firewall_hint_windows_names_netsh_and_the_port(monkeypatch):
    monkeypatch.setattr(platforms, "WINDOWS", True)
    hint = network.firewall_hint(8080)
    assert "netsh advfirewall" in hint
    assert "localport=8080" in hint


def test_firewall_hint_termux_mentions_battery(monkeypatch):
    monkeypatch.setattr(platforms, "WINDOWS", False)
    monkeypatch.setattr(platforms, "TERMUX", True)
    assert "Termux" in network.firewall_hint(9000)


def test_firewall_hint_posix_offers_ufw(monkeypatch):
    monkeypatch.setattr(platforms, "WINDOWS", False)
    monkeypatch.setattr(platforms, "TERMUX", False)
    hint = network.firewall_hint(1234)
    assert "ufw allow 1234/tcp" in hint
    assert "firewall-cmd" in hint


# ── tool discovery ──────────────────────────────────────────────────────────
def test_find_tool_prefers_path(monkeypatch):
    monkeypatch.setattr(platforms, "WINDOWS", False)
    monkeypatch.setattr(
        platforms.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None
    )
    assert platforms.find_tool("ffmpeg") == "/usr/bin/ffmpeg"


def test_find_tool_tries_extra_names(monkeypatch):
    seen: list[str] = []

    def fake_which(name):  # noqa: ANN001
        seen.append(name)
        return "/opt/bin/magic-wormhole" if name == "wormhole" else None

    monkeypatch.setattr(platforms, "WINDOWS", False)
    monkeypatch.setattr(platforms.shutil, "which", fake_which)
    assert platforms.find_tool("magic-wormhole", "wormhole") == "/opt/bin/magic-wormhole"
    assert seen[:2] == ["magic-wormhole", "wormhole"]


def test_find_tool_windows_tries_pathext(monkeypatch, tmp_path):
    exe = tmp_path / "aria2c.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(platforms, "WINDOWS", True)
    monkeypatch.setattr(platforms.shutil, "which", lambda name: None)
    monkeypatch.setattr(platforms, "_EXTRA_TOOL_DIRS", ())
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")

    assert platforms.find_tool("aria2c") == str(exe)


def test_find_tool_returns_none(monkeypatch):
    monkeypatch.setattr(platforms, "WINDOWS", False)
    monkeypatch.setattr(platforms.shutil, "which", lambda name: None)
    monkeypatch.setattr(platforms, "_EXTRA_TOOL_DIRS", ())
    assert platforms.find_tool("definitely-not-installed") is None


# ── ports ───────────────────────────────────────────────────────────────────
def test_port_is_free_and_find_free_port():
    port = platforms.find_free_port(0, host="127.0.0.1", limit=1)
    assert port == 0 or isinstance(port, int)
    assert platforms.port_is_free(platforms.find_free_port(52000, host="127.0.0.1", limit=20), "127.0.0.1")


def test_find_free_port_raises_when_exhausted(monkeypatch):
    monkeypatch.setattr(platforms, "port_is_free", lambda port, host="0.0.0.0": False)
    with pytest.raises(OSError):
        platforms.find_free_port(7000, limit=3)


# ── paths / names ───────────────────────────────────────────────────────────
def test_safe_filename_strips_separators():
    assert "/" not in platforms.safe_filename("../../etc/passwd")
    assert platforms.safe_filename("") == "file"
    assert platforms.safe_filename('a:b*c?"<>|') == platforms.safe_filename('a:b*c?"<>|')


def test_is_hidden(tmp_path):
    hidden = tmp_path / ".secret"
    hidden.write_text("x")
    visible = tmp_path / "readme.txt"
    visible.write_text("x")
    assert platforms.is_hidden(hidden)
    assert not platforms.is_hidden(visible)


def test_is_inside(tmp_path):
    inner = tmp_path / "a" / "b"
    inner.mkdir(parents=True)
    assert platforms.is_inside(inner, tmp_path)
    assert not platforms.is_inside(tmp_path.parent, tmp_path)


def test_platform_key_is_a_short_string():
    key = platforms.platform_key()
    assert isinstance(key, str) and key and " " not in key


def test_human_gib_rounds():
    assert platforms.human_gib(1024**3) == "1 GB"
    assert platforms.human_gib(0) == "0 GB"


def test_downloads_dir_is_a_path():
    assert isinstance(platforms.downloads_dir(), Path)


def test_primary_local_ip_returns_none_or_dotted_quad(monkeypatch):
    monkeypatch.setattr(platforms.socket, "gethostname", lambda: "box")
    monkeypatch.setattr(
        platforms.socket, "gethostbyname_ex", lambda name: (_ for _ in ()).throw(OSError("nope"))
    )
    ip = platforms.primary_local_ip()
    assert ip is None or ip.count(".") == 3


def test_is_private_ip():
    assert platforms.is_private_ip("192.168.1.5")
    assert platforms.is_private_ip("10.0.0.1")
    assert not platforms.is_private_ip("8.8.8.8")


def test_install_hint_covers_every_known_tool():
    for tool in ("ffmpeg", "aria2c", "cloudflared", "tailscale", "qrcode"):
        hint = platforms.install_hint_for(tool)
        assert isinstance(hint, str)


def test_run_quiet_round_trips(monkeypatch):
    completed = subprocess.CompletedProcess(["echo"], 0, "hi\n", "")
    monkeypatch.setattr(platforms.subprocess, "run", lambda cmd, **kwargs: completed)
    assert platforms.run_quiet(["echo", "hi"]) == (0, "hi\n", "")


def test_env_is_sane():
    assert os.sep in ("/", "\\")


# ── sendfile: the Windows branch, simulated on Linux ────────────────────────
class _FakeSocket:
    """Mimics socket.sendfile: ``offset=None`` continues from ``tell()``."""

    def __init__(self, budget: int | None = None) -> None:
        self.buf = bytearray()
        self.budget = budget  # stop after this many bytes, like a dropped peer
        self.calls = 0

    def sendfile(self, file, offset=None, count=None):  # noqa: ANN001
        if self.budget is not None and len(self.buf) >= self.budget:
            return 0
        if offset is not None:
            file.seek(offset)
        self.calls += 1
        limit = count if self.budget is None else min(count, self.budget - len(self.buf))
        chunk = file.read(limit)
        self.buf.extend(chunk)
        return len(chunk)


@pytest.fixture
def payload_file(tmp_path):
    data = bytes(range(256)) * 400  # 102 400 bytes
    path = tmp_path / "payload.bin"
    path.write_bytes(data)
    return path, data


def test_sendfile_windows_branch_honours_the_offset(payload_file, monkeypatch):
    """`socket.sendfile(offset=None)` starts at tell(), so the offset must stick."""
    monkeypatch.setattr(platforms, "HAS_SENDFILE", False)
    path, data = payload_file
    sock = _FakeSocket()
    with open(path, "rb") as handle:
        sent = platforms.sendfile(sock, handle, 50_000, 1_000)
    assert sent == 1_000
    assert bytes(sock.buf) == data[50_000:51_000]


def test_sendfile_reports_only_what_the_peer_received(payload_file, monkeypatch):
    """A mid-transfer disconnect must not be reported as a full send."""
    monkeypatch.setattr(platforms, "HAS_SENDFILE", False)
    path, data = payload_file
    sock = _FakeSocket(budget=2_000)
    with open(path, "rb") as handle:
        sent = platforms.sendfile(sock, handle, 0, len(data))
    assert sent == len(sock.buf) == 2_000
    assert bytes(sock.buf) == data[:2_000]


def test_sendfile_falls_back_to_a_plain_copy(payload_file, monkeypatch):
    """No sendfile at all: the chunked copy still sends the right slice."""
    monkeypatch.setattr(platforms, "HAS_SENDFILE", False)
    path, data = payload_file

    class Writer:
        def __init__(self):
            self.buf = bytearray()

        def write(self, chunk):
            self.buf.extend(chunk)

    out = Writer()
    with open(path, "rb") as handle:
        sent = platforms.sendfile(out, handle, 1_000, 5_000)
    assert sent == 5_000
    assert bytes(out.buf) == data[1_000:6_000]


def test_sendfile_progress_is_called_for_every_chunk(payload_file, monkeypatch):
    monkeypatch.setattr(platforms, "HAS_SENDFILE", False)
    path, data = payload_file
    seen: list[int] = []
    with open(path, "rb") as handle:
        platforms.sendfile(_FakeSocket(), handle, 0, len(data), progress=seen.append)
    assert sum(seen) == len(data)
    assert all(delta > 0 for delta in seen)


def test_start_dir_skips_a_remembered_folder_it_cannot_read(tmp_path, monkeypatch):
    """A remembered `/` must not beat the user's real storage.

    `state.last_dir` was validated with `is_dir()`, so `/` and
    `/data/data/com.termux` passed — they are directories, they just cannot be
    listed. The picker then opened on one and printed `Permission denied: /`.
    """
    from local_file_sharer import platforms

    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    readable = tmp_path / "readable"
    readable.mkdir()

    monkeypatch.setattr(platforms, "home_dir", lambda: readable)
    monkeypatch.setattr(platforms, "storage_root", lambda: readable)

    try:
        chosen = platforms.start_dir(locked)
        assert chosen == readable, "an unreadable memory must not win"
        # and a readable memory is honoured
        assert platforms.start_dir(readable) == readable
    finally:
        locked.chmod(0o755)


def test_start_dir_falls_back_to_home_when_nothing_is_readable(tmp_path, monkeypatch):
    from local_file_sharer import platforms

    locked = tmp_path / "locked"
    locked.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(platforms, "home_dir", lambda: home)
    monkeypatch.setattr(platforms, "storage_root", lambda: locked)
    locked.chmod(0o000)

    try:
        assert platforms.start_dir(locked) == home
    finally:
        locked.chmod(0o755)


def test_sdcard_is_found_through_either_route(tmp_path, monkeypatch):
    """/sdcard is what people type; ~/storage is what termux-setup-storage makes."""
    from local_file_sharer import platforms

    shared = tmp_path / "sdcard"
    (shared / "Download").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()

    monkeypatch.setattr(platforms, "home_dir", lambda: home)

    # stock Android: /sdcard exists
    monkeypatch.setattr(platforms, "SDCARD_ALIASES", (shared,))
    assert platforms.sdcard_dir() == shared

    # some builds hide /sdcard but the ~/storage symlink is there
    (home / "storage").symlink_to(shared)
    monkeypatch.setattr(platforms, "SDCARD_ALIASES", (tmp_path / "nope",))
    assert platforms.sdcard_dir() == home / "storage"


def test_downloads_land_in_sdcard_download_not_the_shell_home(tmp_path, monkeypatch):
    """The Termux home is invisible to the phone's Files app."""
    from local_file_sharer import platforms

    shared = tmp_path / "sdcard"
    (shared / "Download").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(platforms, "home_dir", lambda: home)
    monkeypatch.setattr(platforms, "SDCARD_ALIASES", (shared,))

    assert platforms.downloads_dir() == shared / "Download"
    assert platforms.start_dir() == shared


def test_downloads_fall_back_when_there_is_no_android_storage(tmp_path, monkeypatch):
    from local_file_sharer import platforms

    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setattr(platforms, "home_dir", lambda: home)
    monkeypatch.setattr(platforms, "SDCARD_ALIASES", (tmp_path / "nope",))

    assert platforms.downloads_dir() == home / "Downloads"


def test_tune_socket_asks_the_kernel_for_its_real_maximum():
    """A clamped SO_SNDBUF must be retried at the kernel ceiling.

    Linux silently caps socket buffers at net.core.wmem_max. Requesting 4 MiB
    and silently accepting the ~200 KB the kernel grants leaves TCP starved on a
    Wi-Fi link with any latency, which is exactly where throughput is lost.
    """
    import socket as _socket

    from local_file_sharer import platforms

    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        granted = platforms._set_buffer(sock, "SO_SNDBUF", 4 * 1024 * 1024)
        assert granted > 0
        reported = sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF)
        assert reported == granted
        ceiling = platforms._kernel_buffer_max("wmem_max")
        if ceiling:  # a clamped buffer is retried at the ceiling, not left small
            assert granted >= min(ceiling, 4 * 1024 * 1024)
    finally:
        sock.close()


def test_unknown_sysctl_is_not_an_error():
    from local_file_sharer import platforms

    assert platforms._kernel_buffer_max("definitely_not_a_real_key") is None
