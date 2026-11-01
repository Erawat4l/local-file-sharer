# Screenshot toolchain

Everything in `docs/assets/` is generated from **real renders** of this project,
so the README cannot drift away from the product. Nothing here ships with the
package — it is a maintainer tool.

```text
cli_shots.py ──┐
demo_gif.py  ──┼──> termsvg.py  ──> PNG / GIF   (rich renderables → cells → SVG)
hero.py      ──┤                 └─> webshot.py (hand-authored SVG for the hero)
webgen.py    ──┘
```

## Why not a headless browser?

The three web UIs are shipped HTML+CSS. Chromium needs `libnss3`, `libnspr4`,
Pango and Cairo, which are not available in every build environment. Instead
`webshot.py` draws the same screens with the *shipped* design tokens
(`--bg`, `--accent`, radii, copy) and measures text with the real Inter and
JetBrains Mono files — the PNGs are deterministic and reviewable in a diff.

`termsvg.py` does the same for the terminal: it renders a Rich renderable into a
grid of `(char, fg, bg, bold)` cells and emits one `<text>` per style run. That
also fixes what `Console.export_svg` gets wrong here: it hard-codes Fira Code,
uses clip-paths resvg ignores, and cannot draw emoji.

## Regenerating

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" pillow resvg-py fonttools
```

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
    "Aurora-Film-Festival-Keynote-4K.mkv": 3_842_119_680,
    "live-set-2026.flac": 421_003_264,
    "moodboard-final.png": 18_412_339,
    "report.pdf": 2_918_004,
    "Termux-notes.txt": 4_211,
    "DCIM/IMG_20261101_143210.jpg": 5_112_004,
    "DCIM/IMG_20261101_143422.jpg": 5_112_004,
    "Photos/2026-10-31-wedding-012.jpg": 5_112_004,
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
