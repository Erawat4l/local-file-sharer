"""Friendly, actionable errors.

Every error in local-file-sharer carries a human message, an optional list of
suggested fixes ("hints") and an optional technical detail. The CLI renders
them as a panel instead of a traceback unless ``--debug`` is passed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence


class SharerError(Exception):
    """Base class for all user-facing errors."""

    title = "Something went wrong"
    default_hint = "Re-run with --debug to see the full traceback."

    def __init__(
        self,
        message: str,
        *,
        hints: Sequence[str] | str | None = None,
        detail: str | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if hints is None:
            hints = []
        elif isinstance(hints, str):
            hints = [hints]
        self.hints: list[str] = list(hints)
        self.detail = detail
        if title:
            self.title = title

    def all_hints(self) -> list[str]:
        return list(self.hints) or [self.default_hint]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ConfigError(SharerError):
    title = "Configuration problem"
    default_hint = "Fix the value in ~/.config/local-file-sharer/config.toml or delete the file to reset."


class NetworkError(SharerError):
    title = "Network problem"
    default_hint = "Check that you are connected to Wi-Fi and that both devices are on the same network."


class ToolMissingError(SharerError):
    title = "Optional tool not found"
    default_hint = "Run `start doctor` to see what is installed and how to install it."


class ShareError(SharerError):
    title = "Share failed"
    default_hint = "Try a different port with `--port`, or re-run `start doctor`."


class PickerError(SharerError):
    title = "Cannot read that location"
    default_hint = "Pick another folder, or check permissions with `ls -l`."


class UpdateError(SharerError):
    title = "Update failed"
    default_hint = "Update manually with `pipx upgrade local-file-sharer`."


class TorrentError(SharerError):
    title = "Torrent failed"
    default_hint = "Torrent mode needs at least one file; try a smaller selection."


def bullet_list(items: Iterable[str]) -> list[str]:
    """Helpers for building hint lists."""
    return [i for i in items if i]
