"""Network helpers, IP classification, tool lookup, updater version logic."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from local_file_sharer import platforms
from local_file_sharer.network import (
    can_connect,
    classify,
    cloudflared_available,
    describe_interfaces,
    local_endpoints,
    primary_ip,
    tailscale_status,
)
from local_file_sharer.platforms import LocalAddress
from local_file_sharer.updater import (
    ReleaseInfo,
    detect_installer,
    is_newer,
    parse_version,
    upgrade_command,
)


def test_parse_version():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2") == (1, 2, 0)
    assert parse_version("2.0.0-beta") == (2, 0, 0)
    assert parse_version("garbage") == (0,)


def test_is_newer():
    assert is_newer("1.0.1", "1.0.0")
    assert is_newer("2.0.0", "1.9.9")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("0.9.0", "1.0.0")


def test_detect_installer_returns_known_value():
    assert detect_installer() in ("pipx", "pip", "exe")


def test_upgrade_command_shape():
    cmd = upgrade_command()
    assert isinstance(cmd, list)
    if cmd:
        assert cmd[0]


def test_release_asset_selection():
    release = ReleaseInfo(
        tag="v1.2.0",
        assets=[
            {"name": "start-windows-x86_64.exe", "browser_download_url": "https://x/a.exe"},
            {"name": "start-linux-x86_64", "browser_download_url": "https://x/b"},
        ],
    )
    asset = release.asset_for_platform()
    assert asset is not None
    assert release.version == "1.2.0"


def test_release_without_assets():
    assert ReleaseInfo(tag="v1.0.0").asset_for_platform() is None


@pytest.mark.parametrize(
    ("ip", "private"),
    [
        ("192.168.1.5", True),
        ("10.0.0.7", True),
        ("172.16.5.4", True),
        ("8.8.8.8", False),
        ("not-an-ip", False),
    ],
)
def test_is_private_ip(ip, private):
    assert platforms.is_private_ip(ip) is private


def test_classify():
    assert classify(LocalAddress("192.168.1.2", "wlan0")) == "wifi"
    assert classify(LocalAddress("192.168.1.2", "eth0")) == "ethernet"
    assert classify(LocalAddress("192.168.1.2", "br-custom")) == "other"


def test_local_endpoints_have_valid_ips():
    for endpoint in local_endpoints():
        socket.inet_aton(endpoint.ip)  # raises if malformed
        assert endpoint.kind in ("wifi", "ethernet", "other")
        assert endpoint.icon


def test_primary_ip_is_usable():
    ip = primary_ip()
    assert ip is None or ip.count(".") == 3


def test_describe_interfaces():
    for iface, ip in describe_interfaces():
        assert isinstance(iface, str)
        assert ip.count(".") == 3


def test_can_connect_false_for_closed_port(free_port):
    assert can_connect("127.0.0.1", free_port, timeout=0.2) is False


def test_tailscale_status_never_raises():
    status = tailscale_status()
    assert isinstance(status.installed, bool)
    assert isinstance(status.summary(), str)


def test_cloudflared_available_boolean():
    assert isinstance(cloudflared_available(), bool)


def test_find_tool_and_install_hints():
    assert platforms.find_tool("definitely-not-a-real-tool") is None
    hint = platforms.install_hint_for("aria2c")
    assert hint
    assert platforms.platform_key() in ("termux", "windows", "macos", "linux")


def test_normalize_path(tmp_path):
    assert platforms.normalize_path("~").name != ""
    assert platforms.normalize_path(str(tmp_path)) == tmp_path


def test_safe_filename():
    assert platforms.safe_filename("a/b\\c:d") == "a_b_c_d"
    assert platforms.safe_filename("") == "file"
    assert platforms.safe_filename("CON") in ("CON", "_CON")


def test_sendfile_copy_tracks_bytes(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"z" * 100_000)
    sent = {"total": 0}
    left, right = socket.socketpair()
    try:
        with open(src, "rb") as handle:
            platforms.sendfile(
                left, handle, 0, 100_000, progress=lambda n: sent.__setitem__("total", sent["total"] + n)
            )
        right.settimeout(2)
        received = 0
        while received < 100_000:
            chunk = right.recv(65536)
            if not chunk:
                break
            received += len(chunk)
        assert received == 100_000
        assert sent["total"] == 100_000
    finally:
        left.close()
        right.close()


def test_sendfile_with_offset(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(bytes(range(256)) * 100)
    left, right = socket.socketpair()
    try:
        with open(src, "rb") as handle:
            platforms.sendfile(left, handle, 1000, 500)
        right.settimeout(2)
        received = b""
        while len(received) < 500:
            chunk = right.recv(65536)
            if not chunk:
                break
            received += chunk
        assert received == src.read_bytes()[1000:1500]
    finally:
        left.close()
        right.close()


def test_file_urls_are_quoted():
    from local_file_sharer.links import encode_filename, file_url, zip_url

    assert file_url("http://h:1/", "abc") == "http://h:1/f/abc"
    assert zip_url("http://h:1/") == "http://h:1/download/all.zip"
    assert encode_filename("a b.mp4") == "a%20b.mp4"


def test_clipboard_payload():
    from local_file_sharer.links import clipboard_payload

    text = clipboard_payload({"link": "http://x", "zip": "http://x/z.zip"})
    assert "link: http://x" in text
    assert "zip: http://x/z.zip" in text


def test_one_liners_include_all_clients():
    from local_file_sharer.links import build_all

    commands = build_all("http://192.168.1.4:8080", connections=16)
    keys = {c.key for c in commands}
    assert {"curl", "wget", "aria2c", "powershell", "bits", "browser"} <= keys
    aria = next(c for c in commands if c.key == "aria2c")
    assert "-x16" in aria.command


def test_home_and_storage_dirs_exist():
    assert platforms.home_dir().exists()
    assert isinstance(platforms.storage_root(), Path)
    assert isinstance(platforms.downloads_dir(), Path)


def test_tailscale_status_is_cached_between_menu_renders(monkeypatch):
    """The menu re-renders after every keypress; it must not spawn tailscale each time.

    ``tailscale status --json`` costs hundreds of milliseconds on a phone, and
    the status line is redrawn constantly. Five seconds of staleness is free.
    """
    from local_file_sharer import network, platforms

    calls: list[list[str]] = []

    def fake_find(name, *a, **k):
        return "/usr/bin/tailscale" if name == "tailscale" else None

    def fake_run(cmd, **k):
        calls.append(list(cmd))
        return 0, "{}", ""

    monkeypatch.setattr(platforms, "find_tool", fake_find)
    monkeypatch.setattr(network, "run_quiet", fake_run)
    network._tailscale_cache = None

    network.tailscale_status(fresh=True)
    after_first = len(calls)
    assert after_first >= 1

    for _ in range(20):
        network.tailscale_status()
    assert len(calls) == after_first, "cached reads must not spawn a subprocess"

    network.tailscale_status(fresh=True)
    assert len(calls) > after_first
    network._tailscale_cache = None


# ── the "which lfs am I running" report ─────────────────────────────────────
def test_doctor_names_the_copy_that_wins_path(monkeypatch):
    """Four copies of lfs and the oldest first on PATH: this is why an upgrade
    appears to do nothing, and nothing the app printed said so."""
    from local_file_sharer import doctor

    duplicates = [
        {"name": "lfs", "path": "/usr/bin/lfs", "kind": "pip"},
        {"name": "lfs", "path": "/home/bin/lfs", "kind": "pip"},
    ]
    monkeypatch.setattr("local_file_sharer.updater.find_duplicates", lambda: duplicates)
    monkeypatch.setattr("local_file_sharer.updater._first_on_path", lambda: "/home/bin/lfs")

    checks = doctor.check_install_copies()
    winner = next(c for c in checks if c.status == "warn")
    assert "/home/bin/lfs" in winner.detail
    assert winner.fix == "rm /home/bin/lfs", "the fix must name the real command"
    assert all(c.status in ("warn", "info") for c in checks)


def test_doctor_is_clean_when_only_one_copy_exists(monkeypatch):
    from local_file_sharer import doctor

    monkeypatch.setattr("local_file_sharer.updater.find_duplicates", lambda: [])
    checks = doctor.check_install_copies()
    assert len(checks) == 1 and checks[0].status == "ok"


def test_doctor_suggests_pipx_uninstall_for_a_pipx_copy(monkeypatch):
    from local_file_sharer import doctor

    duplicates = [{"name": "lfs", "path": "/home/.local/bin/lfs", "kind": "pipx"}]
    monkeypatch.setattr("local_file_sharer.updater.find_duplicates", lambda: duplicates)
    monkeypatch.setattr("local_file_sharer.updater._first_on_path", lambda: "/home/.local/bin/lfs")
    check = next(c for c in doctor.check_install_copies() if c.status == "warn")
    assert check.fix == "pipx uninstall local-file-sharer"


# ── `lfs doctor --fix-copies` ───────────────────────────────────────────────
def _shadow_setup(tmp_path, monkeypatch):
    """Two fake lfs binaries; the *other* one wins PATH."""
    winner = tmp_path / "bin" / "lfs"
    loser = tmp_path / "other" / "lfs"
    for path in (winner, loser):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
    from local_file_sharer import updater

    monkeypatch.setattr(
        updater,
        "find_duplicates",
        lambda: [
            {"name": "lfs", "path": str(winner), "kind": "pip"},
            {"name": "lfs", "path": str(loser), "kind": "pip"},
        ],
    )
    monkeypatch.setattr(updater, "_first_on_path", lambda: str(winner))
    return winner, loser


def test_fix_copies_removes_every_copy_that_is_not_this_one(tmp_path, monkeypatch):
    """Both are stale relative to what is running, so both go.

    Removing only the winner leaves the next one to take its place, which is
    how a device ends up back here with the same confusing symptom.
    """
    winner, loser = _shadow_setup(tmp_path, monkeypatch)
    from local_file_sharer import updater

    updater.remove_shadowing_copies()
    assert not winner.exists(), "the shadowing copy should be gone"
    assert not loser.exists(), "the other stale copy should go too"


def test_fix_copies_dry_run_changes_nothing(tmp_path, monkeypatch):
    winner, loser = _shadow_setup(tmp_path, monkeypatch)
    from local_file_sharer import updater

    updater.remove_shadowing_copies(dry_run=True)
    assert winner.exists() and loser.exists()


def test_fix_copies_leaves_a_pipx_install_to_pipx(tmp_path, monkeypatch):
    """Deleting a pipx launcher would leave a broken half-install behind."""
    winner = tmp_path / "bin" / "lfs"
    winner.parent.mkdir(parents=True)
    winner.write_text("#!/bin/sh\n", encoding="utf-8")
    from local_file_sharer import updater

    monkeypatch.setattr(
        updater, "find_duplicates", lambda: [{"name": "lfs", "path": str(winner), "kind": "pipx"}]
    )
    monkeypatch.setattr(updater, "_first_on_path", lambda: str(winner))
    results = updater.remove_shadowing_copies()
    assert winner.exists(), "a pipx copy must be removed with pipx, not unlink()"
    assert any("pipx uninstall" in how for _p, how in results)


# ── a stalled download must not hang for ever ───────────────────────────────
def test_a_silent_process_is_killed_by_the_stall_watchdog():
    """`lfs update` sat at "0/5 contacting GitHub" with no deadline at all.

    pip's output was read inline, and iterating stdout blocks: if the clone
    stopped sending bytes there was nothing to time out and no message.
    """
    import time

    from local_file_sharer.updater import run_with_progress

    start = time.monotonic()
    code, lines = run_with_progress(
        ["python3", "-c", "import time; print('Cloning...', flush=True); time.sleep(30)"],
        stall_timeout=1.5,
    )
    assert code == 124, "a stalled run must be reported as such"
    assert time.monotonic() - start < 15, "the watchdog must not wait for the child"
    assert any("stopped answering" in line for line in lines)


def test_a_slow_but_progressing_process_is_not_killed():
    """Silence means stalled; steady output means slow. Do not confuse them."""
    from local_file_sharer.updater import run_with_progress

    code, _lines = run_with_progress(
        ["sh", "-c", "for i in 1 2 3 4; do echo \"step $i\"; sleep 0.6; done"],
        stall_timeout=2.0,
    )
    assert code == 0


def test_progress_output_and_exit_codes_still_flow_through():
    from local_file_sharer.updater import run_with_progress

    code, lines = run_with_progress(["sh", "-c", "echo 'Successfully installed x'"], stall_timeout=5)
    assert code == 0 and lines == ["Successfully installed x"]
    code, lines = run_with_progress(["sh", "-c", "echo 'boom'; exit 7"], stall_timeout=5)
    assert code == 7 and lines == ["boom"]


# ── a pipx copy must be recognised by its path, not just its text ───────────
def test_a_pipx_venv_console_script_is_recognised(tmp_path):
    """It has an ordinary Python shebang and no "pipx" in it at all, so the
    old text-only check called it a pip install and advised `pip uninstall`."""
    script = tmp_path / ".local/share/pipx/venvs/local-file-sharer/bin/lfs"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/python\nimport sys\n", encoding="utf-8")
    from local_file_sharer.updater import is_pipx_install

    assert is_pipx_install(str(script)) is True


def test_a_shim_that_names_its_pipx_venv_is_recognised(tmp_path):
    shim = tmp_path / "bin/local-file-sharer"
    shim.parent.mkdir(parents=True)
    shim.write_text(
        "#!/bin/sh\nexec /home/u/.local/share/pipx/venvs/local-file-sharer/bin/lfs \"$@\"\n",
        encoding="utf-8",
    )
    from local_file_sharer.updater import is_pipx_install

    assert is_pipx_install(str(shim)) is True


def test_a_plain_pip_script_is_not_called_pipx(tmp_path):
    script = tmp_path / "lfs"
    script.write_text("#!/usr/bin/python\nimport sys\n", encoding="utf-8")
    from local_file_sharer.updater import is_pipx_install

    assert is_pipx_install(str(script)) is False


# ── clearing every stale copy, not just the winner ──────────────────────────
def test_fix_copies_clears_all_of_them_not_only_the_winner(tmp_path, monkeypatch):
    """Four copies is normal; removing one leaves the next in its place."""
    bins = {}
    for name in ("a", "b", "c"):
        path = tmp_path / name / "lfs"
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        bins[name] = path
    from local_file_sharer import updater

    monkeypatch.setattr(
        updater,
        "find_duplicates",
        lambda: [{"name": "lfs", "path": str(p), "kind": "pip"} for p in bins.values()],
    )
    monkeypatch.setattr(updater, "_first_on_path", lambda: str(bins["a"]))
    updater.remove_shadowing_copies()
    assert not any(p.exists() for p in bins.values()), "every stale copy should go"


# ── the rehash command is per shell ─────────────────────────────────────────
@pytest.mark.parametrize(
    ("shell_path", "expected"),
    [
        ("/usr/bin/fish", "rehash"),
        ("/bin/zsh", "hash -r"),
        ("/bin/bash", "hash -r"),
        ("/bin/sh", "command -v lfs"),
        ("/usr/bin/pwsh", "$env:PATH = $env:PATH"),
    ],
)
def test_the_shell_forgets_a_moved_binary_with_its_own_command(shell_path, expected, monkeypatch):
    """`hash -r` in fish prints "fish: Unknown command: hash" -- advice that
    fails in the shell it was printed for."""
    from local_file_sharer import platforms

    monkeypatch.setenv("SHELL", shell_path)
    assert platforms.rehash_command() == expected


def test_an_unknown_shell_falls_back_to_opening_a_new_one(monkeypatch):
    from local_file_sharer import platforms

    monkeypatch.setenv("SHELL", "/usr/bin/tcsh")
    monkeypatch.setattr(platforms, "current_shell", lambda: "")
    assert platforms.rehash_command() == ""
    assert "open a new shell" in platforms.rehash_hint()


def test_the_removal_command_matches_the_flavour():
    from local_file_sharer import updater

    assert updater.removal_command({"path": "/home/bin/lfs", "kind": "pip"}) == "rm /home/bin/lfs"
    assert (
        updater.removal_command({"path": "/home/.local/bin/lfs", "kind": "pipx"})
        == "pipx uninstall local-file-sharer"
    )
