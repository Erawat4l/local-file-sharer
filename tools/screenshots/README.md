# Screenshot toolchain

Everything in `docs/assets/` is generated from **real renders** of this project,
so the README cannot drift away from the product. Nothing here ships with the
package — it is a maintainer tool.

```text
cli_shots.py ──┐
demo_gif.py  ──┼──> termsvg.py     ──> PNG / GIF   (rich renderables → cells → SVG)
hero.py      ──┤
web_gif.py   ──┘
webshot_live.py ───> Chromium (CDP) ──> web-*.png   (the real pages, screenshotted)
```

## The web screenshots are the pages

`web-download.png`, `web-server.png` and `web-player.png` come from
`webshot_live.py`, which boots a real `ShareServer` on a loopback port, points
headless Chromium at it and saves the viewport. There is no drawing step, so
there is nothing to drift: whatever the HTML and CSS say is what the image says.

They used to be hand-authored SVG (`webgen.py`, still used by `web_gif.py` for
its own frames). It re-drew the pages from the design tokens so the files would
be deterministic in a diff — and they drifted anyway, because nothing compared
the drawing to the markup. The pictures showed a search box, two solid buttons
and a header line the shipped page never had.

`web-text.txt` is written from the live DOM: one label per visible element, read
in place so CSS `text-transform` is applied the way a person sees it.
`tests/test_asset_drift.py` holds that file against both the templates and a
list of copy that must never come back.

Running it needs a Chromium binary and `websocket-client`; CI does not run the
capture, only the checks on the committed results.

```bash
python tools/screenshots/webshot_live.py --chrome "$(command -v chromium)" --root /path/to/demo-tree
```

`termsvg.py` renders the *terminal* the same way: a Rich renderable becomes a
grid of `(char, fg, bg, bold)` cells and then one `<text>` per style run. That
also fixes what `Console.export_svg` gets wrong here: it hard-codes Fira Code,
uses clip-paths resvg ignores, and cannot draw emoji.

## Regenerating

Any change to a menu label, a description or a theme name makes every terminal
asset stale, and nothing about a `.gif` shows up in a diff — `demo.gif` once
advertised ``start <path>`` and ``switch dark / light / neon`` long after the
program said ``lfs <path>`` and ``light / mono``. So:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" pillow resvg-py fonttools

python tools/screenshots/demo_gif.py     # gif + docs/assets/demo-menu.txt
python tools/screenshots/cli_shots.py    # the cli-*.png screenshots
python tools/screenshots/check_assets.py # tofu, leaking paths, missing files
```

`demo_gif.py` also writes the menu it drew as plain text next to the gif, and
`tests/test_screenshots.py` re-renders the menu and compares the two. That test
fails when the code changes and the assets were not regenerated, which is the
only way to catch this without a human looking at a picture.

Fonts (not committed — they are large and separately licensed under the OFL):

| Font | Used for | Where |
|---|---|---|
| JetBrains Mono | terminal shots, mono text | <https://github.com/JetBrains/JetBrainsMono> |
| Inter | web UIs, hero | <https://github.com/rsms/inter> |
| Twemoji SVG | every emoji glyph (MIT/CC-BY 4.0) | <https://github.com/jdecked/twemoji> |

All three are gitignored. Drop them into `tools/screenshots/inputs/` (or point
`LFS_SHOT_INPUTS` somewhere else):

```text
tools/screenshots/inputs/
├── fonts/
│   ├── JetBrainsMono-Regular.ttf   JetBrainsMono-Bold.ttf
│   ├── Inter-Regular.ttf  -Medium  -SemiBold  -Bold  -ExtraBold
│   └── Noto-COLRv1.ttf             (optional: previews of glyph fallbacks)
├── twemoji/                        (the contents of assets/svg, ~4000 files)
└── demo/                           (see the tree below)
```

`InterVariable.ttf` can be instanced with `fontTools.varLib.instancer` for each
weight (400/500/600/700/800) — the family name must stay `Inter` or resvg will
not match `font-family: Inter`.

Then, from the repository root (they only write PNGs — no SVG copies are kept,
because each one would be megabytes of embedded font):

```bash
PYTHONPATH=src python tools/screenshots/cli_shots.py    # docs/assets/cli-*.png
PYTHONPATH=src python tools/screenshots/webgen.py       # docs/assets/web-*.png
PYTHONPATH=src python tools/screenshots/hero.py         # docs/assets/banner.png
PYTHONPATH=src python tools/screenshots/demo_gif.py     # docs/assets/demo.gif
```

`cli_shots.py` and `webgen.py` expect the demo tree used in the pictures. It
lives under `sdcard/Download/demo` so the picker breadcrumb reads like the
Termux layout instead of this checkout's absolute path:

```bash
python - <<'PY'
import pathlib
root = pathlib.Path("tools/screenshots/inputs/sdcard/Download/demo")
for rel, size in {
    "Sample-Video-4K.mkv": 3_842_119_680,
    "track-01.flac": 421_003_264,
    "design-preview.png": 18_412_339,
    "receipt.pdf": 2_918_004,
    "notes.txt": 4_211,
    "DCIM/screenshot-01.jpg": 5_112_004,
    "DCIM/screenshot-02.jpg": 5_112_004,
    "Photos/photo-01.jpg": 5_112_004,
}.items():
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(size)          # sparse files: instant, no disk cost
PY
```

## Before you commit a screenshot

```bash
PYTHONPATH=src python tools/screenshots/check_assets.py
```

It fails if a README image is missing from `docs/assets/`, if a referenced
image is untracked, or if any emoji used by the terminal shots has no Twemoji
SVG — which is how a whole set of screenshots once shipped with tofu boxes
while `to_svg` printed nothing at all. `to_svg` now warns too, but the checker
is what CI runs.

## Conventions in the pictures

- Terminal shots are captured at 104 columns (the doctor at 132) — wide enough
  for the widest panel, narrow enough to stay readable on GitHub.
- Every CLI shot uses the **dark** theme with the cyan accent, matching the
  default `config.toml`.
- The dashboard shows a 4 GB share at 72 MB/s so the "at target" badge is
  visible; the numbers are internally consistent (96.1% of 4 GB, ETA 2s).
- Web shots are 1440 × 900 @1.5× with the real palette; no personal data, no
  real IP addresses (`192.168.1.42` is a demo value).
