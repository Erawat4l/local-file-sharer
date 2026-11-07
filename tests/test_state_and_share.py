"""State persistence, picker rendering, share preparation and the runner."""

from __future__ import annotations

import os
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from local_file_sharer import share as share_mod
from local_file_sharer.config import load_config
from local_file_sharer.errors import ShareError
from local_file_sharer.picker import analyze
from local_file_sharer.share import ShareRequest, ShareRunner, make_pin, prepare_manifest, run_share
from local_file_sharer.state import State
from local_file_sharer.ui import picker_ui
from local_file_sharer.ui.console import set_theme
from local_file_sharer.ui.theme import build_theme


# ── state ───────────────────────────────────────────────────────────────────
def test_state_roundtrip(tmp_path: Path):
    # Real paths, not the literal "/tmp/a.mp4": add_recent stores str(Path(...)),
    # which is "\tmp\a.mp4" on Windows, so a POSIX literal asserted nothing
    # there and made the test platform-specific for no reason.
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    state = State.load(tmp_path / "state.json")
    state.add_recent(first, limit=3)
    state.add_recent(second, limit=3)
    state.add_recent(first, limit=3)
    assert state.recent_files()[0]["path"] == str(first)
    assert state.recent_files()[0]["count"] == 2
    assert len(state.recent_files()) == 2

    again = State.load(tmp_path / "state.json")
    assert again.recent_files()[0]["path"] == str(first)
    assert again.data["install_id"]


def test_state_favorites_and_totals(tmp_path: Path):
    state = State.load(tmp_path / "s.json")
    assert state.toggle_favorite("/tmp/share") is True
    assert state.is_favorite("/tmp/share")
    assert state.toggle_favorite("/tmp/share") is False
    assert not state.is_favorite("/tmp/share")
    state.add_transfer(bytes_sent=1024, files=2)
    state.add_transfer(bytes_sent=1024, files=1)
    state.add_download(3)
    state.add_share()
    totals = state.totals()
    assert totals["bytes_sent"] == 2048
    assert totals["files_sent"] == 3
    assert totals["downloads"] == 3
    assert totals["shares"] == 1


def test_state_history_and_first_run(tmp_path: Path):
    state = State.load(tmp_path / "s.json")
    assert state.first_run() is True
    state.mark_started()
    assert state.first_run() is False
    state.add_history({"mode": "download", "path": "/x"})
    assert state.history()[0]["mode"] == "download"
    assert "at" in state.history()[0]


def test_state_corrupt_file_is_tolerated(tmp_path: Path):
    target = tmp_path / "bad.json"
    target.write_text("{not json")
    state = State.load(target)
    assert state.recent_files() == []
    state.add_recent("/tmp/x")
    assert State.load(target).recent_files()


def test_state_last_dir_and_theme(tmp_path: Path):
    state = State.load(tmp_path / "s.json")
    assert state.last_dir is None
    state.remember_dir(tmp_path)
    assert State.load(tmp_path / "s.json").last_dir == tmp_path
    state.theme = "mono"
    assert State.load(tmp_path / "s.json").theme == "mono"


# ── picker rendering ────────────────────────────────────────────────────────
def test_picker_render_without_tty(share_tree: Path):
    set_theme(build_theme("dark", "cyan"))
    state = picker_ui.PickerState(cwd=share_tree)
    state.reload()
    assert state.visible()
    assert state.current() is not None
    out = picker_ui.render(state, width=100, height=40)
    import io

    from rich.console import Console

    buffer = io.StringIO()
    Console(file=buffer, width=100, no_color=True).print(out)
    text = buffer.getvalue()
    assert "hello.txt" in text
    assert "SELECT" in text
    assert "📁" in text or "photos" in text


def test_picker_navigation_and_selection(share_tree: Path):
    state = picker_ui.PickerState(cwd=share_tree, page_size=5)
    state.reload()
    state.move(1)
    assert state.cursor == 1
    state.move(-1)
    assert state.cursor == 0
    state.toggle()
    assert len(state.selected) == 1
    state.toggle()
    assert not state.selected
    state.select_indices([0, 1])
    assert len(state.selected) == 2
    state.clear()
    assert not state.selected


def test_picker_fuzzy_query(share_tree: Path):
    state = picker_ui.PickerState(cwd=share_tree)
    state.reload()
    state.query = "mp4"
    assert [e.name for e in state.visible()] == ["clip.mp4"]
    state.query = ""
    assert len(state.visible()) > 1


def test_picker_line_mode_from_pipe(share_tree: Path, monkeypatch):
    state = picker_ui.PickerState(cwd=share_tree)
    state.reload()
    # pick hello.txt by whatever number it got on this page
    hello_index = next(i for i, e in enumerate(state.visible(), start=1) if e.name == "hello.txt")
    answers = iter([str(hello_index), ""])
    monkeypatch.setattr(picker_ui, "_fallback_prompt", lambda _s: next(answers))
    result = picker_ui._run_line_mode(state, icons=True)
    assert not result.cancelled
    assert result.paths
    assert result.paths[0].name == "hello.txt"


# ── share preparation ───────────────────────────────────────────────────────
def test_prepare_manifest(share_tree: Path):
    manifest = prepare_manifest([share_tree / "photos"], mode="download", expire_minutes=5)
    assert manifest.file_count == 3
    assert manifest.expires_at is not None
    assert manifest.mode == "download"


