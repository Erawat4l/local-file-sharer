# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **PIN gate**: one mistyped PIN used to lock the client out for two seconds, so
  the *correct* PIN came back as `429` while the page still said "Wrong PIN".
  Five attempts are now free (`FREE_PIN_ATTEMPTS`) and the lockout page always
  says how long to wait, even when a right PIN arrives during the lockout.
- **Login redirect**: the form posted the PIN, the server answered `302 /`, and
  the redirect omitted `Set-Cookie` — the browser landed back on the login page
  in a loop. `redirect()` now carries the pending cookie.
- **Share URLs**: `/api/manifest` published `http://host:port//download/<id>`
  because `base_url` was set with a trailing slash and then concatenated with
  `/download/`. `base_url` is normalised where it is set and where it is used.
- **Tracker**: announcing to a server that has a torrent but no registry
  answered `404 tracker disabled`; the registry is now created on demand.
- **Auth surface is pinned**: a test asserts that only `/healthz`,
  `/favicon.ico` and `/robots.txt` answer without the PIN, sweeping the whole
  route table. Verified it fails when `/api/manifest` is added to the exempt
  list, so the next "just exempt this one too" cannot slip through.
- **`/api/stats` leaked on PIN-protected shares**: the endpoint was exempt from
  the PIN check so the login page could show a counter, but its payload names
  every shared file and lists each visitor's IP and byte count. It now requires
  the PIN like everything else; `/healthz` stays public for monitoring.
- **Config and state files are private (0600)**: `config.toml` stores
  `share.pin` in plain text and `state.json` remembers absolute paths and share
  URLs, but both were created world-readable (0644). They are now written with
  `O_CREAT`/0600 on POSIX.
- **`config set` advice**: a rejected value told the user to "fix the value in
  ~/.config/local-file-sharer/config.toml" — but nothing had been written, so the
  file was the one place the problem was *not*. A bad argument now says so and
  points at `config show`; a genuinely broken file still points at the file.
- **`start … --json` now keeps stdout clean**: the banner and the "share ready"
  panel went to stdout alongside the final JSON object, so `--json | jq` failed
  on the first box-drawing character. Human output moves to stderr in JSON mode;
  stdout is exactly one object. Non-JSON output is unchanged.
- **ZIP edge cases are covered**: verified by hand that a 3 GiB single-file
  archive (ZIP64 territory) has a valid EOCD and central directory, that a
  3000-file archive zips completely with correct CRCs, and that a range taken
  deep inside a large archive lands on the right offset. Tests now cover the
  many-entry and deep-range cases without needing 3 GiB of disk.
- **Symlinks leaked files outside the share**: `walk_folder` listed every
  directory entry, so sharing `~/Documents` with one stray `innocent.txt ->
  ~/.ssh/id_rsa` link published the private key. Links are now followed only
  when they resolve inside the shared folder; dangling links are skipped too.
  (Naming a symlink as the *path argument* still shares it — that is explicit.)
- **QR codes are verified, not just rendered**: the existing tests only checked
  that the output contained `<svg`. New tests assert the symbol's structure
  (three finder patterns, separators, alternating timing patterns, the always-dark
  module) and that hostile `?data=` is encoded as modules instead of emitted as
  markup. Confirmed out of band with OpenCV: the rendered codes decode back to
  the exact share URL, including one carrying `#theme=neon&pin=1234`.
- **Page JS is now executed, not just pattern-matched**: a Node harness
  (`tests/js/downloader_smoke.js`) loads the real `downloader.html` script in a
  DOM shim, presses *#download-all*, and checks every file reassembles
  byte-identically. It catches runtime errors — e.g. a `null.boom()` inside the
  click handler — that source-shape assertions cannot see. Skips if Node is
  absent.
- **Expiry coverage**: expiry was only tested on `/`. It now has tests proving
  an expired share serves no bytes on `/f/`, `all.zip`, `/raw/`, `/webseed/`,
  `/torrent` or `/api/manifest`, that `Range` cannot bypass it, and that
  `/healthz` and `/api/expired` stay reachable so a monitor can tell an expired
  share from a dead one. (No behaviour change — the code was already correct.)
- **Upload collisions**: two uploads of the same name in the same second both
  reported `ok: true` but were written to the *same* `-<epoch>` filename, so the
  second silently destroyed the first. Names are now chosen with an atomic
  `O_EXCL` create (`existing-1.txt`, `existing-2.txt`, …), which also survives
  concurrent uploads.
- **Truncated uploads**: a client that promised more bytes than it sent got
  `ok: true` and left a partial file under a clean name. That now returns `400`
  and the stub is removed.
- **429 responses** now carry `Retry-After` (RFC 6585), so a script can wait
  the right amount instead of retrying blind.
- **PIN lockout bypass**: `client_ip()` trusted `X-Forwarded-For` from anyone,
  and the lockout is keyed on that value — so a LAN client could send a
  different fake address with every request and guess the PIN forever. The
  header is now honoured only when the connection itself comes from loopback
  (i.e. a reverse proxy on the same machine).
- **PIN lockout leaked between shares**: the attempt counters were module-level,
  so failing five times on one share locked the same client out of a *different*
  share started later in the same process. They live on the share now.
- **Tracker**: `event=stopped` was ignored, so a client that quit kept being
  handed to other peers for the full three-minute peer timeout, and the swarm
  counts stayed inflated. A stop now removes the peer immediately.
- **Multi-range downloads**: `_send_multipart` wrote a CRLF after every part
  but left those `2 * len(ranges)` bytes out of `Content-Length`. A client
  stopped reading at the declared length, so the closing delimiter *and the
  tail of the last range* were silently dropped — `aria2c -x16` and Safari,
  which both use `multipart/byteranges`, got truncated data.
