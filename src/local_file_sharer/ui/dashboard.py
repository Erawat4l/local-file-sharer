"""Live transfer dashboard — rendered in place, never clearing the screen.

* Uses ``rich.Live`` with ``transient=False`` and a *bounded* layout so the
  scrollback keeps one final frame instead of hundreds of redraws.
* Degrades to a one-line-per-N-seconds log when stdout is not a TTY (CI, pipes,
  ``--no-anim``), so logs stay readable.
* Shows speed, ETA, progress, connected devices and total sent, exactly as the
  web UI does (both read :class:`TransferTracker`).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from rich.console import Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..fmt import bar, human_count, human_size, human_speed, human_time, percent
from ..transfer.stats import TransferTracker
from .console import console, current_theme, screen_width
from .theme import Theme

SPEED_GOAL = 50 * 1024 * 1024  # the "50 MB/s or more" target on same Wi-Fi


@dataclass
class DashboardOptions:
    title: str = "sharing"
    links: list[tuple[str, str]] | None = None
    footer: str = ""
    show_devices: bool = True
    show_files: bool = True
    refresh: float = 0.5
    max_file_rows: int = 4
    goal: int = SPEED_GOAL


def _speed_badge(speed: float, goal: int, theme: Theme) -> Text:
    text = Text()
    text.append(human_speed(speed), style=f"bold {theme.success if speed >= goal else theme.accent}")
    if speed >= goal:
        text.append("  🚀 at target", style=theme.success)
    elif speed > 0:
        text.append(f"  ({int(speed / goal * 100)}% of {human_speed(goal)})", style=theme.faint)
    return text


def _device_table(tracker: TransferTracker, theme: Theme) -> Table:
    table = Table(box=None, pad_edge=False, show_header=True, header_style=theme.faint, expand=False)
    table.add_column("device", style=theme.secondary, no_wrap=True)
    table.add_column("ip", style=theme.muted, no_wrap=True)
    table.add_column("sent", justify="right", style=theme.accent, no_wrap=True)
    table.add_column("req", justify="right", style=theme.faint, no_wrap=True)
    table.add_column("now", overflow="ellipsis", max_width=34, style=theme.faint)
    devices = tracker.active_devices()
    if not devices:
        table.add_row(Text("waiting for a device…", style=theme.faint), "", "", "", "")
        return table
    for device in devices[:6]:
        state = "idle" if device.last_seen < time.time() - 3 else "active"
        label = device.label if len(device.label) <= 18 else device.label[:17] + "…"
        table.add_row(
            Text(
                f"{'📱' if 'android' in device.user_agent.lower() else '💻'} {label}",
                style=theme.secondary,
            ),
            device.ip,
            human_size(device.bytes_sent),
            str(device.requests),
            f"{state} · {device.current_file or '—'}",
        )
    return table


def _file_progress(tracker: TransferTracker, theme: Theme, limit: int, width: int) -> RenderableType:
    snapshot = tracker.snapshot()
    files = [f for f in snapshot["files"] if f["total"]]
    if not files:
        return Text("no active transfers yet — open the link on your other device", style=theme.faint)
    rows: list[RenderableType] = []
    for record in files[-limit:]:
        total = int(record["total"])
        sent = min(int(record["sent"]), total)
        ratio = percent(sent, total)
        name = record["name"].split("/")[-1]
        bar_width = max(8, min(28, width // 2 - 34))
        filled = bar(sent, total, bar_width)
        row = Text()
        row.append(f"{name[:24]:<24}", style=theme.secondary)
        row.append(" ")
        row.append(filled.replace("░", "█"), style=theme.accent)
        row.append("░" * filled.count("░"), style=f"dim {theme.faint}")
        row.append(f" {ratio:5.1f}%", style=theme.muted)
        row.append(f"  {human_size(sent)}/{human_size(total)}", style=theme.faint)
        rows.append(row)
    return Group(*rows)


def render_dashboard(tracker: TransferTracker, options: DashboardOptions) -> RenderableType:
    theme = current_theme()
    width = screen_width()
    snapshot = tracker.snapshot()
    speed = float(snapshot["speed"] or snapshot["average_speed"] or 0)
    sent = int(snapshot["bytes_sent"])
    transferred = snapshot["transferred"]
    total = int(transferred.get("total") or 0)
    done = int(transferred.get("sent") or 0)
    eta = None
    if total and speed > 0 and done < total:
        eta = (total - done) / speed

    header = Table.grid(expand=True)
    header.add_column(justify="left")
    header.add_column(justify="right")
    left = Text()
    left.append(" LFS ", style=f"bold black on {theme.accent}")
    left.append(f" {options.title}", style=theme.primary)
    left.append(f"  ·  {human_count(snapshot['device_count'], 'device')}", style=theme.muted)
    left.append(f"  ·  up {human_time(snapshot['uptime'])}", style=theme.faint)
    right = Text()
    right.append(f"{snapshot['requests']} requests", style=theme.faint)
    if snapshot["errors"]:
        right.append(f" · {snapshot['errors']} errors", style=theme.warning)
    header.add_row(left, right)

    stats = Table.grid(padding=(0, 3))
    stats.add_column(no_wrap=True)
    stats.add_column(no_wrap=True)
    stats.add_column(no_wrap=True)
    stats.add_column(no_wrap=True)
    stats.add_row(
        Text.assemble(("speed  ", theme.faint), _speed_badge(speed, options.goal, theme)),
        Text.assemble(("sent  ", theme.faint), (human_size(sent), theme.success)),
        Text.assemble(("eta  ", theme.faint), (human_time(eta) if eta else "—", theme.accent)),
        Text.assemble(
            ("peak  ", theme.faint),
            (human_speed(snapshot["peak_speed"]) if snapshot["peak_speed"] else "—", theme.muted),
        ),
    )

    parts: list[RenderableType] = [header, Text("")]
    if total:
        ratio = percent(done, total)
        parts.append(
            Text.assemble(
                (bar(done, total, max(10, width - 34)), theme.accent),
                (f"  {ratio:5.1f}%", theme.primary),
                (f"  {human_size(done)} / {human_size(total)}", theme.muted),
            )
        )
    else:
        parts.append(Text(bar(0, 1, max(10, width - 34)), style=theme.faint))
    parts.append(Text(""))
    parts.append(stats)
    parts.append(Text(""))
    parts.append(Text("Active transfers", style=theme.primary))
    parts.append(_file_progress(tracker, theme, options.max_file_rows, width))
    if options.show_devices:
        parts.append(Text(""))
        parts.append(Text("Connected devices", style=theme.primary))
        parts.append(_device_table(tracker, theme))
    if options.links:
        parts.append(Text(""))
        parts.append(Text("Links", style=theme.primary))
        link_grid = Table.grid(padding=(0, 1))
        link_grid.add_column(style=theme.faint, no_wrap=True)
        link_grid.add_column(style=theme.link)
        for label, url in options.links:
            link_grid.add_row(f"{label}", url)
        parts.append(link_grid)
    if options.footer:
        parts.append(Text(""))
        parts.append(Text(options.footer, style=theme.faint))
    return Panel(
        Group(*parts),
        title=Text.assemble((" LIVE ", f"bold black on {theme.success}")),
        title_align="left",
        border_style=theme.border,
        padding=(0, 1),
        expand=True,
    )


class Dashboard:
    """Context manager that keeps the dashboard alive for the share duration."""

    def __init__(self, tracker: TransferTracker, options: DashboardOptions | None = None) -> None:
        self.tracker = tracker
        self.options = options or DashboardOptions()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_log = 0.0

    def start(self) -> Dashboard:
        con = console()
        if con.is_terminal:
            self._live = Live(
                render_dashboard(self.tracker, self.options),
                console=con,
                refresh_per_second=max(2, int(1 / max(0.1, self.options.refresh))),
                transient=False,
                vertical_overflow="visible",
            )
            self._live.__enter__()
            self._thread = threading.Thread(target=self._loop_live, daemon=True, name="lfs-dashboard")
        else:
            self._live = None
            self._thread = threading.Thread(target=self._loop_plain, daemon=True, name="lfs-dashboard-log")
        self._thread.start()
        return self

    def _loop_live(self) -> None:
        while not self._stop.wait(self.options.refresh):
            try:
                assert self._live is not None
                self._live.update(render_dashboard(self.tracker, self.options), refresh=True)
            except Exception:  # noqa: BLE001 - never break a share for a UI glitch
                break

    def _loop_plain(self) -> None:
        con = console()
        while not self._stop.wait(max(5.0, self.options.refresh * 10)):
            snapshot = self.tracker.snapshot()
            con.print(
                Text(
                    f"[{time.strftime('%H:%M:%S')}] sent {human_size(snapshot['bytes_sent'])} · "
                    f"{human_speed(snapshot['speed'])} · {snapshot['device_count']} device(s)",
                    style="lfs.muted",
                )
            )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if getattr(self, "_live", None) is not None:
            try:
                assert self._live is not None
                self._live.update(render_dashboard(self.tracker, self.options), refresh=True)
            except Exception:  # noqa: BLE001
                pass
            try:
                self._live.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._live = None

    def __enter__(self) -> Dashboard:
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()


def one_line_status(tracker: TransferTracker) -> str:
    snapshot = tracker.snapshot()
    return (
        f"{human_speed(snapshot['speed'])} · {human_size(snapshot['bytes_sent'])} sent · "
        f"{snapshot['device_count']} device(s)"
    )


def render_summary(tracker: TransferTracker, *, title: str = "transfer complete") -> Panel:
    theme = current_theme()
    snapshot = tracker.snapshot()
    avg = float(snapshot["average_speed"] or 0)
    body = Group(
        Text(
            f"📤 {human_size(snapshot['bytes_sent'])} sent in {human_time(snapshot['uptime'])}",
            style=theme.success,
        ),
        Text(
            f"⚡ average {human_speed(avg)} · peak {human_speed(snapshot['peak_speed'])}"
            + (" 🚀 target reached" if avg >= SPEED_GOAL else ""),
            style=theme.muted,
        ),
        Text(f"👥 {snapshot['device_count']} device(s) · {snapshot['requests']} requests", style=theme.faint),
    )
    return Panel(
        body, title=title, title_align="left", border_style=theme.success, padding=(0, 1), expand=False
    )
