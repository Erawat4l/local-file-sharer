<#
.SYNOPSIS
    local-file-sharer installer for Windows 10/11 (PowerShell 5.1+).

.DESCRIPTION
    Installs LOCAL FILE SHARER and its `lfs` command, wires it into fish (if
    present) and puts `lfs.cmd` on PATH so it works in cmd.exe and PowerShell
    too. `lfs` is not a built-in in either shell, so the command is a real
    executable rather than an alias.

    What it does
      1. finds Python 3.9+ (or installs nothing and tells you how)
      2. installs the package with pipx (preferred) or `pip install --user`
      3. adds %APPDATA%\fish\functions\lfs.fish so fish gets `lfs`
      4. drops lfs.cmd on PATH for cmd.exe / PowerShell
      5. registers tab-completion for PowerShell

.EXAMPLE
    pipx install "local-file-sharer @ git+https://github.com/Erawat4l/local-file-sharer.git@arena/d7f4713b-local-file-sharer"

.NOTES
    The raw file host returns 404 while this repository is private. `irm` on a
    raw URL therefore fails even though the file exists; read it through the
    authenticated API, or use the pipx command above.

.EXAMPLE
    .\install.ps1 -Dev -NoExtras
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$NoExtras,
    [switch]$NoFish,
    [switch]$Yes,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$App       = 'local-file-sharer'
$Version   = '1.0.0'
$RepoSlug  = 'Erawat4l/local-file-sharer'
# The package lives on this branch, not on `main` — `main` holds only a stub
# README, so installing from it fails with "neither 'setup.py' nor
# 'pyproject.toml' found". Override with $Env:LFS_BRANCH when needed.
$Branch    = if ($Env:LFS_BRANCH) { $Env:LFS_BRANCH } else { 'arena/d7f4713b-local-file-sharer' }
$RawBase   = "https://raw.githubusercontent.com/$RepoSlug/$Branch"

function Write-Step  { param($m) Write-Host "> $m" -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host "OK $m" -ForegroundColor Green }
function Write-Warn2 { param($m) Write-Host "!  $m" -ForegroundColor Yellow }
function Write-Die   { param($m) Write-Host "X  $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host " local-file-sharer $Version - Windows installer" -ForegroundColor Blue
Write-Host " powershell $($PSVersionTable.PSVersion) - $([System.Environment]::OSVersion.VersionString)"

# ── uninstall ────────────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Step 'uninstalling'
    if (Get-Command pipx -ErrorAction SilentlyContinue) { pipx uninstall $App 2>$null }
    python -m pip uninstall -y $App 2>$null
    foreach ($name in @('lfs.fish', 'start.fish', 'share.cmd')) {
        $old = Join-Path $env:APPDATA "fish\functions\$name"
        if (Test-Path $old) { Remove-Item $old -Force }
    }
    $binDir = Join-Path $env:LOCALAPPDATA 'local-file-sharer\bin'
    if (Test-Path $binDir) { Remove-Item $binDir -Recurse -Force }
    Write-Warn2 'your config (%APPDATA%\local-file-sharer\config.toml) was left untouched'
    exit 0
}

# ── python ───────────────────────────────────────────────────────────────────
Write-Step 'looking for Python 3.9+'
$python = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        $args0 = if ($candidate -eq 'py') { @('-3', '-c', 'import sys; print(sys.version_info[:2])') }
                 else { @('-c', 'import sys; print(sys.version_info[:2])') }
        $out = & $candidate @args0 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; $pyVersion = $out; break }
    } catch { continue }
}
if (-not $python) {
    Write-Die @'
Python 3.9+ was not found.
  Install it first (one of):
    winget install Python.Python.3.12
    https://www.python.org/downloads/windows/   (tick "Add python.exe to PATH")
  Then run this installer again.
'@
}
Write-Ok "$python $pyVersion"

# ── install the package ─────────────────────────────────────────────────────
Write-Step "installing $App"
$pipx = Get-Command pipx -ErrorAction SilentlyContinue
if ($Dev) {
    if (-not (Test-Path 'pyproject.toml')) { Write-Die '-Dev expects to run inside the repository root' }
    if ($pipx) { pipx install --force --editable . }
    else { & $python -m pip install --user --upgrade -e . }
} elseif ($pipx) {
    pipx install --force $App
} else {
    Write-Warn2 'pipx not found (it keeps CLI tools isolated) - using pip --user'
    & $python -m pip install --user --upgrade $App
    if ($LASTEXITCODE -ne 0) {
        & $python -m pip install --user --upgrade "git+https://github.com/$RepoSlug.git"
    }
}
if ($LASTEXITCODE -ne 0) { Write-Die 'installation failed - see the messages above' }

# Resolve the installed executable now (pipx puts it in ~\.local\bin)
$lfsCmd = Get-Command local-file-sharer -ErrorAction SilentlyContinue
if (-not $lfsCmd) {
    $candidates = @(
        (Join-Path $env:USERPROFILE '.local\bin\local-file-sharer.exe'),
        (Join-Path $env:APPDATA 'Python\Scripts\local-file-sharer.exe')
    )
    foreach ($cand in $candidates) { if (Test-Path $cand) { $lfsCmd = $cand; break } }
}
if ($lfsCmd) { Write-Ok "installed -> $($lfsCmd.Source ?? $lfsCmd)" } else { Write-Warn2 'executable not found on PATH yet (open a new terminal)' }

