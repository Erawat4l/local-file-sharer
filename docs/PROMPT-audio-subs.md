# Prompt for another agent: audio tracks & subtitles in the web player

Copy everything below the line into a fresh session. It is self-contained.

---

## Context

Repository: `Erawat4l/local-file-sharer`, branch **`arena/d7f4713b-local-file-sharer`**
(the branch carries the package; `main` is a stub README with no
`pyproject.toml`, so never install from `main`).

LOCAL FILE SHARER (`lfs`) is a single-file / folder sharing tool for Termux,
Linux, macOS and Windows. It runs an HTTP server (`ThreadingHTTPServer`, range
requests, `os.sendfile`) that serves a browser UI. Relevant files:

- `src/local_file_sharer/transfer/http_handler.py` — routing, the player page
  (`page_stream`), file cards, `_filter_kind`, `_icon_for`.
- `src/local_file_sharer/web/assets/player.html` — the player. A plain
  `<video id="player" src="/f/<file_id>">` plus custom controls, fullscreen and
  a playlist.
- `src/local_file_sharer/transfer/manifest.py` — `FileEntry`, `kind_of`,
  `guess_mime`, the extension tables.
- `src/local_file_sharer/platforms.py` — `find_tool()` (finds `ffmpeg`),
  `start_dir`, `downloads_dir`, `sdcard_dir`.
- `docs/MULTI-TRACK-AUDIO-SUBS.md` — the investigation this prompt is based on.
  **Read it first.**

## The problem

Playing a movie in the web player gives no way to change the audio track or turn
subtitles on. A file with 2 audio tracks and 2 subtitle tracks plays with
whatever the browser picks and nothing else.

## What has already been measured — do not re-litigate

An MKV and an MP4 were built with ffmpeg carrying two AAC audio tracks
(`eng`, `hin`) and two SubRip subtitle tracks, served through `lfs`, and
inspected in Chromium 153:

| API | Result |
|---|---|
| `video.audioTracks` | `undefined` |
| `video.videoTracks` | `undefined` |
| `video.textTracks.length` | `0` |

Both files **play** (8.0 s duration reported). Track enumeration is absent.

Causes, confirmed:

1. Chrome has never implemented `audioTracks`/`videoTracks`. Firefox implements
   them but ships `media.track.enabled = false`. Safari exposes them. There is
   no standard way for a page to enumerate a file's tracks.
2. `textTracks` is 0 because the page never creates a `<track>` element. A
   `<video>` will not discover embedded subtitle streams; Chrome cannot render
   SubRip from inside a container — it needs WebVTT at a separate URL.

## Task

Implement subtitle support first, then audio switching if the first lands
cleanly.

### Phase 1 — sidecar subtitles (do this first)

For every video in the share, find sibling subtitle files with the same stem:
`movie.srt`, `movie.en.srt`, `movie.hi.srt`, `.vtt`, `.ass`, `.ssa`.

- Convert SRT/ASS/SSA to WebVTT on the fly (SRT: replace `,` with `.` in
  timestamps, drop the numeric index lines, prepend `WEBVTT`. ASS: strip
  styling, convert `Dialogue:` lines to cues).
- Serve each at a route like `/subs/<file_id>/<index>.vtt` with
  `Content-Type: text/vtt; charset=utf-8`.
- Emit `<track kind="subtitles" srclang="en" label="English" src="…">` per
  subtitle, defaulting the first `on` only if there is exactly one.
- Label from the language tag (`en` → English) and fall back to the file's
  suffix. Use `label=` — the browser's own caption menu and CC button then work.

Acceptance criteria:

- A video with `movie.en.srt` and `movie.hi.srt` next to it shows two entries in
  the browser's native caption menu and the CC button toggles them.
- Timestamps line up (a cue at 00:00:04,000 appears at 4 s).
- The `.vtt` route is `text/vtt`, and no `.srt` is sent with an HTML content
  type.
- A share with no subtitle files renders exactly as it does today.

### Phase 2 — audio switching (only after phase 1 is merged and tested)

`<video>` cannot report its tracks and Chrome cannot switch them. Two viable
routes; pick one and justify it in the commit message:

**A. ffmpeg remux to per-track URLs (server-side).**
`ffmpeg -i in.mkv -map 0:a:1 -c:a copy -f mp4 -movflags frag_keyframe+empty_moov -`
and serve at `/audio/<file_id>/<index>`. The player fetches a track list from a
JSON endpoint and builds its **own** audio menu (the browser gives none), then
re-assigns `v.src` with `#t=<currentTime>` to stay in sync. Probe with
`ffprobe -show_streams`, lazily on first play, cached by `(path, mtime, size)`.
Note: `-c:a copy` is fast, but a 4 GB file on a phone is still real work.

**B. MSE remux in the browser.** `fetch()` the file, demux with `mp4box.js` (MP4)
or a Matroska parser, one SourceBuffer per chosen track; switching = `abort()` +
`remove()` on the audio buffer, append the new init segment, refill. Works with
no server change but is ~1.5k–3k lines and pipes every byte through JS.

Preference: **A**, behind a config flag, with a clear fallback when ffmpeg is
absent. Termux users need `pkg install ffmpeg` (~60 MB).

## Constraints

- Match the existing code: monochrome UI, no emoji (the web had a tofu-glyph
  bug from emoji with no font — use inline SVG, `_icon_paths` / `icon_svg`),
  ruff with `line-length = 110` and `E501` disabled, `ruff format`.
- Every new behaviour needs a test. The suite is PTY-driven where it matters;
  `pytest` needs `PYTHONPATH=src`. `tests/test_screenshots.py` skips without
  fontTools.
- Do not break Range requests or the `sendfile` fast path for the *original*
  media URL.
- Verify in a real browser, not just by reading the HTML. Chromium is available
  in this sandbox via npm `@sparticuz/chromium@153` (unpack `chromium.br` and
  `al2023.tar.br` with brotli; run with `LD_LIBRARY_PATH` pointing at the
  unpacked `lib`).
- Update `CHANGELOG.md` and `docs/MULTI-TRACK-AUDIO-SUBS.md` (tick off what is
  implemented, correct anything that turns out to be wrong).

## Pitfalls found already — do not rediscover these

- `state.last_dir` used to be validated with `is_dir()`, so an unreadable `/`
  counted as valid. Use `platforms.start_dir()`.
- Pressing `b` into an unlocked-but-unreadable folder used to kill the picker;
  navigation goes through `enter_dir()`.
- `lfs upgrade` must not install from `main` (no `pyproject.toml`); see
  `resolve_ref()` and `ref_has_package()` in `updater.py`.
- `/api/manifest` IDs and the IDs rendered into page HTML can differ per
  request; take stream URLs from the rendered page in tests, or `/f/<id>` 404s.
- The player's `<video>` has no `src` in the template; JS sets it from a
  `const src = '/f/…'`.
- `gh api ... -H "Accept: application/vnd.github.raw"` is how to read a file
  from this private repo; `raw.githubusercontent.com` returns 404 without a
  token.

## Definition of done

- Sidecar subtitles work in a real browser, verified by screenshot and by
  asserting cues appear at the right time.
- Audio switching either works behind a flag or is explicitly documented as
  deferred, with the reason.
- `pytest` and `ruff` clean; `CHANGELOG.md` updated; pushed to
  `arena/d7f4713b-local-file-sharer`.
