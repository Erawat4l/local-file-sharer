# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- The README's install section is a third of its former length. It opened with
  a long branch on the repository being **private** — a first-time reader had
  to work out which of three situations applied before typing anything — but
  the repository is public, so most of that was a second audience reading
  documentation for a situation that applied to neither of them. The section is
  now: one-liner, clone, pipx, update, Termux, Windows, stale-copy, source. The
  `gh repo edit --visibility` recipe, the "you may not have access" path and the
  "test the link while logged out" note are gone with the condition they
  described.
- The table of contents was pointing at four sections that no longer existed
  (`#if-you-just-want-it-running`, `#the-three-commands`, `#one-file-no-git-no-url-to-type`,
  `#linux--macos`) and at two anchors that never resolved
  (`#platform-support`, `#shell-integration--completions`). All 33 internal
  links now resolve.

### Fixed
- Torrent mode produced a `.torrent` that only worked while the share was
  running. The file named this machine's own tracker and its own HTTP webseed,
  so handing it to somebody else and closing the share left them with a dead
  link in a `.torrent` costume. The file now carries public **DHT bootstrap
  nodes** and public fallback trackers, so their client joins the global DHT
  and can find peers with nothing of yours running. `private=True` still
  produces a swarm-only torrent with neither.
- The `.torrent` path was never printed: it was written to `~/Downloads` and
  the panel showed only the magnet, so the file you asked for was invisible.
  It is now the first line of the torrent panel, with its size and piece count.
- The torrent panel claimed the webseed "pulls pieces straight from this
  device", which was true and useless — it described a download that stops when
  the share does. It now says whether the file outlives the share.
- Every sub-folder on a nested page read `0 files · 0 B` even though its files
  were on disk: the row's path was built by joining the level being listed with
  a trail that already contained it, so it asked for `Root/Level/Level/Child/`
  and matched nothing. The count and size now cover what the folder holds, and
  its zip downloads those files instead of answering 404.
- A large download stopped partway through and never resumed. Three causes, all
  on the transfer path: saving to disk collected every range into memory before
  writing any of it, so a multi-gigabyte file was resident in RAM in full
  before a byte landed; one dropped range made the page navigate away and
  restart the file from zero; and a 12 GB share spent its zip preparation
  deflating files before the first byte of the response went out. Ranges are
  now written as they arrive, a failed range is retried with backoff, and the
  compression budget is checked before a file is read.
- On Android the share did nothing to stop the device sleeping. A minute or two
  after the screen went off the CPU is suspended and every transfer freezes,
  with no error in the log because nothing failed. A share now takes a wake
  lock while it runs (`termux-wake-lock`), says so in the summary panel, and
  tells you to install Termux:API when the helper is missing. `lfs doctor`
  reports the state on a phone.
- Picking two folders and a file showed the file and neither folder: every
  path was stripped of its first segment, so `ADM/a.txt` became a loose
  `a.txt` at the top of the page and the folder card was never drawn. Folders
  are folders again, and each one drills down to its own contents. The count
  and size on each card cover everything inside it.
- `/browse/<folder>` and `/folder/<folder>` answered 404 for a folder that was
  picked in its own right, so the link the page had just drawn could not be
  opened or downloaded.
- Ctrl-C at a prompt is a cancellation, not a crash. Interrupting "generate a
  temporary public Cloudflare link?" -- asked after the share is already
  serving -- printed an "Unexpected error" panel and exited 1; it now reads as
  "no" and the share keeps running. A second ^C while reporting exits quietly
  instead of dumping a traceback.

### Added
- `./run` — clone the repository, `cd` into it, run one command. The first run
  builds a private `.venv`, installs the package in editable mode and starts
  `lfs`; later runs go straight to the app. `./run upgrade` is `git pull` plus
  re-install, and a plain `git pull` is enough because the install is editable
  and `.venv` notices the new commit. Nothing is written outside the checkout.

