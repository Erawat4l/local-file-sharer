# ─────────────────────────────────────────────────────────────────────────────
#  fish function `lfs`  —  Termux · Linux · macOS · fish on Windows
#
#  Install:
#    install.sh copies this file to ~/.config/fish/functions/lfs.fish
#    (Windows: %APPDATA%\fish\functions\lfs.fish)
#
#  Why a function at all? It prefers the pipx/pip console script and falls back
#  to `python -m local_file_sharer`, so `lfs` works even when the script is not
#  on PATH (a common Termux situation). It defines nothing else: no `start`.
# ─────────────────────────────────────────────────────────────────────────────

function lfs --description 'local-file-sharer — share files between your devices (menu, doctor, config…)'
    # Prefer the console script installed by pipx/pip; fall back to the module.
    set -l lfs_bin (command -s local-file-sharer)
    set -l py (command -s python3; or command -s python)

    if test -n "$lfs_bin"
        command $lfs_bin $argv
    else if test -n "$py"
        command $py -m local_file_sharer $argv
    else
        echo "LOCAL FILE SHARER is not installed." >&2
        echo "  Termux/Linux/macOS:  ./install.sh   (or: pipx install 'local-file-sharer @ git+https://github.com/Erawat4l/local-file-sharer.git')" >&2
        echo "  Windows:             irm https://raw.githubusercontent.com/Erawat4l/local-file-sharer/main/install.ps1 | iex" >&2
        return 127
    end
end

# Shortcut: `share` behaves exactly like `lfs` (muscle memory from other tools)
function share --description 'alias for lfs (local-file-sharer)'
    lfs $argv
end
