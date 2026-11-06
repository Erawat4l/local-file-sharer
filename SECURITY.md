# Security policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.x     | ✅ |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private vulnerability reporting:
[Security → Report a vulnerability](../../security/advisories/new).

Include:

- what you found and why it matters,
- the exact version (`lfs version`) and platform,
- reproduction steps or a proof of concept,
- any mitigation you already know of.

You can expect an acknowledgement within **72 hours** and a status update within
**7 days**. Once a fix is ready we will coordinate a release date, credit you in
the advisory (unless you prefer to stay anonymous) and publish a GitHub Security
Advisory with a CVE request where appropriate.

## Scope

`local-file-sharer` deliberately runs a **local HTTP server** that other devices
on your network can reach. Things we consider in scope:

- path traversal (`..`, symlinks, URL-encoded tricks) escaping the shared roots
- bypassing the PIN / cookie gate, session fixation, CSRF on uploads
- remote code execution, command injection through filenames or headers
- denial of service that a single LAN peer can trigger
- information disclosure beyond the files the user shared (logs, config, tokens)
- supply-chain problems in the packaging (installers, release artefacts)

Out of scope:

- the fact that a share is reachable by anyone on the same network when no PIN
  is set — that is the documented design; use `--pin` and `--expire`
- vulnerabilities in `tailscale`, `cloudflared`, `aria2c` or the browser
- self-XSS, missing security headers that have no impact
- reports produced only by automated scanners without a working proof

## Design notes

- **No telemetry.** The only outbound requests are the ones a feature needs:
  public-IP lookup, Cloudflare/Tailscale CLIs, the update check and the GitHub
  release download.
- **Secrets stay local.** Per-machine values (device 2 SSH details, static IPs,
  tokens) belong in `config.local.toml`, which is gitignored. `config.toml`
  holds preferences only.
- **PIN gate.** Optional six-digit PIN, compared in constant time, stored in an
  `HttpOnly`, `SameSite=Lax` cookie scoped to the share. Failed attempts are
  rate-limited per client address.
- **Expiry.** `--expire N` stops the share and the server after *N* minutes;
  `/healthz` and `/api/expired` keep answering so clients get a clear message
  instead of a connection reset.
- **No shell interpolation.** External tools receive argument lists, never a
  shell string. Filenames never reach a shell.
- **Uploads are opt-in** (`--upload`), size-capped and written inside the
  configured inbox directory with sanitised names.
- **Cache disabled** on every response so a shared file list can never be served
  stale to the wrong device.

## Hardening checklist for users

```bash
lfs --pin 4821 --expire 30 --no-upload    # untrusted networks
lfs --tailscale                           # never expose the port publicly
lfs doctor                                # confirm what is listening where
```

Run the share with the least privileged account that can read the files, and
avoid sharing `$HOME` or drive roots on a network you do not control.