### Changed
- The CLI menu is grouped into SHARE / RECENT & FAVOURITES / SETTINGS, with a
  key gutter, an icon column and one aligned description column — the layout
  from docs/assets/demo.gif. Quit moved into SETTINGS next to Help.
- Every web page shows a build stamp next to the version, so a stale copy of
  the app is obvious at a glance instead of looking like a broken feature.

### Changed
- The player's controls are icons, not words, and fit one row on a phone.
- The player stage takes the video's own shape: a 2.39:1 film fills a wide
  frame instead of sitting in black bands, and a 9:16 short fills a tall one.
  The aside shows the real resolution (e.g. `1920×804 · wide`).

### Fixed
- Tapping the picture pauses nothing now: a tap shows and hides the controls,
  and a second tap brings them back. The old handler paused the video instead,
  and the controls only reappeared on a 2.2s timer.
- The mute button no longer replaces its icon with the word "mute".
- `lfs upgrade` advances off "contacting GitHub" as soon as pip prints
  anything, so a slow clone no longer looks like a hang, and `--verbose` shows
  pip's own output when the bar is not enough.
- Parallel downloads use 8 connections instead of 16: measured against this
  server, throughput peaks at 4-8 and drops by half by 16.

### Fixed
- `lfs upgrade` froze forever at `0/5 contacting GitHub`. pip handed the terminal
  to git, git asked for a username and password, and nothing was reading stdin:
  on a private repo that prompt never returns. stdin is now closed and pip and
  git are both told not to ask.
- A private repository reports "Repository not found", the same message a
  mistyped URL gives. The upgrade now names the likely cause instead of leaving
  the user to decode pip's subprocess traceback.
- `pipx install --force` beside an existing copy leaves two `lfs` commands on
  PATH, and only one of them gets upgraded. The upgrade now says which copy it
  is updating and which other one exists.

### Added
- `tools/make_installer.py` builds a single-file installer that carries the
  source inside it. The repository is private, so `pip install git+…` dies with
  "Repository not found" on a device without credentials — which is
  indistinguishable from a typo'd URL. The installer needs no git and no token:
  `python3 install-lfs.py`.

### Fixed
- Videos and audio inside a folder now open the player instead of downloading.
  Every row pointed at the raw bytes, so opening a movie from a folder saved it.
- A file this browser cannot decode — HEVC, which is what most phones record,
  and DivX — showed a black rectangle with a dead Play button and a toast that
  vanished after 1.6s. The player now names the codec and offers Download.
- The file card body on the landing page was a dead `<div>`; media cards open
  the player when tapped.
- `lfs upgrade` installed nothing, silently, on every run after the first. pip
  compared version numbers, every commit of this package reports the same one,
  so it called the requirement already satisfied and exited 0. The install is
  now forced, and the recorded commit is compared before and after so a no-op
  is reported as "nothing changed" instead of "upgraded".

### Changed

- **`lfs upgrade` is a progress bar, not a wall of pip output.** It used to hand
  the terminal to pip: thirty lines of collect/clone/resolve/build chatter, then
  the `python -m pip install …` command line echoed back. The command is now run
  with its output captured, and one line tracks the real stages —
  `upgrading ███████████░░░░░ 2/5 building` — advancing on pip's own phase
  markers (`cloning`, `preparing metadata`, `installing collected packages`)
  rather than a guessed timer. Output is shown only when the upgrade fails,
  where the reason matters. The command line is no longer printed, and the bar
  is skipped entirely when output is piped so logs stay clean.

### Fixed

- **The Termux dashboard no longer stacks.** A panel taller than the window made
  rich scroll instead of repaint, so a short Termux window filled with
  half-cut copies of the dashboard — cut-off progress bars, repeated headers and
  no footer. The panel is now built against a real row budget: sections are
  added only while they fit, the order of sacrifice is fixed (transfers, then
  links, then the device table, then the footer), and `vertical_overflow` is
  `crop` so nothing can spill. Checked in a VT100 emulator at 12, 15, 18, 24 and
  40 rows: exactly one header, bottom border always visible.