# ── fish integration (fish is already installed on device 1) ────────────────
if (-not $NoFish) {
    Write-Step 'wiring the fish function `lfs`'
    $fishDir = Join-Path $env:APPDATA 'fish'
    $fishFnDir = Join-Path $fishDir 'functions'
    $fishCompDir = Join-Path $fishDir 'completions'
    New-Item -ItemType Directory -Force -Path $fishFnDir, $fishCompDir | Out-Null

    $localFish = Join-Path $PSScriptRoot 'shell\windows\lfs.fish'
    if (Test-Path $localFish) {
        Copy-Item $localFish (Join-Path $fishFnDir 'lfs.fish') -Force
        Write-Ok "fish function -> $fishFnDir\lfs.fish"
    } else {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "$RawBase/shell/windows/lfs.fish" `
                -OutFile (Join-Path $fishFnDir 'lfs.fish')
            Write-Ok "downloaded fish function -> $fishFnDir\lfs.fish"
        } catch { Write-Warn2 "could not write the fish function: $_" }
    }
    $exe = if ($lfsCmd) { ($lfsCmd.Source ?? ($lfsCmd -as [string])) } else { 'local-file-sharer' }
    try {
        & $python -m local_file_sharer completions fish 2>$null |
            Set-Content -Encoding UTF8 (Join-Path $fishCompDir 'lfs.fish')
        Write-Ok 'fish completions installed'
    } catch { Write-Warn2 'fish completions skipped' }
    Write-Host '   open a new fish session, then type: lfs' -ForegroundColor DarkGray
}

# ── lfs.cmd (cmd.exe + PowerShell) ─────────────────────────────────────────
Write-Step 'installing the lfs.cmd launcher'
$binDir = Join-Path $env:LOCALAPPDATA 'local-file-sharer\bin'
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$localCmd = Join-Path $PSScriptRoot 'shell\windows\lfs.cmd'
if (Test-Path $localCmd) {
    Copy-Item $localCmd (Join-Path $binDir 'lfs.cmd') -Force
} else {
    Invoke-WebRequest -UseBasicParsing -Uri "$RawBase/shell/windows/lfs.cmd" -OutFile (Join-Path $binDir 'lfs.cmd')
}
# `share.cmd` was the old name; remove it so PATH has one launcher
$staleShare = Join-Path $binDir 'share.cmd'
if (Test-Path $staleShare) { Remove-Item $staleShare -Force }
Write-Ok "lfs.cmd -> $binDir\lfs.cmd"

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathParts = @()
if ($userPath) { $pathParts = $userPath -split ';' | Where-Object { $_ } }
if ($pathParts -notcontains $binDir) {
    [Environment]::SetEnvironmentVariable('Path', (($pathParts + $binDir) -join ';'), 'User')
    Write-Ok "added $binDir to your user PATH (open a new terminal)"
}
$env:Path = "$env:Path;$binDir"

# ── PowerShell completion + optional tools ─────────────────────────────────
Write-Step 'installing PowerShell completion'
$profileDir = Split-Path -Parent $PROFILE.CurrentUserAllHosts
if (-not (Test-Path $profileDir)) { New-Item -ItemType Directory -Force -Path $profileDir | Out-Null }
$compFile = Join-Path $binDir 'local-file-sharer-completions.ps1'
try {
    & $python -m local_file_sharer completions powershell | Set-Content -Encoding UTF8 $compFile
    $sourceLine = ". `"$compFile`""
    if (-not (Test-Path $PROFILE.CurrentUserAllHosts) -or
        -not (Select-String -Path $PROFILE.CurrentUserAllHosts -SimpleMatch $sourceLine -Quiet)) {
        Add-Content -Path $PROFILE.CurrentUserAllHosts -Value "`n# local-file-sharer completions`n$sourceLine"
    }
    Write-Ok "completion -> $compFile (dot-sourced from your profile)"
} catch { Write-Warn2 'completion setup skipped' }

if (-not $NoExtras) {
    Write-Step 'optional tools (aria2 for parallel downloads, tunnels)'
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not (Get-Command aria2c -ErrorAction SilentlyContinue) -and $winget) {
        if ($Yes) {
            winget install --id aria2.aria2 -e --accept-source-agreements --accept-package-agreements | Out-Null
            Write-Ok 'aria2 installed'
        } else {
            Write-Warn2 'aria2 not found - install for 16-connection downloads: winget install aria2.aria2'
        }
    }
    if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
        Write-Host '   tailscale not found (optional): winget install tailscale.tailscale' -ForegroundColor DarkGray
    }
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        Write-Host '   cloudflared not found (optional): winget install Cloudflare.cloudflared' -ForegroundColor DarkGray
    }
}

# ── summary ─────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host ' done - local-file-sharer is ready' -ForegroundColor Green
Write-Host @"
   any shell:   lfs                  (lfs.cmd is on PATH; fish gets a function)
   menu         lfs
   doctor       lfs doctor
   config       lfs config show
   update       lfs upgrade

   config: %APPDATA%\local-file-sharer\config.toml
   docs:   https://github.com/$RepoSlug#readme
"@ -ForegroundColor DarkGray
