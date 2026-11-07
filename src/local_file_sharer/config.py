"""Layered configuration.

Sources, in increasing priority:

1. Built-in defaults (this module).
2. ``~/.config/local-file-sharer/config.toml`` (``%APPDATA%`` on Windows).
3. ``./config.local.toml`` — gitignored, per-machine secrets such as device 2's
   SSH host, static IPs and API tokens.
4. ``LOCAL_FILE_SHARER_*`` environment variables.
5. CLI flags (handled by the argument parser).

The file is written with comments so it doubles as documentation. Unknown keys
are preserved but ignored so newer configs never crash older installs.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

try:  # Python 3.11+ ships tomllib; older versions get the tomli backport.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.9/3.10
    try:
        import tomli as tomllib
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]

from . import platforms
from .errors import ConfigError

CONFIG_FILENAME = "config.toml"
SECRETS_FILENAME = "config.local.toml"
ENV_PREFIX = "LOCAL_FILE_SHARER_"

THEMES = ("dark", "light", "mono")

DEFAULT_CONFIG_TEMPLATE = """\
# ─────────────────────────────────────────────────────────────────────────────
#  local-file-sharer configuration
#  Docs: https://github.com/Erawat4l/local-file-sharer#configuration
#  Everything here is optional — delete the file to restore defaults.
# ─────────────────────────────────────────────────────────────────────────────

theme = "dark"                  # dark | light | mono
accent = "white"                # any rich colour name or #RRGGBB
animate_banner = true           # animated gradient banner on start
icons = true                    # inline emoji icons in the file picker

[network]
port = 8080                     # first port to try (auto-increments if busy)
host = "0.0.0.0"                # bind address; use 127.0.0.1 for localhost-only
prefer_interface = ""           # e.g. "wlan0"; empty = auto-detect Wi-Fi first
public_ip_lookup = true         # query api.ipify.org for the public IP
connect_timeout = 1.5           # seconds per reachability probe

[speed]
socket_buffer = 4194304         # 4 MiB kernel send/recv buffers
parallel_connections = 16       # connections used by the web downloader
use_sendfile = true             # zero-copy transfers where the OS supports it
zip_compression = 1             # 0 = store (fastest), 9 = smallest
adaptive_chunking = true        # grow read chunks on fast links

[share]
pin = ""                        # optional PIN/password required by the web UI
auto_expire_minutes = 0         # 0 = never; otherwise links stop working
allow_upload = false            # let devices upload files back to you
show_qr = true                  # render the terminal QR code
copy_links = true               # copy the main link to the clipboard
serve_mode = "download"         # download | serve | stream | torrent
one_liners = ["curl", "wget", "aria2c", "powershell"]

[torrent]
tracker_port = 0                # 0 = auto; built-in HTTP tracker listens here
enable_webseed = true           # expose an HTTP webseed for the torrent
piece_length = 0                # 0 = auto (size based)
announce_interval = 30          # seconds between tracker polls in the UI

[tools]
tailscale = "auto"              # auto | require | off
cloudflared = "auto"            # auto | require | off
aria2c = "auto"
ffmpeg = "auto"

[ui]
page_size = 14                  # rows per picker page (auto-fits terminal)
confirm_quit = true
recent_limit = 12               # recent files kept in state.json
favorite_limit = 20
live_dashboard = true           # refresh the transfer dashboard in place
notify_on_done = true           # terminal bell on completed transfers

[updates]
check_on_start = false          # check GitHub for a new release at launch
channel = "stable"              # stable | prerelease

