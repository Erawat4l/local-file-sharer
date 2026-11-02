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
    # a stale install must be distinguishable from a current one
    assert "installed from" in out
    assert "source " in out


def test_version_flags_show_the_install_source(capsys):
    """`-V` used to be argparse's version action and printed one line."""
    for flag in ("-V", "--version"):
        assert main([flag]) == 0
        out = capsys.readouterr().out
        assert __version__ in out
        assert "installed from" in out


def test_help_command(capsys):
    assert main(["help"]) == 0
    out = capsys.readouterr().out
    assert "LOCAL FILE SHARER" in out
    assert "lfs doctor" in out


def test_split_argv_detects_subcommands():
    assert split_argv(["doctor"])[0] == "doctor"
    assert split_argv(["config", "show"])[0] == "config"
    assert split_argv(["/tmp/file.txt"])[0] is None
    assert split_argv(["--port", "9000", "theme", "mono"])[0] == "theme"
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
    assert main(["theme", "mono"]) == 0
    out = capsys.readouterr().out
    assert "mono" in out
    assert main(["config", "get", "theme"]) == 0
    assert "mono" in capsys.readouterr().out


def test_theme_list(capsys):
    assert main(["theme"]) == 0
    out = capsys.readouterr().out
    assert "dark" in out and "light" in out and "mono" in out


def test_completions(capsys):
    """Each script must register completions for `lfs`, not the retired name."""
    for shell in ("fish", "bash", "powershell", "zsh"):
        assert main(["completions", shell]) == 0
        out = capsys.readouterr().out
        assert "lfs" in out
        # a script that still says `start` would never fire for the command
        assert "start" not in out
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
    for name in (
        "menu",
        "doctor",
        "config",
        "theme",
        "completions",
        "banner",
        "upgrade",
        "version",
        "help",
    ):
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


def test_doctor_json_contract(capsys):
    """`lfs doctor --json` is what issue reports and CI consume."""
    payload = json.loads(_run_json(["doctor", "--json", "--fast"]))
    assert set(payload) >= {"checks", "health", "healthy", "blocking", "warnings", "fixes"}
    assert isinstance(payload["checks"], list) and payload["checks"]
    for check in payload["checks"]:
        assert {"name", "status", "detail", "fix", "group"} <= set(check)
        assert check["status"] in {"ok", "warn", "fail", "info"}
    assert payload["blocking"] == len(payload["blocking_checks"])
    assert 0 <= payload["health"] <= 100


def _run_json(argv: list[str]) -> str:
    """Run the CLI and return stdout with the JSON document isolated."""
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        main(argv)
    text = buffer.getvalue()
    start = text.index("{")
    end = text.rindex("}")
    return text[start : end + 1]


def test_json_share_keeps_stdout_machine_readable():
    """`lfs … --json | jq` must not choke on the banner.

    Human output (banner, "share ready" panel) goes to stderr in JSON mode, so
    stdout is exactly one JSON object.
    """
    from local_file_sharer.ui import console as console_module

    console_module.human_output_to_stderr(True)
    try:
        con = console_module.console()
        assert con.stderr is True
    finally:
        console_module.human_output_to_stderr(False)
        console_module.console()


def test_human_output_stays_on_stdout_by_default():
    from local_file_sharer.ui import console as console_module

    console_module.human_output_to_stderr(False)
    con = console_module.console()
    assert con.stderr is False


def test_the_share_command_switches_streams_only_for_json():
    """The flag is what decides; nothing else may flip stdout/stderr."""
    from argparse import Namespace

    from local_file_sharer.config import Config
    from local_file_sharer.state import State
    from local_file_sharer.ui import console as console_module

    calls = []
    original = console_module.human_output_to_stderr

    def spy(enabled=True):
        calls.append(enabled)
        return original(enabled)

    console_module.human_output_to_stderr = spy
    try:
        import local_file_sharer.cli as cli_module

        real_run = cli_module.ShareRunner

        class FakeRunner:
            def __init__(self, *a, **k):
                pass

            def run(self, **k):
                class Result:
                    url = "http://x/"
                    lan_url = "http://x/"
                    public_ip = ""
                    tailscale_url = ""
                    cloudflare_url = ""
                    magnet = ""
                    bytes_sent = 0
                    duration = 0.1

                return Result()

        cli_module.ShareRunner = FakeRunner
        try:
            base = Namespace(
                mode="download",
                pin=None,
                expire=None,
                upload=False,
                tailscale=False,
                no_tailscale=True,
                cloudflare=False,
                no_cloudflare=True,
                funnel=False,
                open=False,
                no_qr=True,
                no_copy=True,
                interface=None,
                json=True,
            )
            cli_module.cmd_share(Config(), State(), base, [])
            assert calls == [True]

            base.json = False
            cli_module.cmd_share(Config(), State(), base, [])
            assert calls == [True, False]
        finally:
            cli_module.ShareRunner = real_run
    finally:
        console_module.human_output_to_stderr = original
        console_module.human_output_to_stderr(False)
