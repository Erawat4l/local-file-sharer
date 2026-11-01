## What does this PR do?

<!-- One paragraph. Link the issue: Closes #123 -->

## Type of change

- [ ] `feat` — new behaviour
- [ ] `fix` — bug fix
- [ ] `perf` — faster, same behaviour
- [ ] `refactor` — internal cleanup, same behaviour
- [ ] `docs` — documentation only
- [ ] `test` — tests only
- [ ] `ci` / `build` / `chore`

## How was it tested?

<!-- Commands you ran, devices you used, what you verified by hand. -->

```bash
pytest -q
ruff check src tests && ruff format --check src tests
```

## Screenshots (visual changes only)

<!-- Drag PNGs here: CLI panels, web UIs, both themes if relevant. -->

## Checklist

- [ ] `ruff check` and `ruff format --check` pass
- [ ] `pytest -q` passes, and new behaviour has tests
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] No secrets, tokens, personal IPs or `config.local.toml` in the diff
- [ ] Docs updated if the CLI, config keys or web routes changed
- [ ] Still pure Python with only `rich` + `qrcode` at runtime