# ─────────────────────────────────────────────────────────────────────────────
#  Per-machine secrets — keep them in config.local.toml (gitignored), not here.
# ─────────────────────────────────────────────────────────────────────────────
[device2]
enabled = false
host = ""                       # e.g. "192.168.1.42" or "100.x.y.z"
port = 22
user = ""
ssh_key = ""                    # path to a key file (never commit the key)
share_dir = ""                  # where device 2 drops incoming files
"""


def config_path() -> Path:
    return platforms.config_dir() / CONFIG_FILENAME


def secrets_path() -> Path:
    override = os.environ.get(ENV_PREFIX + "SECRETS")
    if override:
        return Path(override).expanduser()
    return platforms.config_dir() / SECRETS_FILENAME


def local_project_config() -> Path | None:
    """``./config.local.toml`` next to the checkout, when developing."""
    candidate = Path.cwd() / SECRETS_FILENAME
    if candidate.is_file():
        return candidate
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class NetworkConfig:
    port: int = 8080
    host: str = "0.0.0.0"
    prefer_interface: str = ""
    public_ip_lookup: bool = True
    connect_timeout: float = 1.5


@dataclass
class SpeedConfig:
    socket_buffer: int = 4 * 1024 * 1024
    parallel_connections: int = 16
    use_sendfile: bool = True
    zip_compression: int = 1
    adaptive_chunking: bool = True


@dataclass
class ShareConfig:
    pin: str = ""
    auto_expire_minutes: int = 0
    allow_upload: bool = False
    show_qr: bool = True
    copy_links: bool = True
    serve_mode: str = "download"
    one_liners: list[str] = field(default_factory=lambda: ["curl", "wget", "aria2c", "powershell"])


@dataclass
class TorrentConfig:
    tracker_port: int = 0
    enable_webseed: bool = True
    piece_length: int = 0
    announce_interval: int = 30


@dataclass
class ToolsConfig:
    tailscale: str = "auto"
    cloudflared: str = "auto"
    aria2c: str = "auto"
    ffmpeg: str = "auto"


@dataclass
class UIConfig:
    page_size: int = 14
    confirm_quit: bool = True
    recent_limit: int = 12
    favorite_limit: int = 20
    live_dashboard: bool = True
    notify_on_done: bool = True


@dataclass
class UpdatesConfig:
    check_on_start: bool = False
    channel: str = "stable"


@dataclass
class Device2Config:
    enabled: bool = False
    host: str = ""
    port: int = 22
    user: str = ""
    ssh_key: str = ""
    share_dir: str = ""


@dataclass
class Config:
    theme: str = "dark"
    accent: str = "white"
    animate_banner: bool = True
    icons: bool = True
    network: NetworkConfig = field(default_factory=NetworkConfig)
    speed: SpeedConfig = field(default_factory=SpeedConfig)
    share: ShareConfig = field(default_factory=ShareConfig)
    torrent: TorrentConfig = field(default_factory=TorrentConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)
    device2: Device2Config = field(default_factory=Device2Config)

    # ── convenience ─────────────────────────────────────────────────────────
    @property
    def source_files(self) -> list[Path]:
        return [p for p in (config_path(), local_project_config(), secrets_path()) if p and p.is_file()]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path | None = None) -> Path:
        target = path or config_path()
        platforms.ensure_dir(target.parent)
        text = dump_config(self)
        tmp = target.with_suffix(target.suffix + ".tmp")
        # This file can hold `share.pin` in plain text, so keep it private to the
        # user rather than the 0644 a plain write_text would give it.
        descriptor = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        tmp.replace(target)
        return target


# ─────────────────────────────────────────────────────────────────────────────
# Load / merge
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = Config()

_SECTION_TYPES: dict[str, type] = {
    "network": NetworkConfig,
    "speed": SpeedConfig,
    "share": ShareConfig,
    "torrent": TorrentConfig,
    "tools": ToolsConfig,
    "ui": UIConfig,
    "updates": UpdatesConfig,
    "device2": Device2Config,
}


def _bad_value(message: str, from_cli: bool) -> ConfigError:
    if from_cli:
        return ConfigError(
            message,
            hints=[
                "Nothing was written — pass a value this key accepts",
                "Run `lfs config show` to see the current values",
            ],
        )
    return ConfigError(message, hints=["Fix the value in the config file, or delete it to start over"])


def _coerce(current: Any, raw: Any, dotted: str, *, from_cli: bool = False) -> Any:
    """Convert *raw* to the type of *current*.

    Two callers deserve different advice: a bad value in the file means "edit the
    file", while a bad `config set` argument means "retype it" — nothing was
    written, so pointing at the file sends the user to the wrong place.
    """

    if isinstance(current, bool):
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on", "y")
        return bool(raw)
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise _bad_value(f"{dotted} must be a whole number (got {raw!r})", from_cli) from exc
    if isinstance(current, float):
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise _bad_value(f"{dotted} must be a number (got {raw!r})", from_cli) from exc
    if isinstance(current, list):
        if isinstance(raw, str):
            return [item.strip() for item in raw.split(",") if item.strip()]
        if isinstance(raw, (list, tuple)):
            return [str(item) for item in raw]
        raise _bad_value(f"{dotted} must be a list (got {raw!r})", from_cli)
    if isinstance(current, str):
        if isinstance(raw, (list, tuple)):
            return ",".join(str(i) for i in raw)
        return str(raw)
    return raw


def _apply_table(target: Any, table: dict[str, Any], prefix: str = "") -> None:
    valid = {f.name: f for f in fields(target)}
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        if key not in valid:
            continue  # forward compatibility: ignore unknown keys
        current = getattr(target, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply_table(current, value, prefix=f"{dotted}.")
            continue
        setattr(target, key, _coerce(current, value, dotted))


def _read_toml(path: Path) -> dict[str, Any]:
    if tomllib is None:  # Python < 3.11 without the tomli backport
        # No parser means we cannot read the file at all. A missing *config* is
        # not fatal (defaults are fine), so only fail if the user made one.
        if not path.is_file():
            return {}
        raise ConfigError(
            f"Could not read {path}: this Python has no built-in TOML parser.",
            hints=[
                "pip install tomli   (it is installed automatically on Python 3.9/3.10)",
                f"Or move the file aside — defaults are fine: mv {shlex.quote(str(path))} {shlex.quote(str(path))}.bak",
            ],
        )
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"Could not parse {path}: {exc}",
            hints=[
                f"Fix the syntax in {path}",
                "Compare with `lfs config show --defaults`",
                f"Or move it aside: mv {shlex.quote(str(path))} {shlex.quote(str(path))}.bak",
            ],
        ) from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc


def _env_overrides() -> dict[str, Any]:
    """``LOCAL_FILE_SHARER_NETWORK__PORT=9000`` → ``{"network": {"port": 9000}}``."""
    overrides: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX) or key == ENV_PREFIX + "SECRETS":
            continue
        path = key[len(ENV_PREFIX) :].lower().split("__")
        cursor = overrides
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[path[-1]] = value
    return overrides


def load_config(*, path: Path | None = None, respect_env: bool = True) -> Config:
    cfg = Config()
    sources = [path] if path else [config_path(), local_project_config()]
    if path is None and secrets_path().is_file():
        sources.append(secrets_path())
    for source in sources:
        if source is None:
            continue
        table = _read_toml(source)
        if table:
            _apply_table(cfg, table)
    if respect_env and path is None:
        env = _env_overrides()
        if env:
            _apply_table(cfg, env)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.theme not in THEMES:
        raise ConfigError(
            f"Unknown theme {cfg.theme!r}",
            hints=[f"Choose one of: {', '.join(THEMES)}", 'Set `theme = "dark"` in config.toml'],
        )
    if cfg.share.serve_mode not in ("download", "serve", "stream", "torrent"):
        raise ConfigError(
            f"Unknown serve_mode {cfg.share.serve_mode!r}",
            hints=["Use download, serve, stream or torrent"],
        )
    if not (1 <= cfg.network.port <= 65535):
        raise ConfigError("network.port must be between 1 and 65535")
    cfg.speed.socket_buffer = max(64 * 1024, min(cfg.speed.socket_buffer, 64 * 1024 * 1024))
    cfg.speed.parallel_connections = max(1, min(cfg.speed.parallel_connections, 64))
    cfg.speed.zip_compression = max(0, min(cfg.speed.zip_compression, 9))
    cfg.ui.page_size = max(5, min(cfg.ui.page_size, 40))
    cfg.share.one_liners = [k for k in cfg.share.one_liners if k in ("curl", "wget", "aria2c", "powershell")]


def dump_config(cfg: Config | None = None) -> str:
    """Serialise *cfg* as annotated TOML (stdlib only, ordered)."""
    cfg = cfg or Config()
    scalars = [f.name for f in fields(cfg) if not hasattr(getattr(cfg, f.name), "__dataclass_fields__")]
    lines: list[str] = [
        "# LOCAL FILE SHARER configuration — generated by `lfs config init`",
        "# Docs: https://github.com/Erawat4l/local-file-sharer#configuration",
        "",
    ]
    for name in scalars:
        lines.append(f"{name} = {_toml_value(getattr(cfg, name))}")
    for section_name, section_cls in _SECTION_TYPES.items():
        section = getattr(cfg, section_name)
        lines.append("")
        lines.append(f"[{section_name}]")
        for f in fields(section_cls):
            lines.append(f"{f.name} = {_toml_value(getattr(section, f.name))}")
    return "\n".join(lines).rstrip() + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def init_config(*, force: bool = False) -> tuple[Path, bool]:
    """Write the default config. Returns ``(path, created)``."""
    target = config_path()
    if target.exists() and not force:
        return target, False
    platforms.ensure_dir(target.parent)
    target.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return target, True


def set_value(dotted: str, raw: str) -> Path:
    """`lfs config set network.port 9000` — persists to config.toml."""
    cfg = load_config(respect_env=False)
    parts = dotted.split(".")
    target: Any = cfg
    for part in parts[:-1]:
        if not hasattr(target, part):
            raise ConfigError(
                f"Unknown config section {part!r}",
                hints=[
                    "Run `lfs config show` to list every key",
                    "Nothing was written",
                ],
            )
        target = getattr(target, part)
    leaf = parts[-1]
    if not hasattr(target, leaf):
        raise ConfigError(
            f"Unknown config key {dotted!r}",
            hints=["Run `lfs config show` to list every key"],
        )
    current = getattr(target, leaf)
    setattr(target, leaf, _coerce(current, raw, dotted, from_cli=True))
    _validate(cfg)
    return cfg.save()


def get_value(dotted: str) -> Any:
    cfg = load_config()
    target: Any = cfg
    for part in dotted.split("."):
        if not hasattr(target, part):
            raise ConfigError(f"Unknown config key {dotted!r}")
        target = getattr(target, part)
    return target
