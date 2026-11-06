"""Live transfer statistics for the dashboard and ``/api/stats``.

Design goals:

* O(1) updates from the hot send loop (a dict + a deque of samples).
* Thread-safe without blocking sends for long.
* Works for both the terminal dashboard and the browser (JSON snapshot).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

SAMPLE_WINDOW = 30  # seconds of samples kept
DEVICE_TIMEOUT = 20.0  # seconds before a quiet device is dropped
MIN_SAMPLE_INTERVAL = 0.2
#: Nothing on a phone's Wi-Fi or USB link exceeds this; a larger reading is an
#: artefact of dividing by a too-small time span, not a real transfer.
MAX_PLAUSIBLE_SPEED = 400 * 1024 * 1024


@dataclass
class Device:
    key: str
    ip: str = ""
    user_agent: str = ""
    kind: str = "browser"
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    bytes_sent: int = 0
    requests: int = 0
    current_file: str = ""

    @property
    def label(self) -> str:
        ua = self.user_agent.lower()
        if "curl" in ua:
            return "curl"
        if "wget" in ua:
            return "wget"
        if "aria2" in ua:
            return "aria2c"
        if "powershell" in ua or "windowspowershell" in ua:
            return "PowerShell"
        if "android" in ua:
            return "Android"
        if "iphone" in ua or "ipad" in ua:
            return "iOS"
        if "windows" in ua:
            return "Windows"
        if "mac os" in ua:
            return "macOS"
        if "linux" in ua:
            return "Linux"
        return "browser"

    @property
    def pretty_bytes(self) -> str:
        from ..fmt import human_size  # noqa: PLC0415 - avoid import cycle at module load

        return human_size(self.bytes_sent)


class TransferTracker:
    """Tracks bytes, speed, devices and per-file progress."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._samples: deque[tuple[float, int]] = deque(maxlen=400)
        # last speed that was measured over a usable timespan
        self._last_speed = 0.0
        self._total_start = time.time()
        self.bytes_sent = 0
        self.requests = 0
        self.bytes_received = 0
        self.errors = 0
        self.devices: dict[str, Device] = {}
        self.files: dict[str, dict[str, float | str | int]] = {}
        self.started_at = time.time()
        self._last_sample = 0.0
        self.peak_speed = 0.0
        self.lock = self._lock  # public alias for dashboards

    # ── updates ─────────────────────────────────────────────────────────────
    def register_request(self, ip: str, user_agent: str, *, path: str = "") -> Device:
        key = f"{ip}|{user_agent[:64]}"
        now = time.time()
        with self._lock:
            device = self.devices.get(key)
            if device is None:
                device = Device(key=key, ip=ip, user_agent=user_agent, first_seen=now)
                self.devices[key] = device
            device.last_seen = now
            device.requests += 1
            self.requests += 1
            if path:
                device.current_file = path
            self._sample_locked(0)
            return device

    def add_bytes(self, count: int, *, device: Device | None = None, file_id: str = "") -> None:
        if count <= 0:
            return
        with self._lock:
            self.bytes_sent += count
            if device is not None:
                device.bytes_sent += count
                device.last_seen = time.time()
            if file_id:
                record = self.files.setdefault(file_id, {"sent": 0, "total": 0, "started": time.time()})
                record["sent"] = int(record.get("sent", 0)) + count
            self._sample_locked(count)

    def add_received(self, count: int) -> None:
        with self._lock:
            self.bytes_received += count

    def note_error(self) -> None:
        with self._lock:
            self.errors += 1

    def start_file(self, file_id: str, name: str, total: int) -> None:
        with self._lock:
            self.files[file_id] = {
                "name": name,
                "sent": 0,
                "total": total,
                "started": time.time(),
            }

    def finish_file(self, file_id: str) -> None:
        with self._lock:
            record = self.files.get(file_id)
            if record is not None:
                record["finished"] = time.time()

    def _sample_locked(self, delta: int) -> None:
        """Record a running total of bytes sent.

        Every call appends the *cumulative* total, even when ``delta`` is 0.
        Skipping zero-delta calls left the newest sample's total stuck at an old
        value, so the elapsed time kept growing while the byte count did not —
        the reported speed collapsed towards zero mid-transfer, and the "sent"
        figure could stop advancing while data was still flowing.
        """
        now = time.time()
        total = (self._samples[-1][1] if self._samples else 0) + delta
        self._samples.append((now, total))
        if now - self._last_sample >= MIN_SAMPLE_INTERVAL:
            self._last_sample = now
            speed = self.speed_locked(window=3.0)
            if speed > self.peak_speed:
                self.peak_speed = speed

    # ── reads ───────────────────────────────────────────────────────────────
    def speed_locked(self, window: float = 3.0) -> float:
        """Bytes per second over the last *window* seconds.

        The old version gave up (returned 0.0) whenever the samples spanned
        less than 50 ms. Callers flush every 150 ms, so the first sample after
        any quiet moment always read as exactly 0 — which is what the page then
        displayed as the transfer speed. A short span is not an error; it is
        just a small denominator, and the value is clamped to a sane minimum.
        """
        now = time.time()
        if len(self._samples) < 2:
            return 0.0
        cutoff = now - window
        base_ts, base_bytes = self._samples[0]
        for ts, total in self._samples:
            if ts >= cutoff:
                base_ts, base_bytes = ts, total
                break
        latest_ts, latest_bytes = self._samples[-1]
        elapsed = latest_ts - base_ts
        if elapsed < 0.01:
            # Two samples closer together than a clock tick. Dividing by that
            # gives nonsense in the megabytes-per-microsecond range, so keep
            # showing the last real measurement instead. If there has not been
            # one yet, look further back for a pair that can be measured.
            if self._last_speed:
                return self._last_speed
            usable = [(ts, tot) for ts, tot in self._samples if latest_ts - ts >= 0.01]
            if not usable:
                # Everything arrived inside one clock tick. Reporting 0 makes the
                # page look stalled while it is actually downloading as fast as
                # it can, so report the bytes over the minimum measurable span.
                start_bytes = self._samples[0][1]
                moved = latest_bytes - start_bytes
                # Clamp to something a LAN can plausibly do. This branch only
                # runs when the clock did not move, so the divisor is a guess.
                return min(moved / 0.01, MAX_PLAUSIBLE_SPEED) if moved > 0 else 0.0
            base_ts, base_bytes = usable[0]
            elapsed = latest_ts - base_ts
        speed = min(max(0.0, (latest_bytes - base_bytes) / elapsed), MAX_PLAUSIBLE_SPEED)
        self._last_speed = speed
        return speed

    def speed(self, window: float = 3.0) -> float:
        with self._lock:
            return self.speed_locked(window)

    def average_speed(self) -> float:
        with self._lock:
            elapsed = max(0.001, time.time() - self._total_start)
            return self.bytes_sent / elapsed

    def active_devices(self, *, timeout: float = DEVICE_TIMEOUT) -> list[Device]:
        now = time.time()
        with self._lock:
            for key in [k for k, d in self.devices.items() if now - d.last_seen > timeout]:
                self.devices.pop(key, None)
            return sorted(self.devices.values(), key=lambda d: -d.last_seen)

    def progress(self) -> tuple[int, int]:
        """``(sent, total)`` across currently active files."""
        with self._lock:
            sent = sum(int(f.get("sent", 0)) for f in self.files.values())
            total = sum(int(f.get("total", 0)) for f in self.files.values())
            return sent, total

    def eta(self) -> float | None:
        sent, total = self.progress()
        if not total or sent >= total:
            return None
        speed = self.speed()
        if speed <= 0:
            return None
        return (total - sent) / speed

    def snapshot(self) -> dict:
        with self._lock:
            sent, total = self.progress()
            devices = [
                {
                    "label": d.label,
                    "ip": d.ip,
                    "bytes": d.bytes_sent,
                    "requests": d.requests,
                    "current": d.current_file,
                    "idle": round(time.time() - d.last_seen, 1),
                }
                for d in self.active_devices()
            ]
            files = [
                {
                    "name": str(f.get("name", "")),
                    "sent": int(f.get("sent", 0)),
                    "total": int(f.get("total", 0)),
                }
                for f in self.files.values()
            ]
            return {
                "bytes_sent": self.bytes_sent,
                "bytes_received": self.bytes_received,
                "speed": round(self.speed_locked(), 1),
                "average_speed": round(self.average_speed(), 1),
                "peak_speed": round(self.peak_speed, 1),
                "requests": self.requests,
                "errors": self.errors,
                "devices": devices,
                "device_count": len(devices),
                "files": files,
                "transferred": {"sent": sent, "total": total},
                "uptime": round(time.time() - self.started_at, 1),
            }

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._last_speed = 0.0
            self.bytes_sent = 0
            self.requests = 0
            self.bytes_received = 0
            self.errors = 0
            self.devices.clear()
            self.files.clear()
            self.peak_speed = 0.0
            self.started_at = time.time()
            self._total_start = time.time()
