# Audio tracks and subtitles in the web player

Status: **not supported today.** This note records what the browser actually
does, what the server already has, and what a real implementation would cost —
so nobody re-litigates it from scratch.

## What works now

Pressing `s` in the picker shares the selection and starts the server; `1` then
`s` on the menu does the same in Download mode. The link opens a page that lists
the files, and `/s/<id>` opens the player.

The player is a plain `<video src="/f/<id>">`. That gets you:

- play/pause, seek, volume, speed, and true fullscreen;
- HTTP Range seeking, so scrubbing a large file is instant;
- any track the browser happens to pick as *default*.

That is the whole feature set. Everything else in the container is ignored.

## What the browser actually does — measured, not assumed

A test file was built with ffmpeg containing two AAC audio tracks
(`eng` "English 5.1", `hin` "Hindi Dub") and two SubRip subtitle tracks, in both
`.mkv` and `.mp4` containers. Chromium 153 was then asked what it could see.

| API | Result |
|---|---|
| `video.audioTracks` | **`undefined`** |
| `video.videoTracks` | **`undefined`** |
| `video.textTracks.length` | **0** |

Both containers played (8.0 s duration reported). Both reported zero selectable
tracks.

This is not a bug in LOCAL FILE SHARER, and not something a bigger player or a
cleverer `<video>` tag can fix:

1. **`audioTracks` / `videoTracks` are not available in Chrome, and in Firefox
   only behind a preference.** Chrome has never implemented them. Firefox
   implements them but ships with `media.track.enabled = false`, so they are
   `undefined` unless a user edits `about:config`. Safari exposes them, which is
   why multi-audio "works" there and nowhere else. There is no standards-track
   way for a page to enumerate a file's audio tracks.
2. **`textTracks` only reaches 0 because no subtitle was added.** The container
   has them; the page never creates a `<track>` element, and a `<video>` will
   not discover embedded subtitle streams by itself. Even if it did, Chrome
   cannot render SubRip from inside a container — it needs WebVTT served as a
   separate URL.
3. **MATROSKA (`.mkv`) is not a web format.** Chrome plays the parts it can
   decode but offers no track enumeration. Most `.mkv` in the wild is
   H.264/AAC inside Matroska, which is why "it plays" is true while "I can pick
   a track" is not.

So: **no**, right now you cannot change the audio track or turn on subtitles.
Not because the feature was skipped, but because `<video>` has no standard way
to enumerate them.

## What the server already has

- **ffmpeg is detected but never used.** `platforms.find_tool("ffmpeg")` exists,
  `doctor` advertises it as "media remuxing (optional)", and nothing calls it.
  The advertised remuxing does not exist yet. `pkg install ffmpeg` on Termux is
  already suggested by the doctor.
- `FileEntry` carries `mime`, `kind` and `size`.
- Range requests are implemented and byte-exact, which is what makes remuxing
  viable: a track can be extracted once and then served with seeks.

## What an implementation costs

Three routes, cheapest first.

### 1. Sidecar subtitles — small, no ffmpeg, works everywhere

For each `<name>.srt`/`.ass`/`.vtt` sitting next to a video, emit a
`<track kind="subtitles" srclang=… label=…>` and convert SRT/ASS to WebVTT on
the fly (a small text transform; ASS needs styling stripped). The browser then
provides its own caption menu and the CC button.

- Cost: one endpoint, one converter, a dozen lines in the player template.
- Limitation: only helps files whose subtitles are *separate files*.
- This is the only route that gets a real, browser-native track picker.

### 2. ffmpeg remux on demand — the honest one, but heavy

Probe with `ffprobe -show_streams`, then serve
`/t/<id>/audio/<n>` and `/t/<id>/subs/<n>` as separate streams:

```
ffmpeg -i in.mkv -map 0:a:1 -c:a copy -f mp4 -movflags frag_keyframe+empty_moov -
ffmpeg -i in.mkv -map 0:s:0 -f webvtt -
```

`-c:a copy` means no re-encoding, so it is fast even on a phone. The player
fetches the track list from a JSON endpoint and builds its own menu, calling
`v.src` again with `#t=<currentTime>` to stay in sync when switching audio.

- Cost: stream probing, a per-track URL with Range support, a custom audio menu
  (the browser gives you none), and sync-on-switch handling.
- Termux caveat: `pkg install ffmpeg` is a ~60 MB install, and remuxing a 4 GB
  file on a phone is real work even with `copy`.
- Subtitles here become WebVTT, which the browser *can* render — so this route
  also fixes the embedded-subtitle case.

### 3. MSE remux in the browser — what "real" players do, and why not here

The technique used by commercial web players, and the only way to get audio
switching in Chrome without re-encoding: fetch the file with `fetch()`, demux it
in JS (`mp4box.js` for MP4, a Matroska parser for MKV), and feed one SourceBuffer
per chosen track through Media Source Extensions. Switching audio means
`abort()` + `remove()` on the audio SourceBuffer, append the new track's init
segment, and refill from `currentTime`.

It works, and it needs no server, no ffmpeg and no transcode. It is still not
worth it here: roughly 1.5k–3k lines plus a bundled demuxer, a ~60 s forward
buffer to manage, `fetch()` CORS where `<video src>` needed none, and byte-offset
seeking for MKV Cues. On a phone, piping every byte through JavaScript to watch
a movie is the wrong trade against a server that can already `sendfile()`.

### 4. Client-side extraction (JS + WASM) — not worth it

`ffmpeg.wasm` or a JS Matroska parser could demux in the browser. It costs tens
of megabytes downloaded by the *viewer*, and on a phone it is slower than the
server. Rejected.

## Recommended order

1. **Sidecar subtitles (route 1).** Small, no dependency, and it is the one case
   that yields a native picker. Do this first.
2. **ffmpeg remux (route 2)** behind a config flag, with the doctor telling the
   user to `pkg install ffmpeg`, and a clear fallback when it is missing.
   Audio-track switching needs a custom menu, because the browser has none.

## Things to check before building route 2

- `ffprobe` is a *separate binary* in some Termux packages — verify
  `pkg install ffmpeg` provides both, or ship `ffmpeg -i file -f null -` and
  parse stderr (it prints the stream table).
- Seek accuracy after switching audio: `#t=` is a hint, not a guarantee; expect
  to re-apply it on `loadedmetadata`.
- A share with 500 files must not run 500 `ffprobe` calls at page load. Probe
  lazily, on first play, and cache by `(path, mtime, size)`.
- Range responses for a remuxed stream cannot use `os.sendfile` on a pipe; use
  the chunked path.
- `Allow-Ranges` must stay correct for the new endpoints or seeking breaks.

## Summary in one line

Multi-audio and embedded subtitles are not available in the current player
because `<video>` exposes no track list in Chrome/Firefox; fixing it needs
separate per-track URLs from ffmpeg (and a custom menu for audio), or sidecar
subtitle files served as WebVTT, which the browser renders natively.
