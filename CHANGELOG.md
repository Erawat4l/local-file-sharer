# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Install instructions no longer return 404.** Every documented install
  command used `raw.githubusercontent.com`, which answers 404 to any
  unauthenticated request while the repository is private — including for files
  that exist, and including `main`. `curl … | bash` therefore failed with
  `curl: (22) … error: 404` before it ever reached the script. The README now
  leads with `pipx install "local-file-sharer @ git+https://…@<branch>"`, which
  works without a token (verified with a stripped environment and no
  credential helper), and documents the authenticated
  `gh api … -H "Accept: application/vnd.github.raw" | bash` form for running
  the installer itself. Both installer headers say the same.
- **`lfs upgrade` / `lfs update` can actually install.** The default branch was
  `main`, and `main` in this repository holds nothing but a 19-byte README — no
  `pyproject.toml`. So every upgrade ran `pip install git+…@main` and died with
  *"does not appear to be a Python project: neither 'setup.py' nor
  'pyproject.toml' found"*. Worse, a copy installed from `main` reported
  `installed_ref() == "main"` and therefore retried the same broken branch
  forever. `lfs upgrade` now asks the API whether the branch has a
  `pyproject.toml` before installing, redirects to a branch that does (saying
  why), and defaults to `arena/d7f4713b-local-file-sharer`. An explicit
  `--ref` is still honoured, with a warning if it cannot work. Offline, the
  check is skipped rather than blocking the install.
- **`lfs update` works.** It was not a registered command, so `update` was read
  as a *path* and fell through to the share path, which answered
  "Nothing to share" — a baffling way to say "no such command". It is now an
  alias of `lfs upgrade` and shares its parser.
- **Bare `lfs` shows the menu again.** It jumped straight into the file picker,
  so the five modes (Download, Serve, Stream, Torrent) were hidden behind a
  screen that only offered "pick files". `lfs --picker` jumps in directly, and
  `1` on the menu goes straight to the picker as before.
- **The picker opens in your storage, not a locked system folder.** The start
  folder was `state.last_dir or storage_root()`, and `last_dir` was validated
  with `is_dir()` — so a remembered `/` or `/data/data/com.termux` counted as
  valid (they are directories; they just cannot be listed) and beat real
  storage. `platforms.start_dir()` now probes by listing and skips anything
  unreadable, falling through to `/storage/emulated/0`, then `~/storage`, then
  home.
- **Pressing `b` into a locked folder no longer closes the picker.** Going up
  from `/data/data/com.termux` lands on `/data/data`, which Android will not
  list; that exception escaped the key loop and ended the session, losing the
  selection and showing a full error panel for pressing "up". All navigation
  (Enter, `b`, Backspace, `h`, `g`) now goes through one `enter_dir()` helper
  that stays put and flashes the reason.

### Changed

- **The main menu is five rows.** It listed twelve labelled actions, which read
  as a control panel rather than an answer to "what do you want to share?".
  Download, Serve, Stream, Torrent and Quit remain; Recent, Favourites, Config,
  Doctor, Theme, Update and Help moved behind `?`. Their keys still work
  directly. Descriptions are dropped below 70 columns instead of wrapping,
  because a wrapped description doubled the height of every row on a phone.
- **The file picker's footer is five hints, and one of them is "up".** The old
  footer listed nine pairs, so a narrow terminal silently dropped whole pairs
  and `b` (go back a directory) was never shown at all — the single most common
  question. It now reads `arrows move · Enter open · Space pick · b up ·
  s share`, and Backspace goes up too. The empty-folder hint points at `b`.
- **The picker no longer opens on an unreadable folder.** On Termux
  `/storage/emulated/0` is a directory long before `termux-setup-storage` has
  been run, so `storage_root()` returned it and the picker printed
  `! Permission denied: /` before falling back. It now probes by listing.
- **No emoji anywhere in the web UI.** The broken glyphs ("all is shattered")
  were tofu boxes: the pages used emoji for icons and the phone's browser had no
  emoji font for them. Every one is now an inline SVG — clock, people, download,
  zip, copy, bolt, lock, house and upload — including the two generated in
  Python (the breadcrumb house and the upload dropzone), which the earlier
  template sweep missed.
- **Web buttons stack into one column on a phone.** Wrapped inline buttons came
  out ragged — "Download all" short, the zip button longer, both left-hugging —
  and the phone had no room for "· 16 connections" on the label.

### Added

- **`lfs upgrade`**: installs the newest code from GitHub, from the same branch
  this copy came from (`--ref` overrides). This is the update command that
  works: the package is not on PyPI and the repository has no release tags, so
  `lfs --update` — which compares against a GitHub *release* — could only ever
  report `404`, and the "update manually with `pipx upgrade local-file-sharer`"
  hints pointed at a package PyPI does not have. `--update` now says so and
  points at `lfs upgrade`. It uses `pipx install --force` or
  `pip install --upgrade` (retrying with `--break-system-packages` on
  Debian/Ubuntu) and refuses to replace a standalone `.exe`.

- **Media player**: a seek preview shows the time under the cursor before you
  commit, volume and position are remembered across reloads, tapping the
  picture toggles playback, and ↑/↓ adjust volume.

### Changed

- **Monochrome UI**: the neon cyan/violet palette is gone from both faces of
  the app. The terminal now ships **dark**, **light** and **mono** themes —
  greys for structure, white (or black) for the accent — and the `neon` theme
  is retired; an existing `theme = "neon"` config falls back to `dark`. The web
  pages and the media player use the same black/white palette, with contrast
  instead of hue.
