@echo off
rem ────────────────────────────────────────────────────────────────────────────
rem  LOCAL FILE SHARER · cmd.exe / PowerShell launcher
rem
rem  `lfs` is not a built-in in cmd.exe or PowerShell, so this is a real command
rem  on PATH rather than an alias. It finds the installed Python entry point and
rem  falls back to `python -m local_file_sharer`.
rem
rem  Usage:  lfs               → interactive menu
rem          lfs doctor      → diagnostics
rem          lfs D:\Photos    → share a folder immediately
rem ────────────────────────────────────────────────────────────────────────────
setlocal EnableExtensions

set "LFS_CMD="

rem 1) standalone build placed next to this script
if exist "%~dp0local-file-sharer.exe" set "LFS_CMD=%~dp0local-file-sharer.exe"

rem 2) the console script installed by pipx/pip
if not defined LFS_CMD (
    where local-file-sharer.exe >nul 2>nul && set "LFS_CMD=local-file-sharer.exe"
)
if not defined LFS_CMD (
    where local-file-sharer.cmd >nul 2>nul && set "LFS_CMD=local-file-sharer.cmd"
)

rem 3) the Python module (python / py launcher / pipx venv)
if not defined LFS_CMD (
    where python.exe >nul 2>nul && set "LFS_CMD=python.exe -m local_file_sharer"
)
if not defined LFS_CMD (
    where py.exe >nul 2>nul && set "LFS_CMD=py.exe -3 -m local_file_sharer"
)

if not defined LFS_CMD (
    echo [local-file-sharer] Python not found.
    echo   Install Python 3.9+ ^(winget install Python.Python.3.12^),
    echo   or run: powershell -ExecutionPolicy Bypass -File "%~dp0..\..\install.ps1"
    exit /b 127
)

rem UTF-8 output: the box-drawing and glyphs need it on a legacy console.
chcp 65001 >nul 2>nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

%LFS_CMD% %*
set "LFS_CODE=%ERRORLEVEL%"
endlocal & exit /b %LFS_CODE%
