# ─────────────────────────────────────────────────────────────────────────────
#  Windows: fish function `lfs`
#
#  `install.ps1` drops this at  %APPDATA%\fish\functions\start.fish
#  It shadows the Windows built-in `lfs` inside fish only — cmd.exe and
#  PowerShell keep their built-in, which is why share.cmd exists as a fallback.
# ─────────────────────────────────────────────────────────────────────────────

function lfs --description 'local-file-sharer (Windows) — share files, run doctor/config/theme'
    set -l lfs_bin (command -s local-file-sharer)
    set -l lfs_cmd (command -s local-file-sharer.cmd)
    set -l lfs_exe (command -s local-file-sharer.exe)
    set -l py (command -s python; or command -s py; or command -s python3)

    if test -n "$lfs_exe"
        command $lfs_exe $argv
        return $status
    end
    if test -n "$lfs_bin"
        command $lfs_bin $argv
        return $status
    end
    if test -n "$lfs_cmd"
        command $lfs_cmd $argv
        return $status
    end
    if test -n "$py"
        command $py -m local_file_sharer $argv
        return $status
    end

    echo "local-file-sharer is not installed." >&2
    echo "  Run:  irm https://raw.githubusercontent.com/Erawat4l/local-file-sharer/main/install.ps1 | iex" >&2
    echo "  Or:   pipx install 'local-file-sharer @ git+https://github.com/Erawat4l/local-file-sharer.git'" >&2
    return 127
end

function share --description 'alias for start (local-file-sharer)'
    start $argv
end
