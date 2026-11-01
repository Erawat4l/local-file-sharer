# ─────────────────────────────────────────────────────────────────────────────
#  fish function `start`  —  Termux · Linux · macOS · fish on Windows
#
#  Install:
#    install.sh copies this file to ~/.config/fish/functions/start.fish
#    (Windows: %APPDATA%\fish\functions\start.fish)
#
#  Why a function? `start` is a Windows built-in (cmd.exe / PowerShell), and
#  typing `start` in fish should open local-file-sharer — never a new window.
# ─────────────────────────────────────────────────────────────────────────────

function start --description 'local-file-sharer — share files between your devices (menu, doctor, config…)'
    # Prefer the console script installed by pipx/pip; fall back to the module.
    set -l lfs_bin (command -s local-file-sharer)
    set -l py (command -s python3; or command -s python)

    # `start --update` must not be swallowed by the Python entry point check
    if test (count $argv) -gt 0; and contains -- $argv[1] doctor config theme completions banner version help menu
        set -l cmd $argv[1]
        set -e argv[1]
        if test -n "$lfs_bin"
            command $lfs_bin $cmd $argv
        else if test -n "$py"
            command $py -m local_file_sharer $cmd $argv
        else
            echo "local-file-sharer is not installed. Run: pipx install local-file-sharer" >&2
            return 127
        end
        return $status
    end

    if test -n "$lfs_bin"
        command $lfs_bin $argv
    else if test -n "$py"
        command $py -m local_file_sharer $argv
    else
        echo "local-file-sharer is not installed." >&2
        echo "  Termux/Linux/macOS:  pipx install local-file-sharer   (or ./install.sh)" >&2
        echo "  Windows:             irm https://raw.githubusercontent.com/Erawat4l/local-file-sharer/main/install.ps1 | iex" >&2
        return 127
    end
end

# Shortcut: `share` behaves exactly like `start` (muscle memory from other tools)
function share --description 'alias for start (local-file-sharer)'
    start $argv
end

# Shortcut: `lfs` too
function lfs --description 'alias for start (local-file-sharer)'
    start $argv
end