- **The dashboard is readable on a phone-width terminal.** The stats row was
  four fixed columns, so a 60-column window showed `sent …` and `eta 2…`.
  Narrow windows now drop the peak and goal columns instead of truncating all
  of them. Every width from 52 to 120 renders with no line over the edge.
- **`{{pager}}` can no longer reach a browser.** A template placeholder that
  nobody substituted is now blanked at send time, so a missed substitution shows
  as an empty slot rather than literal template syntax on the page.
- **The progress figure matches the browser.** `start_file` re-registered the
  file on every request, so the sixteen range requests of one download kept
  resetting `sent` to zero and fighting each other — the browser said 98% while
  the dashboard said 42%. Progress is now kept across ranges and counted once
  per file, and finished files no longer drag the percentage down.
- **A connected device is not dropped for being quiet.** A 16-stream download
  sends nothing between ranges and a browser fetching one file makes no other
  requests, so the status line fell to "0 devices" mid-transfer even though the
  request count was climbing. The idle window is 45 s and is measured from
  every request, not only from bytes.
- **The speed readout stops when the transfer does.** After a download
  finished, the last measured value was held forever, so the dashboard showed a
  frozen "304 MB/s" next to a completed file — which reads as a transfer that
  never ends. Speed is reported as zero after 4 s without new bytes.
- **The page scrolls on Android.** `body` was given `min-height:100dvh` plus
  `overflow-y:auto`, which makes the body its own scroll container that never
  moves; a finger drag then has nothing to grab. The document scrolls now, on
  both the listing and the player, and `overflow-x: clip` keeps a wide child
  from turning the page into a sideways canvas.
- **Downloading a folder no longer saves an HTML page.** "Download selected"
  fetched each row's `href`, and on a folder listing that href is the browse
  page — so selecting three folders saved `Camera.html`, `Screenshots.html` and
  `WhatsApp Images.html` instead of the archives. Rows and cards now carry the
  real download URL in `data-dl` and its filename in `data-filename`, so the
  browser cannot fall back to `.html`. Verified in a browser: folder zips,
  file rows, grid cards and "Download selected" all save real content, with
  zero HTML files. The folder name still opens the folder; the ↓ cell downloads
  it.
- **The web page shows folders instead of every file at once.** A share of a
  phone was rendering *all* nested files on the front page — 17,001 cards in a
  single response (~10 MB of HTML, 50,000+ DOM nodes), which is why the listing
  looked shattered and would not settle. Each level now renders only its own
  children: one card per sub-folder (with its file count and size) plus the
  files that actually sit in that folder. The same 17,001-file tree is now a
  4.1 KB page with 7 cards.
- **Large folders are paged** (60 cards per page, `?p=N`) with prev/next links,
  so a folder holding thousands of files stays fast to open and to scroll
  instead of dumping everything at the browser. A page past the end clamps to
  the last page.
- **The broken row layout.** Rows emit four cells into what was a three-column
  grid, and the page script prepends a checkbox as a fifth, so names were
  squeezed into the size column and every row wrapped onto two lines. Columns
  are explicit now, the name takes the remaining space, and full folder names
  are visible again instead of being cut to "Cam" / "Scre".
- **"This folder is empty" no longer appears under a populated listing.** The
  flag was inverted, so every non-empty folder showed the empty-state note.
- **Share links under a shared folder point at the right place.** Card links and
  folder-zip URLs were built from the level's display name, which would have
  asked for `/folder/<share>/<sub>` instead of `/folder/<sub>`.

### Performance

- **Downloads use the whole link instead of one TCP stream.** A single stream is
  capped by the bandwidth-delay product, not by the Wi-Fi: on an emulated
  40 MB/s link with 8 ms latency, one stream measured **3.8 MB/s** while
  sixteen measured **34 MB/s**. That is the "2.5 MB/s even on the same Wi-Fi"
  report. Per-file downloads now split the file into ranges and fetch them
  concurrently, and where the browser supports the File System Access API the
  pieces are written straight to disk, so a multi-gigabyte video downloads at
  link speed without buffering the whole thing in the tab. Files over 64 MB
  fall back to a normal single download on browsers without that API rather
  than risking the tab.