- **The download page lists folders, not their contents**: a share used to
  flatten into one card per file, so a folder of 400 photos became 400 cards.
  Sub-folders are now one card each (file count + size) with their own zip
  button, and only files sitting in the share root are listed individually.
- **Per-folder download**: `/folder/<name>` streams a zip of one folder, rooted
  at its own contents, so downloading "Photos" no longer pulls the whole share.

### Fixed

- **Arrow keys did nothing in the picker.** `_read_key()` read stdin through
  Python's buffered text stream, which swallows the whole `ESC [ B` into its
  own buffer and hands back one character — so the follow-up `select()` on the
  fd saw nothing pending and the code decided a lone ESC had been pressed. The
  cursor never moved on Linux, Termux or macOS. Bytes are now read unbuffered
  with `os.read()` in cbreak mode, and the tail of the sequence is collected
  with a timeout long enough for a split one (ssh, tmux, Termux's pty). All
  eight arrow/PgUp/PgDn/Home/End keys are covered by a PTY test that fails on
  the old code.
- **`lfs` opened a prompt, not the picker.** A bare `lfs` printed the menu and
  asked "what would you like to do?" in line mode, so nothing responded to an
  arrow key until you typed `1` and pressed Enter. It now goes straight to the
  file picker, and falls back to the menu only when the picker is cancelled.
  `lfs --menu` asks for the menu explicitly.
- **Mangled footer and header.** Key hints were one `Text`, so Rich soft-wrapped
  them mid-phrase — `h` ended one line and `home` sat alone on the next, and a
  narrow terminal truncated labels to `Space pi…`. Hints now wrap whole pairs
  onto their own rows, ordered most-useful-first so a narrow screen drops a
  complete pair rather than a fragment, and the picker's item counter moves
  under the title instead of colliding with it below 62 columns.

- **Scrolling**: the player forced `height: 100vh` on a grid and the page set
  `min-height: 100%` on `html`, which pinned the document and made long pages
  unscrollable on a phone. The document now owns the scroll (and uses `100dvh`).
- **Fullscreen**: fullscreen put the *stage* in a box still sized by the 100vh
  grid row and still sharing the page with the 330px sidebar. Fullscreen now
  drops the padding, border and fixed row, paints a black `::backdrop`, keeps
  the controls reachable, and falls back to native video fullscreen on iOS.
  The help text and keyboard hints are hidden there, and the controls fade out
  after 2.2 s of playback (any pointer movement brings them back, cursor too) —
  verified against a real 30 s 720p clip: the element measured 390×844 on a
  390×844 viewport, controls stayed reachable and the page stopped scrolling.
- **Emoji in the UI**: every emoji is gone — the terminal uses single-cell
  glyphs (a double-width emoji in a phone terminal shifted every row and often
  rendered as tofu), and the web pages use inline `currentColor` SVG for file
  kinds, folders, the download arrow and the play/pause glyph.

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
- **A file deleted mid-archive was replaced with zeros**: if a file vanished
  before its turn in a streamed ZIP, the entry was padded with NULs to keep the
  archive valid — so the download had the right size, a *correct CRC* and
  fabricated content. Files that are already gone at request time are now left
  out of the archive; a file that disappears after the stream is laid out keeps
  its size (so resume still works) but carries a readable marker saying it
  vanished, instead of a silent block of zeros.
- **Transfer counters are trustworthy**: verified that `bytes_sent` equals the
  file size exactly, that a `Range` adds only its own bytes, and that 20
  concurrent downloads produce exactly 20 × size with 20 requests. A test now
  pins this, since every download path funnels through the tracker and a lost
  update would only show up as a slightly wrong number.
- **Expiry surface is pinned too**: after expiry only `/static/app.css` answers
  from the content routes — the 410 page needs it to look right —
  so nothing can start serving a dead link. (`/healthz` and `/api/expired`
  stay available for monitoring; `/api/expiry`, the countdown, does not.)
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
- **`lfs … --json` now keeps stdout clean**: the banner and the "share ready"
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
- `lfs` — one command that opens the interactive menu, or shares the paths you
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
- `lfs doctor` — dependencies, tools, sendfile, buffers, LAN/public IP,
  firewall, tunnels, config, clipboard, with friendly fixes instead of
  tracebacks. `--json` for CI.
- `lfs config show|init|path|get|set|reset` (TOML, env overrides,
  `config.local.toml` for secrets).
- `lfs theme`, `lfs completions fish|bash|powershell|zsh`, `lfs banner`,
  `lfs --update`, `lfs version/help`.

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
- `pip`/`pipx` install with a `lfs` console script (`local-file-sharer` alias).
- `install.sh` (Termux/Linux/macOS) and `install.ps1` (Windows) with fish
  integration, plus `shell/windows/share.cmd` because `lfs` is a shell built-in.
- GitHub Actions: ruff, pytest on Linux/Windows across Python 3.9–3.13, and
  tagged releases that attach a standalone Windows `.exe`.
- README with screenshots, feature table and troubleshooting; CONTRIBUTING,
  SECURITY, issue/PR templates; MIT license.

[Unreleased]: https://github.com/Erawat4l/local-file-sharer/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Erawat4l/local-file-sharer/releases/tag/v1.0.0
