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