- **Resume integrity**: the ETag was built from `int(mtime)`, so a file
  rewritten inside the same second kept its old validator — a client resuming
  with `If-Range` got a `206` and spliced new bytes onto old ones. The tag now
  uses `st_mtime_ns` and the inode, and a failed `If-Range` (stale or
  unparseable) correctly falls back to a full `200` per RFC 9110.
- **API authentication**: `_check_auth` called `self._set_auth_cookie()`, a
  method that does not exist, so every request authenticated with
  `X-LFS-PIN` — the way scripts and `curl -H` do it — died with a 500.
- **Browser uploads**: an uploaded file was written to
  `~/local-file-sharer/` (or `~` when no *Downloads* folder existed) instead of
  the folder being shared, so the person who invited the upload never saw it.
  `share.py` now passes the shared folder as `upload_dir`, and a traversal in
  `?name=` is covered by a test.
- **Web downloader**: the "16 connections" button awaited each `Range` request
  in a `for` loop, so it was 16 *sequential* requests wearing a parallel label.
  They now go out together via `Promise.all`, verified against a live server
  (16 in flight, byte-identical reassembly).
- **Python 3.9/3.10**: `config.py` imported `tomllib` unconditionally, so the
  package raised `ModuleNotFoundError` at import time on every version the wheel
  claims to support. It now falls back to `tomli` (a marker-guarded dependency),
  degrades to defaults when there is no config file, and `tools/check_compat.py`
  guards the AST in CI — there is no 3.9 interpreter to test with otherwise.
- **Docs screenshots**: every committed PNG shipped with tofu boxes where emoji
  belonged (the Twemoji tree was one directory too deep), and `to_svg` failed
  silently. It now warns per missing glyph, `tools/screenshots/check_assets.py`
  gates CI, and the picker shot no longer leaks an absolute sandbox path.

### Added

- `tests/test_e2e.py` — every share mode driven over a real socket, including a
  real BitTorrent `announce` and ranged webseed reads.
- `tests/test_platforms.py` — the Windows drive mask, clipboard chains,
  `PATHEXT` lookup and per-OS firewall hints, all without leaving Linux.
- `tools/check_fish.py`, `tools/screenshots/` (the README image pipeline).

### Planned
- WebRTC data channels for NAT-to-NAT transfers
- Resumable browser uploads
- QR pairing codes that prefill PIN + expiry

## [1.0.0] — 2026-11-01

The first stable release: one command, four share modes, a real transfer engine.

### Added

**Sharing**
- `start` — one command that opens the interactive menu, or shares the paths you
  pass on the command line.
- Four modes: **download** (forced + resumable, folders auto-zipped), **serve**
  (web file browser), **stream** (media player with seeking) and **torrent**
  (`.torrent` + `magnet:`, built-in HTTP tracker and BEP-19 webseed).
- Optional PIN gate, auto-expiring links and browser uploads back to the host.
- One-key clipboard copy of links and one-liners, `--open`, `--json`.

**File picker**
- Scroll-friendly, never clears the screen, fits any terminal width, inline
  icons, no *Type* column, Windows drive letters.
- Fuzzy search (`/`), number ranges (`6,7,8` / `6-8`), select-all, favourite
  folders, recents, and a live summary of files, subfolders, size and
  shareability.

**Transfer engine**
- `os.sendfile` zero-copy on Linux/macOS/Termux, `socket.sendfile` on Windows.
- 4 MiB socket buffers, `TCP_NODELAY`, `TCP_WINDOW_CLAMP` where supported.
- HTTP `Range`, `multipart/byteranges`, `ETag`/`If-Range`, `HEAD`, and a
  16-connection parallel download button in the web UI.
- Streaming ZIP64 with deterministic offsets — a resumed folder download is
  byte-identical to an uninterrupted one.
- Live statistics (`/api/stats`) powering the terminal dashboard and the web
  UIs.

**CLI**
- Animated gradient banner with three themes (**dark**, **light**, **neon**).
- Live transfer dashboard: speed, ETA, per-file progress, connected devices,
  total sent — redrawn in place, never clearing the screen.
- `start doctor` — dependencies, tools, sendfile, buffers, LAN/public IP,
  firewall, tunnels, config, clipboard, with friendly fixes instead of
  tracebacks. `--json` for CI.
- `start config show|init|path|get|set|reset` (TOML, env overrides,
  `config.local.toml` for secrets).
- `start theme`, `start completions fish|bash|powershell|zsh`, `start banner`,
  `start --update`, `start version/help`.

**Network**
- LAN/Wi-Fi address detection with interface *kinds*, preferring Wi-Fi over
  Ethernet and ignoring virtual bridges.
- Public IP lookup in a background thread.
- Tailscale auto-detection with `serve`/`--funnel` prompts.
- Cloudflare quick tunnels with an explicit slow-speed warning.

**Web UIs**
- Download station, file browser and media player, matching the product design
  system; cache disabled (`no-store`), no CDN, works fully offline.

**Packaging & docs**
- `pip`/`pipx` install with a `start` console script (`local-file-sharer` alias).
- `install.sh` (Termux/Linux/macOS) and `install.ps1` (Windows) with fish
  integration, plus `shell/windows/share.cmd` because `start` is a shell built-in.
- GitHub Actions: ruff, pytest on Linux/Windows across Python 3.9–3.13, and
  tagged releases that attach a standalone Windows `.exe`.
- README with screenshots, feature table and troubleshooting; CONTRIBUTING,
  SECURITY, issue/PR templates; MIT license.

[Unreleased]: https://github.com/Erawat4l/local-file-sharer/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Erawat4l/local-file-sharer/releases/tag/v1.0.0
