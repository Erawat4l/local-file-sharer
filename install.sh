#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  local-file-sharer · one-line installer for Termux, Linux and macOS
#
#    pipx install "local-file-sharer @ git+https://github.com/Erawat4l/local-file-sharer.git@main"
#
#    …or run this script directly. While the repository is private the raw file
#    host 404s, so `curl …/raw.githubusercontent.com/… | bash` fails with
#    "curl: (22) … error: 404". Read it through the API instead (authenticated),
#    into a file:
#
#    gh api "repos/Erawat4l/local-file-sharer/contents/install.sh" \
#           -H "Accept: application/vnd.github.raw" > install.sh && bash install.sh
#
#  What it does
#    1. detects Termux / Linux / macOS and the Python version
#    2. installs rich + qrcode (and optionally aria2, tailscale, cloudflared)
#    3. installs the `lfs` command with pipx (preferred) or pip --user
#    4. writes the fish function + completions and, on fish, wires `lfs` up
#    5. prints next steps (`lfs doctor`)
#
#  Flags
#    --dev          install from this checkout in editable mode (needs git)
#    --no-extras    skip optional tools (aria2/cloudflared/tailscale)
#    --no-fish      do not touch the fish config
#    --yes | -y     never prompt
#    --uninstall    remove the pipx/pip install (keeps your config)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_SLUG="Erawat4l/local-file-sharer"
# `main` is the release branch. Override with LFS_BRANCH=… to install from a
# branch under development.
BRANCH="${LFS_BRANCH:-main}"
RAW_BASE="https://raw.githubusercontent.com/${REPO_SLUG}/${BRANCH}"
APP="local-file-sharer"
VERSION="1.0.0"

ASSUME_YES=0
DEV=0
NO_EXTRAS=0
NO_FISH=0
UNINSTALL=0

BLUE='\033[1;34m'; CYAN='\033[1;36m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'
RED='\033[1;31m'; DIM='\033[2m'; BOLD='\033[1m'; RESET='\033[0m'

log()    { printf "${CYAN}▸${RESET} %s\n" "$*"; }
ok()     { printf "${GREEN}✔${RESET} %s\n" "$*"; }
warn()   { printf "${YELLOW}!${RESET} %s\n" "$*"; }
die()    { printf "${RED}✖ %s${RESET}\n" "$*" >&2; exit 1; }
header() { printf "\n${BLUE}%s${RESET}\n" "$*"; }

for arg in "$@"; do
  case "$arg" in
    --yes|-y)     ASSUME_YES=1 ;;
    --dev)        DEV=1 ;;
    --no-extras)  NO_EXTRAS=1 ;;
    --no-fish)    NO_FISH=1 ;;
    --uninstall)  UNINSTALL=1 ;;
    -h|--help)
      awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
      exit 0 ;;
    *) die "unknown flag: $arg (try --help)" ;;
  esac
done

# ── detect the platform ──────────────────────────────────────────────────────
OS="$(uname -s 2>/dev/null || echo unknown)"
case "$OS" in
  Linux)  PLATFORM="linux" ;;
  Darwin) PLATFORM="macos" ;;
  *)      PLATFORM="unknown" ;;
esac
IS_TERMUX=0
if [ -n "${TERMUX_VERSION:-}" ] || [ "${PREFIX:-}" = "/data/data/com.termux/files/usr" ]; then
  IS_TERMUX=1
  PLATFORM="termux"
fi

header "local-file-sharer ${VERSION} installer"
printf "  platform ${BOLD}%s${RESET} · shell ${BOLD}%s${RESET}\n" "$PLATFORM" "${SHELL##*/}"

# ── uninstall ────────────────────────────────────────────────────────────────
if [ "$UNINSTALL" = "1" ]; then
  header "uninstalling"
  if command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q "$APP"; then
    pipx uninstall "$APP" && ok "removed the pipx install"
  fi
  python3 -m pip uninstall -y "$APP" >/dev/null 2>&1 && ok "removed the pip install" || true
  rm -f "${HOME}/.config/fish/functions/start.fish" \
        "${HOME}/.config/fish/functions/lfs.fish" \
        "${HOME}/.config/fish/completions/lfs.fish" 2>/dev/null || true
  warn "your config (~/.config/${APP}/config.toml) was left untouched"
  exit 0
