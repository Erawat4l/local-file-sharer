# Handoff prompt — give this entire file to Antigravity

Copy everything below the line into Antigravity. It is written for an agent that
has shell access to this Termux device.

---

## MISSION

Take over a real, working Python project on this machine: resolve a command-name
collision so that `lfs` runs **this** project, verify the install, then continue
development on a git branch.

Work on **Termux (Android)**, Python **3.14**, repo already installed once via pip.

**Do not redesign, rename the package, or "improve" the CLI.** The feature work is
done and tested; this task is environment repair plus a verified dev setup.

---

## THE PROJECT

- **Repo:** `https://github.com/Erawat4l/local-file-sharer` (private)
- **Branch to use, exclusively:** `arena/d7f4713b-local-file-sharer`
- **Expected HEAD on that branch:** `244b037` — *"fix: a file deleted mid-archive
  became silent NUL bytes"*
- **Package name:** `local-file-sharer` · **version** `1.0.0` ·
  **requires-python** `>=3.9` (correct as of HEAD)
- **Console script:** `lfs = local_file_sharer.cli:main`
- **Module entry point:** `python -m local_file_sharer` (works, and is
  collision-proof)
- **Core dependencies:** `rich>=13.7`, `qrcode>=7.4`, `tomli` on Python < 3.11
  (**3.14 needs no tomli** — `tomllib` is stdlib)
- **Dev extra:** `.[dev]` → `pytest`, `pytest-cov`, `ruff`

### What the app is
A LAN file sharer. `lfs <path>` serves files from that path over HTTP with a
browser UI: resumable downloads, `Range`/multi-range, folder-as-streaming-ZIP,
optional PIN gate with brute-force lockout, optional browser uploads back to the
host, expiry, a media player mode, a folder browser mode, and a torrent mode with
a built-in HTTP tracker + webseed. QR code is rendered server-side.

---

## THE ACTUAL PROBLEM TO SOLVE (read carefully)

`pip install` **succeeded**. The pip log resolved the git branch to commit
`244b037` and printed `Successfully installed local-file-sharer-1.0.0`.

But running `lfs` launched a **different program** — a third-party banner
reading *"⚡ ANTIGRAVITY HIGH-SPEED TERMUX FILE SHARER"* with a file picker rooted
at `/sdcard/Download`.

So: **`lfs` is shadowed.** Another function/script/package already owns that
name, and it wins on `PATH` (or is a shell function/alias, which beats `PATH`
entirely). Our installed `lfs` script exists but is not what runs.

### Whatever you do, note these facts before changing anything
1. The thing currently called `lfs` is **not** part of this repo. Do **not**
   delete files belonging to it until you have recorded exactly what it is.
2. `python -m local_file_sharer` already works and is not affected by the
   collision. Every setup problem is solvable without breaking that:
   it must keep working after you finish.
3. `shell/fish/start.fish` in the repo is **a fish shell function that also
   defines `lfs`**. If the user's fish config sources it, that is a second
   collision source, and it will shadow the console script.
   Check for it explicitly.

---

## STEP 1 — DIAGNOSE (do not skip; report findings)

Run and record the output of each:

```bash
which -a start
type start
command -v start
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
python -c "import shutil; print(shutil.which('start'))"
python -c "import local_file_sharer, sys; print(local_file_sharer.__file__, sys.version)"
```

Then inspect, in order:

```bash
# shell functions / aliases (these override PATH)
grep -nE "^(start|alias start|function start)" ~/.bashrc ~/.bash_profile ~/.profile 2>/dev/null
grep -rn "start" ~/.config/fish/config.fish 2>/dev/null
grep -rn "start" ~/.config/fish/functions/ 2>/dev/null

# every "start" executable on PATH, in order, with its first line
python - <<'PY'
import os, shutil
seen = {}
for d in os.environ.get("PATH", "").split(os.pathsep):
    p = os.path.join(d, "start")
    if os.path.isfile(p) and os.access(p, os.X_OK):
        seen[p] = True
for p in seen:
    with open(p, "rb") as fh:
        head = fh.read(200).decode("utf-8", "replace").splitlines()[:3]
    print(p, "->", " | ".join(head))
PY
```

**Report to me:** which of these is true —
(a) a shell function/alias named `lfs`,
(b) a second executable named `lfs` earlier on `PATH`,
(c) both,
(d) something else (paste `type start`).
Also say whether the shadowing program is a separate pip package
(`python -m pip show <its package>` / `pip list | grep -i`) or a standalone
script, because that determines whether it can be uninstalled safely.

---

## STEP 2 — PICK A FIX, IN THIS ORDER OF PREFERENCE

### Preferred: give this project its own unambiguous command
Add a second console script whose name nothing else can plausibly claim. The
package already supports being invoked as a module, so this is low risk.

Edit `pyproject.toml`:

```toml
[project.scripts]
start = "local_file_sharer.cli:main"
lfs = "local_file_sharer.cli:main"
```

Reinstall the branch in editable mode so changes take effect without rebuilding:

