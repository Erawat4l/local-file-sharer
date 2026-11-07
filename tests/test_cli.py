"""CLI surface: parsing, dispatch, config/theme/completions commands."""

from __future__ import annotations

import json

import pytest

from local_file_sharer import __version__
from local_file_sharer.cli import (
    build_command_parsers,
    build_parser,
    main,
    normalise_options,
    resolve_subcommand,
    split_argv,
)


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
        "update",
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


def test_update_is_an_alias_for_upgrade():
    """`lfs update` used to be read as a path called "update".

    It fell through to the share path, which answered "Nothing to share" — a
    confusing way to say "no such command".
    """
    from local_file_sharer.cli import split_argv

    for word in ("update", "upgrade"):
        command, rest = split_argv([word])
        assert command == word
        assert rest == []


def test_update_and_upgrade_share_one_parser():
    parsers = build_command_parsers()
    assert parsers["update"] is parsers["upgrade"]


# ── typing a command short ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("configure", "configure"),
        ("conf", "configure"),
        ("cfg", "configure"),
        ("up", "upgrade"),
        ("upd", "upgrade"),
        ("update", "update"),
        ("doc", "doctor"),
        ("doctor", "doctor"),
        ("ver", "version"),
        ("menu", "menu"),
    ],
)
def test_a_shortened_command_still_names_its_subcommand(typed, expected):
    """`lfs conf` used to fall through to the share path and answer
    "Nothing to share", which reads like a broken app rather than a typo."""
    assert resolve_subcommand(typed) == expected


@pytest.mark.parametrize("word", ["c", "co", "com", "~/Downloads", "movie.mp4", "sdcard"])
def test_ambiguous_or_pathlike_words_are_not_subcommands(word):
    """A real one- or two-letter filename must still share."""
    assert resolve_subcommand(word) == ""


def test_an_ambiguous_prefix_is_refused_rather_than_guessed():
    """`completions` and `completion` are both installed, so a prefix that
    matches more than one is refused instead of picking arbitrarily."""
    assert resolve_subcommand("com") == ""
    assert resolve_subcommand("compl") == ""
    # and an unambiguous longer prefix still works
    assert resolve_subcommand("comp") == "completions"


def test_a_shortened_command_is_detected_in_argv():
    assert split_argv(["up"]) == ("upgrade", [])
    assert split_argv(["conf"]) == ("configure", [])
    assert split_argv(["~/Downloads"]) == (None, ["~/Downloads"])


# ── a bare word is a command, not a file ────────────────────────────────────
def test_a_made_up_bare_word_is_reported_as_an_unknown_command(capsys):
    """The reported case: `lfs nonsense` answered "Nothing to share".

    That blames the file picker for a command typo, and hides the far more
    likely cause -- an older build that does not have the command.
    """
    code = main(["nonsense"])
    out = capsys.readouterr().out
    assert code == 2
    assert "Unknown command" in out
    assert "nonsense" in out
    assert "doctor" in out, "point at the command that names the shadowing copy"
    assert "Nothing to share" not in out


def test_a_word_that_names_a_command_is_reported_as_unknown_not_unshareable(capsys):
    """`lfs download` is a command word: say so, do not blame the picker."""
    code = main(["download"])
    out = capsys.readouterr().out
    assert code == 2
    assert "Unknown command" in out


def test_a_missing_absolute_path_keeps_the_share_error(capsys):
    """A path that is clearly a path must not be called a command."""
    code = main(["/tmp/definitely-not-here-1234.mp4"])
    out = capsys.readouterr().out
    assert code == 2
    assert "Nothing to share" in out
    assert "Unknown command" not in out


def test_a_missing_dot_slash_path_keeps_the_share_error(capsys):
    code = main(["./definitely-not-here-1234.mp4"])
    out = capsys.readouterr().out
    assert code == 2
    assert "Nothing to share" in out


# ── one dash where two are needed ───────────────────────────────────────────
@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        (["upgrade", "-ref", "main"], ["upgrade", "--ref", "main"]),
        (["doctor", "-fix-copies"], ["doctor", "--fix-copies"]),
        (["upgrade", "-verbose"], ["upgrade", "--verbose"]),
        (["-no-qr", "x"], ["--no-qr", "x"]),
    ],
)
def test_a_single_dash_long_option_is_repaired(typed, expected):
    """`lfs upgrade -ref main` failed with "unrecognized arguments" and dumped
    the whole usage block, because -ref has one dash and argparse only allows
    that for one-letter flags. It is the easiest typo in the world."""
    assert normalise_options(typed) == expected


@pytest.mark.parametrize(
    "untouched",
    [
        ["-p", "9000"],  # a real short option
        ["-V"],  # a real one-letter flag
        ["--ref", "main"],  # already correct
        ["-xyz"],  # no such option: leave it to fail loudly
        ["-1"],  # not a letter
        ["movie.mp4"],
        [],  # nothing to do
    ],
)
def test_option_repair_does_not_touch_anything_else(untouched):
    assert normalise_options(untouched) == untouched


def test_the_repaired_option_actually_parses():
    """End to end: the command from the bug report must reach the parser."""
    args = build_command_parsers()["upgrade"].parse_args(normalise_options(["-ref", "main"]))
    assert args.ref == "main"


def test_everything_after_a_double_dash_is_left_alone():
    assert normalise_options(["--", "-ref"]) == ["--", "-ref"]


# ── interruption is not an error ───────────────────────────────────────────
def test_eof_at_a_prompt_is_a_cancellation_not_a_crash():
    """Ctrl-C at a Rich prompt raises EOFError, and that is a "cancel".

    ``Confirm.ask`` turns ^C into ``EOFError``, so the guard for
    ``KeyboardInterrupt`` never saw it: interrupting "generate a temporary
    public Cloudflare link?" printed an "Unexpected error" panel and exited 1.
    That question is asked *after* the share is already serving, so on a phone
    the result was a traceback on top of a working server.
    """
    from local_file_sharer.cli import _handle_error

    assert _handle_error(EOFError(), debug=False) == 130


def test_an_optional_question_survives_ctrl_c():
    """The tunnel questions answer "no" instead of ending the session."""
    import rich.prompt

    from local_file_sharer.ui.components import ask_yes_no

    real = rich.prompt.Confirm.ask
    for raised in (EOFError, KeyboardInterrupt):

        def boom(*_a, _exc=raised, **_k):
            # bound as a default: a closure over the loop variable would raise
            # the last value on every iteration and prove nothing
            raise _exc

        rich.prompt.Confirm.ask = boom
        try:
            assert ask_yes_no("publish on your tailnet?", default=False) is False
        finally:
            rich.prompt.Confirm.ask = real


def test_a_second_interrupt_while_reporting_still_exits_quietly():
    """A ^C during error reporting must not escape as a traceback.

    ``main`` maps the first interrupt to 130. If the user taps the interrupt
    key again while the panel is being written, that one raises outside every
    handler and the interpreter prints a raw traceback -- which is exactly the
    wall of text a phone user saw at the Cloudflare prompt.
    """
    from local_file_sharer.cli import _guard_interrupt

    def interrupted() -> int:
        raise KeyboardInterrupt

    assert _guard_interrupt(interrupted) == 130
