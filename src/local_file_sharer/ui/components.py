"""Reusable Rich widgets: chips, key hints, dividers, tables, error panels."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import APP_NAME, REPO_URL, __version__
from ..fmt import fit, human_size
from .console import console, current_theme, screen_width
from .theme import Theme


# ── small pieces ────────────────────────────────────────────────────────────
def divider(*, width: int | None = None, char: str = "─") -> Text:
    theme = current_theme()
    width = width or max(20, screen_width() - 4)
    return Text(char * width, style=theme.faint)


def stat_chip(icon: str, value: str, style: str | None = None) -> Text:
    theme = current_theme()
    chip = Text()
    chip.append(f" {icon} ", style=theme.faint)
    chip.append(value, style=style or theme.secondary)
    chip.append(" ", style=theme.faint)
    return chip


def key_hint(key: str, label: str) -> Text:
    theme = current_theme()
    hint = Text()
    hint.append(f" {key} ", style=theme.key)
    hint.append(f" {label} ", style=theme.faint)
    return hint


class KeyHint:
    """Object form for places that build hint lists dynamically."""

    def __init__(self, key: str, label: str) -> None:
        self.key = key
        self.label = label

    def render(self) -> Text:
        return key_hint(self.key, self.label)

    def __rich__(self) -> Text:  # pragma: no cover - rich protocol
        return self.render()


def footer_keys(pairs: Sequence[tuple[str, str]]) -> Text:
    """A single wrapped line of ``key label`` hints."""
    theme = current_theme()
    out = Text()
    for index, (key, label) in enumerate(pairs):
        if index:
            out.append("  ", style=theme.faint)
        out.append(f" {key} ", style=theme.key)
        out.append(f"{label}", style=theme.faint)
    return out


def empty_state(icon: str, title: str, hint: str = "") -> Panel:
    theme = current_theme()
    body = Text()
    body.append(f"{icon}\n", style=theme.muted)
    body.append(f"{title}\n", style=theme.primary)
    if hint:
        body.append(hint, style=theme.faint)
    return Panel(body, border_style=theme.border_soft, expand=False, padding=(1, 3))


# ── panels ──────────────────────────────────────────────────────────────────
def header_panel(title: str, subtitle: str = "", *, badge: str = "") -> Panel:
    theme = current_theme()
    head = Text()
    head.append(" LFS ", style=f"bold black on {theme.accent}")
    head.append(f" {title}", style=theme.primary)
    if badge:
        head.append(f"  {badge}", style=theme.muted)
    body: list[RenderableType] = [head]
    if subtitle:
        body.append(Text(subtitle, style=theme.muted))
    return Panel(Group(*body), border_style=theme.border, padding=(0, 1), expand=False)


def error_panel(
    message: str, *, hints: Sequence[str] = (), detail: str = "", title: str = "Something went wrong"
) -> Panel:
    theme = current_theme()
    body: list[RenderableType] = [Text(message, style=theme.danger)]
    if hints:
        body.append(Text(""))
        body.append(Text("Try this:", style=theme.primary))
        for hint in hints:
            line = Text()
            line.append("  • ", style=theme.accent)
            for index, part in enumerate(str(hint).splitlines()):
                if index:
                    line.append(f"\n    {part}", style=theme.secondary)
                else:
                    line.append(part, style=theme.secondary)
            body.append(line)
    if detail:
        body.append(Text(""))
        body.append(Text(detail, style=theme.faint))
    return Panel(
        Group(*body), title=f"! {title}", title_align="left", border_style=theme.danger, padding=(1, 2)
    )


def info_panel(
    lines: Iterable[tuple[str, str]],
    *,
    title: str = "",
    border: str | None = None,
    value_style: str | None = None,
    expand: bool = False,
) -> Panel:
    theme = current_theme()
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style=theme.faint, no_wrap=True)
    grid.add_column(style=value_style or theme.secondary)
    for key, value in lines:
        grid.add_row(str(key), str(value) if not isinstance(value, Text) else value)
    return Panel(
        grid,
        title=Text(title, style=theme.muted) if title else None,
        title_align="left",
        border_style=border or theme.border,
        padding=(0, 1),
        expand=expand,
    )


def kv_table(
    rows: Iterable[tuple[str, str]], *, headers: tuple[str, str] = ("", ""), title: str = ""
) -> Table:
    theme = current_theme()
    table = Table(
        title=title or None,
        title_align="left",
        border_style=theme.border_soft,
        header_style=theme.faint,
        show_edge=False,
        pad_edge=False,
        expand=False,
    )
    if headers[0]:
        table.add_column(headers[0], style=theme.muted, no_wrap=True)
    else:
        table.add_column(style=theme.muted, no_wrap=True)
    table.add_column(headers[1] or "", style=theme.secondary)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    return table


def file_table(items: Sequence[dict], *, width: int | None = None) -> Table:
    """The share summary table: icon, name, size, status (no Type column)."""
    theme = current_theme()
    width = width or screen_width()
    table = Table(
        box=None,
        pad_edge=False,
        show_edge=False,
        border_style=theme.border_soft,
        header_style=theme.faint,
        expand=False,
    )
    table.add_column("", width=2, no_wrap=True)
    table.add_column("name", overflow="ellipsis", max_width=max(20, width - 34))
    table.add_column("size", justify="right", style=theme.muted, no_wrap=True)
    table.add_column("", style=theme.faint, no_wrap=True)
    for item in items:
        table.add_row(
            item.get("icon", "-"),
            Text(item.get("name", ""), style=theme.secondary),
            item.get("size", ""),
            item.get("status", ""),
        )
    return table


def speed_bar(speed: float, *, peak: float = 0.0, width: int = 24) -> Text:
    """A little meter that saturates at 100 MB/s (LAN realistic)."""
    theme = current_theme()
    ratio = min(1.0, max(0.0, speed / (100 * 1024 * 1024)))
    filled = round(ratio * width)
    bar = Text()
    bar.append("█" * filled, style=theme.success)
    bar.append("░" * (width - filled), style=theme.faint)
    return bar


def mode_badge(mode: str) -> Text:
    theme = current_theme()
    # one-cell glyphs: a double-width emoji in a phone terminal shifts the
    # whole badge and the border it sits in
    icons = {"download": "v", "serve": "n", "stream": ">", "torrent": "t"}
    labels = {"download": "Download", "serve": "Serve", "stream": "Stream", "torrent": "Torrent"}
    text = Text()
    text.append(f" {icons.get(mode, '•')} {labels.get(mode, mode)} ", style=f"bold black on {theme.accent}")
    return text


def link_lines(links: Sequence[tuple[str, str]], *, width: int | None = None) -> Table:
    width = width or screen_width()
    theme = current_theme()
    table = Table.grid(padding=(0, 1))
    table.add_column(no_wrap=True, style=theme.faint)
    table.add_column(overflow="ellipsis", style=theme.link)
    for label, url in links:
        table.add_row(f"{label:>12}", Text(fit(url, max(20, width - 16)), style=theme.link))
    return table


def shortcut_message(text: str) -> Text:
    theme = current_theme()
    return Text(f" {text} ", style=f"bold black on {theme.accent}")


# ── static help texts ───────────────────────────────────────────────────────
HELP_PICKER = """[bold]File picker keys[/]

  [bold]↑ ↓ / j k[/]      move the cursor
  [bold]PgUp / PgDn[/]    jump a page
  [bold]Enter[/]          open a folder (or add the highlighted file)
  [bold]Space[/]          toggle the highlighted row
  [bold]6,7,8 / 6-8[/]    select by number, then Enter
  [bold]a[/]  /  [bold]c[/]      select all / clear
  [bold]s[/]              start sharing the selection
  [bold]/[/]              fuzzy search this folder
  [bold]f[/]              favourite / unfavourite this folder
  [bold]d[/]              list drives     [bold]h[/] home     [bold]b[/] up a level
  [bold]?[/]              this help       [bold]q[/] quit