def test_prepare_manifest_empty_selection():
    with pytest.raises(ShareError) as excinfo:
        prepare_manifest([], mode="download", expire_minutes=0)
    assert "Nothing to share" in excinfo.value.message


def test_prepare_manifest_unreadable(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ShareError) as excinfo:
        prepare_manifest([empty], mode="download", expire_minutes=0)
    assert excinfo.value.all_hints()


def test_make_pin_is_six_digits():
    pin = make_pin()
    assert len(pin) == 6
    assert pin.isdigit()
    assert len({make_pin() for _ in range(20)}) > 1


def test_analyze_matches_manifest(share_tree: Path):
    summary = analyze([share_tree / "photos"])
    manifest = prepare_manifest([share_tree / "photos"], mode="serve", expire_minutes=0)
    assert summary.files == manifest.file_count


# ── full runner (loopback) ──────────────────────────────────────────────────
@pytest.fixture
def runner_setup(share_tree: Path):
    config = load_config(respect_env=False)
    config.ui.live_dashboard = False
    config.animate_banner = False
    config.network.public_ip_lookup = False
    state = State.load(Path(str(share_tree.parent / "state.json")))
    return config, state


def _download_everything(base: str, manifest) -> int:
    total = 0
    for entry in manifest.iter_files():
        with urllib.request.urlopen(f"{base}/f/{entry.file_id}", timeout=15) as response:
            total += len(response.read())
    return total


def _with_client(fn):
    """Patch the runner so a fake client hits the server once publication ends."""
    original_publish = ShareRunner._publish
    original_wait = ShareRunner._wait_loop

    def publish(self, result, endpoints):  # noqa: ANN001
        original_publish(self, result, endpoints)

        def client() -> None:
            fn(self, result)
            self.stop()

        threading.Thread(target=client, daemon=True).start()

    ShareRunner._publish = publish
    ShareRunner._wait_loop = original_wait
    return original_publish, original_wait


def test_share_runner_end_to_end(share_tree: Path, runner_setup, free_port):
    config, state = runner_setup
    seen: dict[str, int] = {}

    def client(runner, result):  # noqa: ANN001
        seen["bytes"] = _download_everything(result.lan_url.rstrip("/"), runner.manifest)

    original_publish, original_wait = _with_client(client)
    try:
        result = run_share(
            [share_tree / "photos", share_tree / "hello.txt"],
            config=config,
            state=state,
            port=free_port,
            no_qr=True,
            use_tailscale=False,
            use_cloudflare=False,
            copy_links=False,
        )
    finally:
        ShareRunner._publish = original_publish
        ShareRunner._wait_loop = original_wait
    assert result.lan_url.startswith("http://")
    assert result.bytes_sent >= 8000
    assert state.totals()["shares"] == 1
    assert state.recent_files()


def test_share_runner_torrent_mode(share_tree: Path, runner_setup, free_port, tmp_path, monkeypatch):
    config, state = runner_setup
    monkeypatch.setattr(share_mod.platforms, "downloads_dir", lambda: tmp_path)
    captured: dict[str, object] = {}

    def client(runner, result):  # noqa: ANN001
        with urllib.request.urlopen(f"{result.lan_url.rstrip('/')}/torrent", timeout=15) as response:
            captured["torrent_bytes"] = response.read()

    original_publish, original_wait = _with_client(client)
    try:
        result = run_share(
            [share_tree / "photos"],
            config=config,
            state=state,
            mode="torrent",
            port=free_port,
            no_qr=True,
            use_tailscale=False,
            use_cloudflare=False,
            copy_links=False,
        )
    finally:
        ShareRunner._publish = original_publish
        ShareRunner._wait_loop = original_wait
    assert result.torrent is not None
    assert result.magnet.startswith("magnet:?xt=urn:btih:")
    assert captured["torrent_bytes"][:1] == b"d"
    saved = list(tmp_path.glob("*.torrent"))
    assert saved and saved[0].stat().st_size > 100


def test_share_request_defaults():
    request = ShareRequest(paths=[Path("/tmp/x")])
    assert request.mode == "download"
    assert request.use_tailscale is None
    assert request.use_cloudflare is None
    assert request.port == 8080


def test_share_runner_unknown_mode(share_tree: Path, runner_setup):
    config, state = runner_setup
    with pytest.raises(ShareError):
        run_share([share_tree / "hello.txt"], config=config, state=state, mode="nope", wait=False)


def test_share_stops_on_expiry(share_tree: Path, runner_setup, free_port):
    config, state = runner_setup
    started = time.time()
    result = run_share(
        [share_tree / "hello.txt"],
        config=config,
        state=state,
        port=free_port,
        expire_minutes=0,
        no_qr=True,
        use_tailscale=False,
        use_cloudflare=False,
        copy_links=False,
        wait=False,
    )
    assert result.url
    assert time.time() - started < 20


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_the_state_file_is_private_to_the_user(tmp_path):
    """History holds absolute paths and share URLs — not for other accounts."""
    from local_file_sharer.state import State

    store = State(tmp_path / "state.json")
    store.add_recent("/home/someone/private-project")
    store.save()
    assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600
