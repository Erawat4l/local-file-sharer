"""CLI surface: parsing, dispatch, config/theme/completions commands."""

from __future__ import annotations

import json

import pytest

from local_file_sharer import __version__
from local_file_sharer.cli import build_command_parsers, build_parser, main, split_argv


def test_version_command(capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert "python" in out


def test_help_command(capsys):
    assert main(["help"]) == 0
    out = capsys.readouterr().out
    assert "local-file-sharer" in out
    assert "start doctor" in out


def test_split_argv_detects_subcommands():
    assert split_argv(["doctor"])[0] == "doctor"
    assert split_argv(["config", "show"])[0] == "config"
    assert split_argv(["/tmp/file.txt"])[0] is None
    assert split_argv(["--port", "9000", "theme", "neon"])[0] == "theme"
    assert split_argv([])[0] is None


def test_config_init_show_get_set(capsys, tmp_path):
    assert main(["config", "init"]) == 0
    capsys.readouterr()
    assert main(["config", "path"]) == 0
    assert "config.toml" in capsys.readouterr().out
    assert main(["config", "set", "network.port", "9111"]) == 0
    capsys.readouterr()
    assert main(["config", "get", "network.port"]) == 0
    assert "9111" in capsys.readouterr().out
    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "9111" in out and "[network]" in out


def test_config_reset(capsys):
    main(["config", "init"])
    assert main(["config", "reset", "--yes"]) == 0
    assert "deleted" in capsys.readouterr().out


def test_config_invalid_key_is_friendly(capsys):
    main(["config", "init"])
    capsys.readouterr()
    code = main(["config", "set", "nope.nope", "1"])
    assert code == 2
    out = capsys.readouterr().out
    assert "Unknown config" in out
    assert "Try this" in out


def test_theme_command(capsys):
    assert main(["theme", "neon"]) == 0
    out = capsys.readouterr().out
    assert "neon" in out
    assert main(["config", "get", "theme"]) == 0
    assert "neon" in capsys.readouterr().out


def test_theme_list(capsys):
    assert main(["theme"]) == 0
    out = capsys.readouterr().out
    assert "dark" in out and "light" in out and "neon" in out


def test_completions(capsys):
    for shell in ("fish", "bash", "powershell", "zsh"):
        assert main(["completions", shell]) == 0
        out = capsys.readouterr().out
        assert "start" in out
        assert len(out) > 200


def test_completion_alias(capsys):
    assert main(["completion", "bash"]) == 0
    assert "bash completion" in capsys.readouterr().out


def test_doctor_fast(capsys):
    code = main(["doctor", "--fast", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload["checks"], list)
    assert payload["checks"]
    assert code in (0, 1)


def test_missing_path_error_is_friendly(capsys, tmp_path):
    code = main([str(tmp_path / "nope.mp4"), "--no-qr", "--no-copy"])
    out = capsys.readouterr().out
    assert code == 2
    assert "Nothing to share" in out
    assert "Try this" in out


def test_parser_help_mentions_modes(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    out = capsys.readouterr().out
    assert "download" in out and "torrent" in out


def test_command_parsers_exist():
    parsers = build_command_parsers()
    for name in ("menu", "doctor", "config", "theme", "completions", "banner", "version", "help"):
        assert name in parsers


def test_no_color_flag_sets_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    main(["version", "--no-color"])
    import os

    assert os.environ.get("NO_COLOR") == "1"


def test_update_check_handles_offline(capsys, monkeypatch):
    import local_file_sharer.updater as updater

    def boom(**kwargs):
        raise updater.UpdateError("offline in tests", hints=["connect to the internet"])

    monkeypatch.setattr(updater, "fetch_latest", boom)
    assert updater.run_update(check_only=True) == 1
    out = capsys.readouterr().out
    assert "offline in tests" in out
    assert "connect to the internet" in out