"""

HELP_MAIN = (
    """[bold]LOCAL FILE SHARER[/]

  [bold]lfs[/]                 open the interactive menu
  [bold]lfs <path>[/]          share a file or folder right away
  [bold]lfs --mode stream[/]   open the media player for the selection
  [bold]lfs doctor[/]          check tools, firewall and network
  [bold]lfs config show[/]     print the effective configuration
  [bold]lfs theme mono[/]      switch theme (dark / light / mono)
  [bold]lfs completions bash[/] print a shell completion script
  [bold]lfs --update[/]        self-update from GitHub

See the README for the full reference: """
    + REPO_URL
)


def about_panel() -> Panel:
    theme = current_theme()
    body = Group(
        Text(f"{APP_NAME} {__version__}", style=theme.primary),
        Text("MIT licensed · built with rich + qrcode", style=theme.muted),
        Text(REPO_URL, style=theme.link),
    )
    return Panel(body, border_style=theme.border_soft, expand=False, padding=(0, 2))


def summary_footer(files: int, folders: int, total_bytes: int, status: str) -> Text:
    theme = current_theme()
    text = Text()
    text.append(f"{files} files", style=theme.accent)
    text.append(" · ", style=theme.faint)
    text.append(f"{folders} subfolders", style=theme.info)
    text.append(" · ", style=theme.faint)
    text.append(human_size(total_bytes), style=theme.success)
    text.append(" · ", style=theme.faint)
    text.append(status, style=theme.secondary)
    return text


def theme_preview(theme: Theme) -> Panel:
    body = Group(
        Text("Accent gradient", style=theme.muted),
        theme.swatch(),
        Text(""),
        Text("Success: transfer complete", style=theme.success),
        Text("Warning: slow public tunnel", style=theme.warning),
        Text("Danger: link expired", style=theme.danger),
        Text("Info: LAN address 192.168.1.42", style=theme.info),
        Text("Muted: secondary text", style=theme.muted),
    )
    return Panel(
        body,
        title=f"theme: {theme.name} · accent {theme.accent_name}",
        border_style=theme.border,
        expand=False,
    )


_ = console  # re-exported for convenience