fi

# ── python ───────────────────────────────────────────────────────────────────
PY=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || die "Python 3.9+ not found.
  Termux: pkg install python
  Debian/Ubuntu: sudo apt install python3 python3-pip
  Fedora: sudo dnf install python3
  macOS: brew install python@3.12"

PY_VERSION="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "Python ${PY_VERSION} is too old — 3.9+ required"
ok "python ${PY_VERSION} (${PY})"

# ── optional system packages ─────────────────────────────────────────────────
if [ "$NO_EXTRAS" = "0" ]; then
  header "optional tools (aria2 for 16-connection downloads, tunnels, media)"
  if [ "$PLATFORM" = "termux" ]; then
    for pkg in aria2 curl wget zip unzip; do
      if ! command -v "${pkg%%-*}" >/dev/null 2>&1; then
        log "pkg install ${pkg}"
        pkg install -y "$pkg" >/dev/null 2>&1 && ok "installed ${pkg}" || warn "could not install ${pkg} (optional)"
      fi
    done
  elif [ "$PLATFORM" = "linux" ] && command -v apt-get >/dev/null 2>&1; then
    MISSING=""
    for bin in aria2c curl wget zip unzip; do
      command -v "$bin" >/dev/null 2>&1 || MISSING="$MISSING ${bin/aria2c/aria2}"
    done
    if [ -n "$MISSING" ]; then
      if [ "$ASSUME_YES" = "1" ] || [ -t 0 ]; then
        log "apt-get install${MISSING} (optional, sudo may prompt)"
        sudo apt-get install -y $MISSING >/dev/null 2>&1 && ok "installed${MISSING}" || warn "skipped${MISSING}"
      else
        warn "missing:${MISSING} — install with: sudo apt install${MISSING}"
      fi
    fi
  fi
  command -v aria2c >/dev/null 2>&1 && ok "aria2c present → 16-connection downloads enabled" \
    || warn "aria2c not found — the web downloader still works, just fewer connections"
  command -v tailscale >/dev/null 2>&1 && ok "tailscale present" \
    || printf "${DIM}  tailscale not found (optional): %s${RESET}\n" "https://tailscale.com/download"
  command -v cloudflared >/dev/null 2>&1 && ok "cloudflared present" \
    || printf "${DIM}  cloudflared not found (optional): https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/${RESET}\n"
fi

# ── install the package ──────────────────────────────────────────────────────
header "installing ${APP}"
PIPX_OK=0
command -v pipx >/dev/null 2>&1 && PIPX_OK=1

if [ "$DEV" = "1" ]; then
  [ -f "pyproject.toml" ] || die "--dev expects to be run from the repository root"
  if [ "$PIPX_OK" = "1" ]; then
    pipx install --force --editable . && ok "installed (editable) with pipx"
  else
    "$PY" -m pip install --user -e . && ok "installed (editable) with pip"
  fi
elif [ "$PIPX_OK" = "1" ]; then
  # not on PyPI: install straight from the repository
  pipx install --force "git+https://github.com/${REPO_SLUG}.git@${BRANCH}" \
    && ok "installed from GitHub with pipx"
else
  warn "pipx not found (recommended for CLI tools)"
  if [ "$PLATFORM" = "termux" ]; then
    warn "Termux: python -m pip install --user . may need: pkg install python-pip"
  fi
  if "$PY" -m pip install --user --upgrade "git+https://github.com/${REPO_SLUG}.git@${BRANCH}" >/dev/null 2>&1; then
    ok "installed with pip --user"
  elif "$PY" -m pip install --user --upgrade --break-system-packages \
         "git+https://github.com/${REPO_SLUG}.git@${BRANCH}" >/dev/null 2>&1; then
    # Debian/Ubuntu mark the system Python as externally managed (PEP 668).
    # A --user install cannot break the OS packages, so it is safe here.
    ok "installed with pip --user (--break-system-packages)"
  else
    die "pip install failed.
  Try: ${PY} -m pip install --user --break-system-packages "git+https://github.com/${REPO_SLUG}.git@${BRANCH}"
  Or:  pipx install "git+https://github.com/${REPO_SLUG}.git@${BRANCH}""
  fi
