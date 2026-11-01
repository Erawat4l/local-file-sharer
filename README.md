<div align="center">

<img src="docs/assets/banner.png" alt="local-file-sharer — share files between your devices at maximum speed" width="100%">

# local-file-sharer

**Share files between your devices at maximum speed — straight from your terminal.**

One command (`start`), an interactive menu, a QR code, and a real HTTP engine that
saturates your Wi-Fi. Works on **Termux/Android**, **Linux**, **macOS** and **Windows**.

[![Python](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-Termux%20%C2%B7%20Linux%20%C2%B7%20macOS%20%C2%B7%20Windows-22d3ee)](#platform-support)
[![License: MIT](https://img.shields.io/badge/license-MIT-34d399)](LICENSE)
[![CI](https://github.com/Erawat4l/local-file-sharer/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Erawat4l/local-file-sharer?color=a78bfa&label=release)](../../releases/latest)
[![Code style: ruff](https://img.shields.io/badge/style-ruff-a78bfa)](https://github.com/astral-sh/ruff)
[![Downloads](https://img.shields.io/badge/downloads-16%20parallel%20connections-fbbf24)](#why-it-is-fast)

<img src="docs/assets/demo.gif" alt="start — the banner shimmer, then the interactive menu" width="100%">

</div>

---

## Table of contents

- [Why it is fast](#why-it-is-fast)
- [Demo](#demo)
- [Features](#features)
- [Install](#install)
  - [Termux (Android)](#termux-android)
  - [Linux / macOS](#linux--macos)
  - [Windows](#windows)
  - [From source](#from-source)
- [Quick start](#quick-start)
- [The file picker](#the-file-picker)
- [Share modes](#share-modes)
- [Network outputs](#network-outputs)
- [One-liners for every device](#one-liners-for-every-device)
- [Configuration](#configuration)
- [Command reference](#command-reference)
- [Diagnostics](#diagnostics)
- [Shell integration & completions](#shell-integration--completions)
- [Performance notes](#performance-notes)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Project layout](#project-layout)
- [Development](#development)
- [Security](#security)
- [License](#license)

---

## Why it is fast

| Technique | Where | Effect |
|---|---|---|
| `os.sendfile` zero-copy | Linux · Termux · macOS | Kernel moves file → socket, no user-space copy |
| `socket.sendfile` fallback | Windows | Native `TransmitFile` path instead of a Python loop |
| 4 MiB socket buffers + `TCP_NODELAY` | every platform | Fewer round trips, higher throughput on Wi-Fi |
| HTTP `Range` + `multipart/byteranges` | server | `aria2c -x16`, resumable `curl -C -`, seekable video |
| 16-connection parallel download button | web UI | Splits files into byte ranges and stitches them |
| Streaming ZIP64 (no temp files) | server | A 40 GB folder starts downloading instantly |
| No caching, no compression in the hot path | server | No CPU spent on gzip while transfer is the bottleneck |

### Measured throughput

Both rows are real transfers of a 200 MB random file through the actual server
(`tests/` reproduces the range maths; the numbers below come from a loopback run
on a 2-vCPU container, so they measure the engine, not the network):

| Test | Result |
|---|---|
| 1 × sequential `GET /f/<id>` | **210 MB in 0.22 s → 946 MB/s** |
| 16 × parallel `Range` requests | **210 MB in 0.26 s → 809 MB/s**, SHA-256 identical to sequential |
| Streaming ZIP + `Range` resume | resumed bytes byte-identical at a 1/3 cut |
| Wire format | every response carries `Accept-Ranges: bytes`, `Content-Length` and a stable `ETag` |

On real Wi-Fi the link is the limit, not the code: the engine keeps a 4 MiB
in-flight window per connection and `start doctor` reports whether the zero-copy
path is active on your machine.

---

## Demo

<div align="center">

| Download station | Web file server | Media player |
|---|---|---|
| <img src="docs/assets/web-download.png" width="260" alt="download station"> | <img src="docs/assets/web-server.png" width="260" alt="web file server"> | <img src="docs/assets/web-player.png" width="260" alt="media player"> |

</div>

The CLI: a gradient banner that shimmers once, a picker that never clears your
scrollback, and a live dashboard with speed, ETA, progress, devices and total sent.

<div align="center"><img src="docs/assets/cli-dashboard.png" width="100%" alt="live transfer dashboard"></div>

---

## Features

| Area | What you get |
|---|---|
| **Picker** | scroll-friendly, never clears the screen, auto-fits any width, inline icons, no *Type* column, Windows drive letters, fuzzy search (`/`), `6,7,8` / `6-8` selection, folder summaries (files, subfolders, size, shareable) |
| **Modes** | **Download** (forced, resumable, folders auto-zipped) · **Serve** (web file browser) · **Stream** (player with seeking) · **Torrent** (`.torrent` + magnet, built-in tracker, HTTP webseed) |
| **Networks** | LAN/Wi-Fi IP first (QR + one-liners), public IP, Tailscale auto-detect (`serve`/`funnel`), Cloudflare Tunnel with a slow-speed warning |
| **Speed** | `os.sendfile` → `socket.sendfile` fallback, 4 MiB buffers, `Range` support, 16-connection parallel downloader |
| **Web UIs** | download station, file browser, media player — all offline-capable, browser caching disabled |
| **CLI polish** | animated gradient banner, three themes (**dark / light / neon**), live transfer dashboard that redraws in place |
| **Convenience** | fuzzy picker, recent files, favourite folders, one-key clipboard copy of links and one-liners |
| **Safety** | optional PIN/password, auto-expiring links, upload toggle, gitignored per-machine secrets |
| **Settings** | `~/.config/local-file-sharer/config.toml` (`%APPDATA%` on Windows) with env overrides |
| **Maintenance** | `start doctor`, `start --update`, friendly errors with fixes instead of tracebacks |
| **Shells** | completions for **fish**, **bash**, **PowerShell** and **zsh**; `start` function for fish incl. Windows |

---

## Install

> Requirements: **Python 3.9+**. Everything else is optional (`aria2c`,
> `tailscale`, `cloudflared` unlock extra features).

### Termux (Android)

```bash
pkg update && pkg install python git
curl -fsSL https://raw.githubusercontent.com/Erawat4l/local-file-sharer/main/install.sh | bash
termux-setup-storage   # once, so the picker can read /sdcard
start
```

The installer also offers `aria2`, `zip`, `curl` and `wget`, and writes the fish
function + completions when fish is present.

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/Erawat4l/local-file-sharer/main/install.sh | bash
```

Prefer doing it by hand?

```bash
pipx install local-file-sharer        # recommended (isolated)
# or
python3 -m pip install --user local-file-sharer
```

Flags: `--dev` (editable install from a checkout), `--no-extras`, `--no-fish`,
`--yes`, `--uninstall`.

### Windows

```powershell
irm https://raw.githubusercontent.com/Erawat4l/local-file-sharer/main/install.ps1 | iex
```

The PowerShell installer:

1. finds Python 3.9+ (or tells you exactly how to install it),
2. installs with `pipx` (preferred) or `pip --user`,
3. wires the **fish** function at `%APPDATA%\fish\functions\start.fish`,
4. installs **`share.cmd`** as the fallback for `cmd.exe`/PowerShell, where
   `start` is a built-in and cannot be replaced,
5. registers PowerShell tab completion.

```text
fish:        start                # the fish function shadows the built-in
cmd.exe:     share                # start is a cmd built-in → share.cmd
PowerShell:  share   or   local-file-sharer
```

There is also a **standalone `local-file-sharer.exe`** attached to every tagged
release — no Python required. `start --update` swaps it for you when running the
frozen build.

### From source

```bash
git clone https://github.com/Erawat4l/local-file-sharer.git
cd local-file-sharer
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
start --help
```

---

## Quick start

```bash
start                          # interactive menu
start ~/Movies                 # share a folder in download mode
start film.mkv episode.mp4     # share several files
start --mode stream ~/Videos   # pick media, open the player
start --mode torrent ~/Photos  # .torrent + magnet + tracker + webseed
start doctor                   # check tools, firewall, network
start config show              # effective configuration
start --update                 # self-update from GitHub
```

<div align="center"><img src="docs/assets/cli-menu.png" width="100%" alt="interactive menu"></div>

---

## The file picker

<img src="docs/assets/cli-picker.png" width="100%" alt="file picker">

```text
▸   5  ○ 📄 clip.mp4                                  12.0 MB
    6  ○ 🖼️ one.jpg                                    4.9 KB
    7  ○ 🖼️ two.png                                    3.0 KB
    8  ● 📁 photos/                                   folder
```

| Key | Action |
|---|---|
| `↑ ↓` / `j k` | move |
| `PgUp` `PgDn` | page |
| `Enter` | open folder / add file |
| `Space` | toggle row |
| `6,7,8` or `6-8` | select by number, then `Enter` |
| `a` / `c` | select all / clear |
| `s` | share the selection |
| `/` | fuzzy search this folder |
| `f` | favourite / unfavourite folder |
| `d` `h` `b` | drives · home · up |
| `?` `q` | help · quit |

The footer always shows the live summary — **files · subfolders · size · shareable
status** — and folders are analysed as you select them.

---

## Share modes

| Mode | Command | What the other device gets |
|---|---|---|
| **Download** | `start --mode download <paths>` | One landing page with every file, a "download all" button using 16 parallel range requests, and a resumable `.zip` for folders |
| **Serve** | `start --mode serve <folder>` | A web file browser (search, multi-select, breadcrumbs, download folder as zip) |
| **Stream** | `start --mode stream <media>` | A media player page with seeking, playback speed, playlist and keyboard control |
| **Torrent** | `start --mode torrent <paths>` | A real `.torrent` + `magnet:` link, a **built-in HTTP tracker** and an **HTTP webseed** so pieces come straight from this machine |

All modes support the PIN gate, expiry and (optionally) uploads back to you.

With `--upload`, the browser gets a drop zone: anything dropped there is written
**into the folder you shared**, under its plain filename (path separators are
stripped, and an existing name is suffixed rather than overwritten). The listing
you can see was built when the share started, so refresh it — or look in the
folder — to see what arrived. `start doctor` reports whether uploads are on.

Each of those four paths runs against a real server on a real socket in
`tests/test_e2e.py` — the login `POST` and its cookie, `/api/manifest`, a
byte-identical download, `206` on `Range`, the player page, the tracker's
bencoded `announce` reply and the webseed's ranged reads. What is *not* proven
is a swarm: the webseed serves pieces over HTTP, but no second BitTorrent client
has been run against the tracker yet (see the roadmap).

<div align="center">
<img src="docs/assets/cli-ready.png" width="100%" alt="share ready — LAN link, QR code and one-liners">
</div>

---

## Network outputs

`start` publishes, in order of usefulness:

1. **Direct LAN/Wi-Fi** — the primary link, the QR code and every one-liner.
   Detected without third-party packages: Wi-Fi interfaces win over Ethernet,
   virtual bridges are ignored.
2. **Public IP** — looked up in a background thread so it never blocks the menu.
3. **Tailscale** — auto-detected. If you are logged in you are asked once:
   publish with `tailscale serve`, or `--funnel` for the public internet, or skip.
4. **Cloudflare Tunnel** — you are asked whether to generate a quick tunnel,
   with an explicit **slow-speed warning** (1–8 MB/s, relayed through the edge).

```bash
start --tailscale            # yes to Tailscale, no question asked
start --no-tailscale --cloudflare
start --funnel --expire 30   # public via tailscale funnel, expires in 30 min
```

---

## One-liners for every device

`start` prints and copies these automatically (clipboard support: `pbcopy`,
`wl-copy`, `xclip`, `xsel`, `termux-clipboard-set`, `clip.exe`):

```bash
curl -L --fail --progress-bar -C - -O http://192.168.1.42:8080/download/all.zip
wget -c --show-progress http://192.168.1.42:8080/download/all.zip
aria2c -x16 -s16 -k1M --file-allocation=none http://192.168.1.42:8080/download/all.zip
```

```powershell
Invoke-WebRequest -Uri "http://192.168.1.42:8080/download/all.zip" -OutFile all.zip -UseBasicParsing
Start-BitsTransfer -Source "http://192.168.1.42:8080/download/all.zip" -Destination $env:USERPROFILE\Downloads\all.zip
```

While a share is running: `c` copies all links, `o` opens the browser, `p`
re-prints the QR, `q` stops the share.

---

## Configuration

`start config init` writes `~/.config/local-file-sharer/config.toml`
(`%APPDATA%\local-file-sharer\config.toml` on Windows):

```toml
theme = "dark"                  # dark | light | neon
accent = "cyan"                 # any colour name or #RRGGBB
animate_banner = true
icons = true

[network]
port = 8080
host = "0.0.0.0"
prefer_interface = ""           # e.g. "wlan0"
public_ip_lookup = true

[speed]
socket_buffer = 4194304
parallel_connections = 16
use_sendfile = true
zip_compression = 1

[share]
pin = ""
auto_expire_minutes = 0
allow_upload = false
show_qr = true
copy_links = true
one_liners = ["curl", "wget", "aria2c", "powershell"]

[tools]
tailscale = "auto"              # auto | require | off
cloudflared = "auto"

[ui]
page_size = 14
live_dashboard = true
recent_limit = 12
```

Layered, in increasing priority:

1. built-in defaults → 2. `config.toml` → 3. `./config.local.toml`
(gitignored, for **secrets** such as device 2's SSH host, static IPs or tokens)
→ 4. `LOCAL_FILE_SHARER_*` environment variables
(`LOCAL_FILE_SHARER_NETWORK__PORT=9000`) → 5. CLI flags.

```bash
start config show | init | path | reset
start config get network.port
start config set network.port 9000
start config set share.pin 1234
```

> **Never commit secrets.** `config.local.toml`, `.env*`, keys and runtime state
> are all in [`.gitignore`](.gitignore). See [SECURITY.md](SECURITY.md).

---

## Command reference

```text
start [paths ...] [options]          share now (no paths → interactive menu)

  -m, --mode {download,serve,stream,torrent}
  -p, --port PORT            --pin [PIN]        --expire MINUTES
  --upload                   --open             --no-qr        --no-copy
  --tailscale | --no-tailscale                --funnel
  --cloudflare | --no-cloudflare
  --theme {dark,light,neon}  --no-anim          --icons | --no-icons
  --interface IFACE          --no-dashboard     --json        --debug

start menu                       open the menu explicitly
start doctor [--json] [--fast]   diagnostics
start config  show|init|path|get|set|reset
start theme   dark|light|neon|next|list [--preview]
start completions fish|bash|powershell|zsh
start banner  [--loop]           preview the animated banner
start version | help
start --update | --check-update
```

---

## Diagnostics

```text
$ start doctor
 LFS  doctor                                     local-file-sharer 1.0.0 · Ubuntu 24.04

    RUNTIME
✔   python        3.11.9 (CPython/Linux)
✔   os             Ubuntu 24.04 · my-laptop
✔   terminal       xterm-256color · 120 cols · colour
✔   clipboard      wl-copy

    PERFORMANCE
✔   sendfile       os.sendfile available (zero-copy)
✔   socket buffer  4.0 MB send buffer

    NETWORK
✔   LAN address    192.168.1.42 (wifi), 100.101.102.103 (other)
✔   bind port      0.0.0.0:8080 is free

 health 100%  ·  0 blocking issue(s), 0 warning(s)
```

`start doctor` checks Python and dependencies, optional tools (`tailscale`,
`cloudflared`, `aria2c`, `zip`, `curl`, `wget`, `ffmpeg`), the zero-copy path,
socket buffers, disk space, LAN addresses, the public IP, the bind port,
firewall commands for your OS, Tailscale/Cloudflare readiness, your config
files and the clipboard. `--json` gives machine-readable output for CI.

---

## Shell integration & completions

```bash
start completions fish        > ~/.config/fish/completions/start.fish
start completions bash        > ~/.local/share/bash-completion/completions/start
start completions powershell  # dot-source from $PROFILE (install.ps1 does it)
start completions zsh         > ~/.zfunc/_start
```

`install.sh` also drops [`shell/fish/start.fish`](shell/fish/start.fish) into
`~/.config/fish/functions/`, so typing `start` always means *local-file-sharer*
in fish, with `share` and `lfs` as aliases.

On Windows, `start` stays a built-in in `cmd.exe` and PowerShell — that is why
[`shell/windows/share.cmd`](shell/windows/share.cmd) exists as the fallback and
why the fish function is installed separately.

---

## Performance notes

- **Zero-copy sends.** On Linux/Android/macOS the socket path is `os.sendfile`;
  on Windows `socket.sendfile` is used. Both bypass the Python byte loop.
- **Big buffers.** 4 MiB `SO_SNDBUF`/`SO_RCVBUF`, `TCP_NODELAY`, and
  `TCP_WINDOW_CLAMP` on Linux. Tunable in `[speed]`.
- **Parallel ranges.** The web UI's download button issues 16 `Range` requests
  per file and stitches them into a Blob; CLI users get `aria2c -x16`.
- **Streaming ZIP.** Folders are zipped on the fly with ZIP64; ran offsets are
  deterministic, so a resumed download produces a byte-identical archive
  (verified in the test suite for 7 different cut points).
- **Store-first ZIP.** Media is stored (no deflate) since it is already
  compressed; small text files are deflated in memory up to 64 MiB so the byte
  layout stays resumable.
- **No caching.** Every page/API response carries
  `Cache-Control: no-store, no-cache, must-revalidate` so phones never show a
  stale file list.

---

## Troubleshooting

<details>
<summary><b>The other device cannot connect</b></summary>

1. Are both devices on the **same Wi-Fi**? (Guest networks and AP isolation block it.)
2. Run `start doctor` and check the *bind port* row.
3. Open the port in your firewall:

```bash
# Linux (ufw)                     # Windows (elevated)
sudo ufw allow 8080/tcp           netsh advfirewall firewall add rule name="lfs" ^
                                    dir=in action=allow protocol=TCP localport=8080
```

Termux: keep the notification visible and disable battery optimisation for Termux.
</details>

<details>
<summary><b>Permission denied when opening a folder (Termux)</b></summary>

```bash
termux-setup-storage    # grants read access to /sdcard
```
</details>

<details>
<summary><b>Slow speed over Cloudflare</b></summary>

Expected — quick tunnels are relayed through Cloudflare's edge (1–8 MB/s).
Use direct Wi-Fi for LAN transfers, or Tailscale for a VPN link instead.
</details>

<details>
<summary><b>`start` opens a new window on Windows</b></summary>

`start` is a `cmd.exe`/PowerShell built-in. In fish, the installed function
takes precedence. In cmd/PowerShell use `share` (installed by `install.ps1`), or
call `local-file-sharer` directly.
</details>

<details>
<summary><b>Video stays black in the player</b></summary>

The browser cannot decode that codec (MKV/HEVC are common). The transfer is
lossless — download the file and play it locally. `ffmpeg` converts on the host.
</details>

<details>
<summary><b>Port already in use</b></summary>

`start --port 9000`, or `start config set network.port 9000`. `start` will
auto-increment from the configured port when it is busy.
</details>

<details>
<summary><b>Anything else</b></summary>

Run it again with `--debug` for a traceback, or
[open an issue](../../issues/new/choose) with the `start doctor --json` output.
</details>

---

## Roadmap

- [x] download / serve / stream / torrent modes
- [x] zero-copy transfers, Range, 16-connection downloads
- [x] PIN, expiry, uploads, themes, dashboard, doctor, completions
- [x] standalone Windows `.exe` in releases
- [ ] **WebRTC** data channels for NAT-to-NAT transfers without a tunnel
- [ ] **Resumable uploads** from the browser UI (chunked, restartable)
- [ ] **Native Android share sheet** integration for Termux
- [ ] **QR pairing codes** that prefill PIN + expiry
- [ ] **Multi-file torrent** seeding from a real peer (not only the webseed)
- [ ] **Plugin API** for custom landing pages

Vote with a 👍 on an issue, or open a PR — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Project layout

```text
local-file-sharer/
├── src/local_file_sharer/
│   ├── cli.py               argparse + dispatch (the `start` command)
│   ├── menu.py              interactive main menu
│   ├── picker.py            file picker model (listing, fuzzy, selection)
│   ├── share.py             orchestrator: pick → prepare → publish → wait
│   ├── config.py            layered TOML config + env overrides
│   ├── state.py             recents, favourites, totals, history
│   ├── platforms.py         OS matrix: sendfile, drives, tools, clipboard
│   ├── network.py           LAN/public IP, Tailscale, Cloudflare, firewall
│   ├── doctor.py            diagnostics
│   ├── updater.py           `start --update` from GitHub releases
│   ├── completions.py       fish/bash/PowerShell/zsh scripts
│   ├── qr.py                terminal + SVG + PNG QR rendering
│   ├── transfer/
│   │   ├── manifest.py      share model and URL layout
│   │   ├── http_handler.py  Range/ETag/zip/torrent routes
│   │   ├── server.py        threaded server lifecycle
│   │   ├── zipstream.py     resumable streaming ZIP64
│   │   ├── torrent.py       bencode, .torrent, tracker, webseed
│   │   └── stats.py         live transfer statistics
│   ├── ui/                  theme, banner, picker UI, dashboard
│   └── web/assets/          downloader / server / player HTML + CSS
├── shell/                   fish functions and the Windows share.cmd
├── tests/                   300+ pytest tests (loopback only, no internet)
├── install.sh · install.ps1
└── docs/assets/             README images
```

---

## Development

```bash
git clone https://github.com/Erawat4l/local-file-sharer.git
cd local-file-sharer
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ruff check src tests && ruff format --check src tests
pytest -q                      # ~300 tests, loopback sockets only
pytest --cov=local_file_sharer
```

Conventional Commits (`feat:`, `fix:`, `docs:`, `perf:`, `chore:` …) are enforced
by review; releases are cut from tags named `vX.Y.Z` and built for Windows and
Linux by GitHub Actions, with the standalone `.exe` attached to the release.

---

## Security

- Shares are **open by default on your LAN** — use `--pin` and `--expire` on
  untrusted networks.
- No telemetry, no accounts, no cloud dependency. The only outbound calls are the
  ones you enable (public-IP lookup, tunnels, update check).
- Secrets belong in `config.local.toml` (gitignored), never in `config.toml`.
- Report vulnerabilities privately: see [SECURITY.md](SECURITY.md).

---

## License

MIT © [Erawat4l](https://github.com/Erawat4l) — see [LICENSE](LICENSE).

<div align="center"><sub>Built with <a href="https://github.com/Textualize/rich">rich</a> and <a href="https://github.com/lincolnloop/python-qrcode">qrcode</a>. No telemetry, no accounts, no cloud.</sub></div>