```bash
cd ~/local-file-sharer            # wherever you clone it, see step 3
python -m pip install --user -e ".[dev]"
```

Result: `lfs /sdcard/Download` runs this project, while leaving whatever `lfs`
currently is untouched. **Verify it with step 4 before doing anything else.**

> Update the docs when you do this: `README.md` mentions `lfs …` in ~47 places
> and `shell/` contains completions. Add `lfs` alongside; do not rip out `lfs`,
> because on Linux/Windows `lfs` is the documented name.

### Second choice: reclaim `lfs`, but only if the owner is identified
If step 1 shows the shadowing `lfs` came from an **Antigravity package or a
stray script that the user says is expendable**, remove *only* that, then confirm
`lfs` resolves to the console script from `sysconfig.get_path('scripts')`.

Do **not** blindly `rm` anything on `PATH` before the diagnosis in step 1, and do
not `pip uninstall` a package you have not identified.

### Never do this
- Do not uninstall/rename `local-file-sharer` — it is the working project.
- Do not copy a script over an existing `lfs` binary that you have not
  identified.
- Do not modify `PATH` in a way that hides other tools.

**Confirm with me which option you took, and why, before moving to step 3.**

---

## STEP 3 — A PROPER DEV CHECKOUT

Do not develop inside the pip build tree. Clone the branch:

```bash
git clone --branch arena/d7f4713b-local-file-sharer \
  https://github.com/Erawat4l/local-file-sharer.git
cd local-file-sharer
git rev-parse --short HEAD     # expect 244b037
```

If the clone fails with an auth error, the repo is private — the user must supply
access (GitHub CLI login or a token via their own credential helper). **Never ask
for a password or token in chat, and never write one into a file.**

Install editable with dev tools:

```bash
python -m pip install --user -e ".[dev]"
```

Termux may need, if anything fails to build:

```bash
pkg install python git
```

---

## STEP 4 — VERIFY (all four must pass; paste the outputs)

```bash
# 1. the collision-free entry point
python -m local_file_sharer version
# expect: local-file-sharer 1.0.0 / python 3.14… / termux

# 2. the new short command (if you added it)
lfs version

# 3. the test suite from the checkout
python -m pytest -q                 # expect 363 passed
python -m ruff check src tests tools   # expect "All checks passed!"
python tools/check_compat.py        # expect "✓ 58 files fit Python 3.9+"

# 4. actually serve something
mkdir -p ~/share-test && echo hello > ~/share-test/a.txt
python -m local_file_sharer ~/share-test --port 8080 --no-qr --no-dashboard
# in a second Termux session:
curl -s -o /tmp/a.zip -w "%{http_code}\n" http://127.0.0.1:8080/download/all.zip
python -c "import zipfile;z=zipfile.ZipFile('/tmp/a.zip');print(z.namelist(), z.testzip())"
# expect: 200, ['share-test/a.txt'], None
```

If anything fails, **stop and report the exact output**. Do not "fix" the
project to make a test pass — the suite is known-green at HEAD `244b037`.

---

## KNOWN ENVIRONMENT NOTES (do not rediscover these the hard way)

- **A test skips on some machines:** `tests/test_screenshots.py` needs the
  `.[shots]` extra (`fonttools`, `pillow`, `resvg-py`). A skip there is expected;
  install `.[shots]` only if you need to regenerate docs images.
- **`tests/js/downloader_smoke.js`** runs the real page JavaScript under
  **Node**. If `node` is missing the test skips, which is fine — do not install
  npm packages for it.
- **Python 3.14 is newer than anything this was tested on** (3.9–3.13 in CI).
  It installed and ran here, but if you hit a traceback caused by 3.14, report it
  rather than patching around it blindly.
- **The app binds `0.0.0.0` by default.** On Termux, Android may prompt for
  network permission; that is expected.
- **Android storage access:** `/sdcard/Download` is readable if Termux storage
  permission is granted (`termux-setup-storage`).

---

## ONGOING DEVELOPMENT RULES FOR THIS REPO

- Stay on `arena/d7f4713b-local-file-sharer`. Never push to `main`, never force-push.
- Gate every change with: `pytest -q` (363 green), `ruff check src tests tools`,
  `ruff format src tests tools`, `python tools/check_compat.py`
  (the last one enforces **Python 3.9** compatibility — no `zip(strict=)`,
  no PEP 604 unions at runtime without `from __future__ import annotations`).
- Every bug fix needs a regression test that **fails before the fix**. Verify it
  fails, then passes.
- Commit style is Conventional Commits (`fix:`, `feat:`, `test:`, `docs:`), and
  the message body explains *why* with the observed evidence.
- Keep `CHANGELOG.md` updated under the current unreleased entry.

---

## DELIVERABLES FROM THIS TASK

Report back with:
1. The origin of the shadowing `lfs` (step 1 output).
2. Which fix you applied and the exact files you changed.
3. Output of all four verifications from step 4.
4. The final command the user should type to share a folder.
