# Contributing to local-file-sharer

Thanks for taking the time to contribute! This document explains how to set up
the project, what we expect from a pull request, and the few conventions that
keep the codebase predictable.

---

## Code of conduct

Be kind, assume good faith, and keep discussion technical. Harassment or
personal attacks are not welcome and will be removed.

---

## Getting started

```bash
git clone https://github.com/Erawat4l/local-file-sharer.git
cd local-file-sharer
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Requirements: **Python 3.9+**. The runtime dependencies stay deliberately tiny —
`rich` and `qrcode` only. Please do not add a third runtime dependency without
discussing it in an issue first.

Useful commands:

```bash
pytest -q                       # full suite, no network needed (~10 s)
pytest -q --cov=local_file_sharer
ruff check src tests
ruff format src tests
start --help                    # the CLI you just installed
```

---

## What to work on

- **Good first issues** are labelled
  [`good first issue`](../../labels/good%20first%20issue).
- **Bugs**: please include the exact command, your OS/terminal, the
  `start doctor --json` output and (if it crashed) `--debug` traceback.
- **Features**: open an issue first so we can agree on the scope. The roadmap in
  the README lists things we already intend to build.

---

## Project layout

```text
src/local_file_sharer/
├── cli.py            argument parsing + dispatch (entry point for `start`)
├── menu.py           the interactive menu
├── picker.py         picker model: listing, fuzzy matching, selection, sizes
├── share.py          orchestration: prepare → start server → publish → wait
├── config.py         layered TOML config + env overrides + local secrets
├── state.py          recents, favourites, totals, history
├── platforms.py      per-OS behaviour (sendfile, drives, tools, clipboard)
├── network.py        LAN/public IP, Tailscale, Cloudflare, firewall hints
├── doctor.py         diagnostics + renderable report
├── updater.py        `start --update` from GitHub releases
├── completions.py    fish / bash / PowerShell / zsh scripts
├── qr.py             terminal, SVG and PNG QR output
├── transfer/         manifest, HTTP handler, server, zipstream, torrent, stats
├── ui/               theme, console, banner, picker UI, dashboard, components
└── web/assets/       downloader / server / player / login / error HTML + CSS
```

Two rules keep this structure healthy:

1. **`ui/` never imports `transfer/`.** Rendering must not know about sockets.
2. **`transfer/` never imports `ui/`.** The HTTP layer stays testable without a
   terminal.

---

## Coding guidelines

- **Format**: `ruff format` (double quotes, 110-col lines). Lint: `ruff check`.
  CI fails on either.
- **Python 3.9 compatibility.** Use `from __future__ import annotations` and
  builtin generics (`list[str]`, `X | None` inside annotations) rather than
  `typing.List`.
- **Type hints** on public functions. Internal helpers may skip them.
- **Errors**: raise `SharerError` (or a subclass) with a human message and
  `hints=[...]`. The CLI turns those into a friendly panel; a bare traceback is
  a bug.
- **No blocking I/O in the UI thread** for anything that can take longer than a
  frame: use the existing background helpers.
- **No screen clearing** in the picker or the dashboard — redraw in place.
- **No caching headers** on anything served by the HTTP layer.
- **No telemetry**, no analytics, no phone-home beyond the features the user
  explicitly enables (public-IP lookup, tunnels, update check).

### Comments

Write comments that explain *why*, not *what*. A comment like
`# loop over the files` is noise; `# ZIP64 needs the central directory before
we can emit the data descriptor` is gold.

---

## Tests

- Every bug fix needs a regression test.
- New behaviour needs tests too: `tests/` mirrors the module names
  (`test_fmt.py`, `test_torrent.py`, `test_server.py`, …).
- Tests must **not** touch the network. `tests/conftest.py` isolates `HOME` and
  the XDG directories automatically; use the `share_tree` and `free_port`
  fixtures instead of hard-coded paths and ports.
- Prefer asserting on behaviour (bytes served, HTTP status, computed sizes) over
  snapshots of rendered text.

```bash
pytest -q tests/test_server.py -k range
```

---

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <short summary>

<body — why, not what>

<footer — refs, BREAKING CHANGE>
```

Types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`,
`chore`, `style`, `revert`.

Examples:

```text
feat(stream): seek without buffering the whole file
fix(picker): keep the cursor visible when the page shrinks
perf(zip): store media files instead of deflating them
docs(readme): add the Windows fallback explanation
```

Keep the subject ≤ 72 characters, imperative mood, no trailing period. One
logical change per commit.

---

## Pull requests

Before you open one:

- [ ] `ruff check src tests` and `ruff format --check src tests` pass
- [ ] `pytest -q` passes
- [ ] You added or updated tests
- [ ] You updated `CHANGELOG.md` under `[Unreleased]`
- [ ] Screenshots included for anything visual (CLI or web UI)
- [ ] No secrets, tokens, personal IPs or `config.local.toml` in the diff

Fill in the PR template, link the issue it closes (`Closes #123`) and describe
*how you tested it*. Reviews focus on behaviour and maintenance cost, not on
personal style preferences that the formatter already handles.

---

## Releasing

Maintainers only:

1. Update `CHANGELOG.md` (move `[Unreleased]` into the new version) and bump
   `version` in `pyproject.toml` + `src/local_file_sharer/__init__.py`.
2. Commit: `chore(release): vX.Y.Z`.
3. Tag and push:

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin main --tags
   ```

4. The release workflow builds the wheel/sdist, runs the tests, creates the
   GitHub release from the changelog section and attaches the standalone
   Windows `.exe` plus `SHA256SUMS`.

---

## License

By contributing you agree that your work is released under the [MIT
License](LICENSE).