fi

# Make sure ~/.local/bin is on PATH for the current session.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) PATH="$HOME/.local/bin:$PATH"; export PATH
     printf "${DIM}  added ~/.local/bin to PATH for this session${RESET}\n" ;;
esac

if ! command -v lfs >/dev/null 2>&1 && ! command -v local-file-sharer >/dev/null 2>&1; then
  warn "the \`lfs\` command is not on PATH yet — open a new terminal or add:"
  printf "    export PATH=\"\$HOME/.local/bin:\$PATH\"   # add to ~/.bashrc / ~/.profile\n"
fi

# ── fish integration ─────────────────────────────────────────────────────────
if [ "$NO_FISH" = "0" ]; then
  header "fish shell integration"
  FISH_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/fish"
  if command -v fish >/dev/null 2>&1 || [ -d "$FISH_DIR" ]; then
    mkdir -p "$FISH_DIR/functions" "$FISH_DIR/completions"
    SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
    # a leftover start.fish from an older install would shadow nothing we want
    rm -f "$FISH_DIR/functions/start.fish" 2>/dev/null || true
    if [ -n "$SRC" ] && [ -f "$SRC/shell/fish/lfs.fish" ]; then
      cp "$SRC/shell/fish/lfs.fish" "$FISH_DIR/functions/lfs.fish"
      ok "installed fish function → $FISH_DIR/functions/lfs.fish"
    else
      if command -v curl >/dev/null 2>&1 && curl -fsSL "${RAW_BASE}/shell/fish/lfs.fish" \
           -o "$FISH_DIR/functions/lfs.fish" 2>/dev/null; then
        ok "downloaded fish function → $FISH_DIR/functions/lfs.fish"
      else
        warn "could not write the fish function (offline?)"
      fi
    fi
    # fish completions
    if command -v local-file-sharer >/dev/null 2>&1; then
      local-file-sharer completions fish > "$FISH_DIR/completions/lfs.fish" 2>/dev/null \
        && ok "installed fish completions"
    fi
    printf "${DIM}  open a new fish session, then type: lfs${RESET}\n"
  else
    printf "${DIM}  fish not detected — skipping (install it for the nicest experience)${RESET}\n"
  fi
fi

# ── bash/zsh completions ─────────────────────────────────────────────────────
header "shell completions"
if command -v local-file-sharer >/dev/null 2>&1; then
  BASH_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion/completions"
  rm -f "$BASH_DIR/start" 2>/dev/null || true
  if mkdir -p "$BASH_DIR" 2>/dev/null && local-file-sharer completions bash > "$BASH_DIR/lfs" 2>/dev/null; then
    ok "bash completion → $BASH_DIR/lfs"
  fi
  ZSH_DIR="${ZDOTDIR:-$HOME}/.zfunc"
  if [ -d "$ZSH_DIR" ] || mkdir -p "$ZSH_DIR" 2>/dev/null; then
    rm -f "$ZSH_DIR/_start" 2>/dev/null || true
    local-file-sharer completions zsh > "$ZSH_DIR/_lfs" 2>/dev/null && ok "zsh completion → $ZSH_DIR/_lfs"
    printf "${DIM}  (add \`fpath+=~/.zfunc; autoload -Uz compinit && compinit\` to ~/.zshrc)${RESET}\n"
  fi
else
  warn "skipped completions (command not on PATH yet)"
fi

# ── summary ─────────────────────────────────────────────────────────────────
header "done"
printf "  ${BOLD}lfs${RESET}                 interactive menu
  ${BOLD}lfs <path>${RESET}          share immediately
  ${BOLD}lfs doctor${RESET}          check tools, firewall and network
  ${BOLD}lfs config show${RESET}     print the configuration
  ${BOLD}lfs upgrade${RESET}         install the newest code from GitHub

${DIM}  config: ~/.config/${APP}/config.toml
  docs:   https://github.com/${REPO_SLUG}#readme${RESET}
"
ok "local-file-sharer is ready"