- **Socket buffers are no longer silently capped.** The code asked for a 4 MiB
  `SO_SNDBUF`, and Linux quietly granted about 400 KB because of
  `net.core.wmem_max` — the request was never checked. It now reads the
  kernel's real ceiling and retries there, and skips `TCP_WINDOW_CLAMP` when
  the granted buffer is small, since clamping to a small buffer *throttles*
  the connection rather than tuning it.
- **The advertised address prefers a real private IP.** `169.254.x.x` is a
  link-local address a device gives itself when DHCP failed; it was being
  promoted to the address shown for sharing even though the rest of the code
  filters it out. It is now a last resort.
- **The speed readout no longer lies.** A flush carrying zero bytes used to
  skip the sample, which froze the running total while time kept passing, so
  the displayed speed decayed towards zero mid-transfer; and any span under
  50 ms read as exactly 0. Speed is now sampled on every flush, clamped to a
  physical ceiling, and never reports 0 while bytes are moving.
- **The picker no longer re-scans every sub-folder it lists.** Building a row
  probed each directory with a second `scandir` to decide whether it was
  readable — an extra real read per folder, on every navigation, on Android
  storage where reads are slow. Readability now comes from the `stat` already
  being taken. Measured with FUSE-like latency: **520 ms → 140 ms** to list 120
  folders. The red "locked folder" cue is unchanged.
- **One `stat` per entry instead of two** in the picker (the second was only
  fetching the symlink bit).
- **`tailscale status` is cached for 5 s.** The menu redraws after every
  keypress and each redraw shelled out to `tailscale version` and
  `tailscale status --json` — around 800 ms per screen on a phone. Cached
  re-renders: **800 ms → 0 ms**; `doctor` and explicit checks still ask fresh.

### Documented

- **Audio tracks and subtitles in the web player** — no, not today, and the
  reason is the browser, not the app. Measured: a real MKV and MP4 carrying two
  AAC tracks and two SubRip tracks play, but Chromium reports
  `audioTracks`/`videoTracks` as `undefined` and `textTracks` as 0. Chrome never
  implemented those collections; Firefox gates them behind
  `media.track.enabled`; Safari exposes them. There is no standard way for a
  page to list a file's tracks. `docs/MULTI-TRACK-AUDIO-SUBS.md` records the
  measurement, the three implementation routes with their real costs (sidecar
  subtitles as WebVTT is the cheap one and gives a native picker; ffmpeg remux
  gives audio switching but needs its own menu; MSE demux in JS is 1.5k–3k
  lines and wrong for a phone), and the Termux caveats.
- **`doctor` no longer claims ffmpeg does "media remuxing".** It is detected but
  never called, so the line now says "not used yet".

### Added

- **The web has real search and type filters.** The box now matches anywhere in
  the name *and* the type — `mp4` finds `holiday clip.mp4`, `MP4` matches too,
  `video` finds it without knowing the extension — and every word must match,
  so `sad mp4` narrows. A row of chips filters by Video, Images, Audio,
  Documents and Archives; the empty state says which of the two hid everything.
  File kinds collapse into those six buckets server-side (`data-kind`), because
  one chip per format would be nine chips on a phone.

### Fixed

- **Downloads go to `/sdcard/Download`, and the picker opens on `/sdcard`.**
  The picker started wherever it happened to resolve, and the download folder
  never considered Android storage at all — its candidate list was
  Termux-home paths only, so every download landed in
  `/data/data/com.termux/files/home`, which no other app on the phone can see.
  `/sdcard` is now tried as an alias (some builds only expose `~/storage`), and
  `downloads_dir()` prefers `<storage>/Download`. Neither is gated on `TERMUX`
  any more: the probe just checks whether the folder can be listed, so a plain
  Linux box falls through unchanged.
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
