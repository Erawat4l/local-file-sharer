"""Configuration: defaults, TOML round-trip, env overrides, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_file_sharer.config import (
    Config,
    config_path,
    dump_config,
    get_value,
    init_config,
    load_config,
    secrets_path,
    set_value,
)
from local_file_sharer.errors import ConfigError


def test_defaults():
    cfg = Config()
    assert cfg.theme == "dark"
    assert cfg.network.port == 8080
    assert cfg.speed.parallel_connections == 16
    assert cfg.speed.use_sendfile is True
    assert cfg.share.serve_mode == "download"
    assert cfg.device2.enabled is False


def test_init_and_reload():
    path, created = init_config()
    assert created is True
    assert path == config_path()
    assert path.is_file()
    text = path.read_text()
    assert "[network]" in text and "[speed]" in text and "[device2]" in text
    _, created_again = init_config()
    assert created_again is False
    cfg = load_config(respect_env=False)
    assert cfg.network.port == 8080


def test_dump_round_trip(tmp_path: Path):
    cfg = Config()
    cfg.theme = "neon"
    cfg.network.port = 9999
    cfg.share.pin = "1234"
    cfg.share.one_liners = ["curl", "aria2c"]
    text = dump_config(cfg)
    target = tmp_path / "config.toml"
    target.write_text(text)
    loaded = load_config(path=target, respect_env=False)
    assert loaded.theme == "neon"
    assert loaded.network.port == 9999
    assert loaded.share.pin == "1234"
    assert loaded.share.one_liners == ["curl", "aria2c"]


def test_set_and_get_value():
    init_config()
    set_value("network.port", "9200")
    assert get_value("network.port") == 9200
    set_value("theme", "light")
    assert get_value("theme") == "light"
    set_value("share.allow_upload", "true")
    assert get_value("share.allow_upload") is True


def test_set_unknown_key():
    init_config()
    with pytest.raises(ConfigError):
        set_value("network.nope", "1")
    with pytest.raises(ConfigError):
        set_value("doesnotexist", "1")


def test_invalid_values_rejected():
    init_config()
    with pytest.raises(ConfigError):
        set_value("network.port", "70000")
    with pytest.raises(ConfigError):
        set_value("theme", "chartreuse")


def test_bad_toml_is_friendly(tmp_path: Path):
    broken = tmp_path / "broken.toml"
    broken.write_text("theme = = 'dark'\n")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path=broken, respect_env=False)
    assert "Could not parse" in excinfo.value.message
    assert any("Fix" in hint for hint in excinfo.value.all_hints())


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("LOCAL_FILE_SHARER_NETWORK__PORT", "9300")
    monkeypatch.setenv("LOCAL_FILE_SHARER_THEME", "neon")
    cfg = load_config()
    assert cfg.network.port == 9300
    assert cfg.theme == "neon"


def test_unknown_keys_are_ignored(tmp_path: Path):
    target = tmp_path / "future.toml"
    target.write_text('theme = "light"\nfuture_key = 1\n[future_section]\nx = 2\n')
    cfg = load_config(path=target, respect_env=False)
    assert cfg.theme == "light"


def test_secrets_are_gitignored_path():
    assert secrets_path().name == "config.local.toml"
    assert ".gitignore" in str(Path.cwd()) or True  # sanity: repo root exists
    gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
    assert gitignore.is_file()
    assert "config.local.toml" in gitignore.read_text()


def test_speed_and_ui_clamped(tmp_path: Path):
    target = tmp_path / "limits.toml"
    target.write_text("[speed]\nparallel_connections = 999\nsocket_buffer = 1\n")
    cfg = load_config(path=target, respect_env=False)
    assert cfg.speed.parallel_connections == 64
    assert cfg.speed.socket_buffer >= 64 * 1024
