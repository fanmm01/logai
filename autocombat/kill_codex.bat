@echo off
rem ============================================================
rem  Force-stop all OpenAI Codex processes.
rem  Usage:  kill_codex.bat            (kill once and verify)
rem          kill_codex.bat -ListOnly  (just list, kill nothing)
rem          kill_codex.bat -Watch     (keep sweeping until Ctrl+C)
rem          kill_codex.bat -DisableService  (elevated: stop auto-start too)
rem ============================================================
setlocal
chcp 65001 >nul

if not exist "%~dp0kill_codex.ps1" (
    echo [error] kill_codex.ps1 not found next to this batch file.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kill_codex.ps1" %*

set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo [done] No Codex process is running.
) else (
    echo [warn] Some Codex processes survived - close the VS Code ChatGPT/Codex panel and retry.
    echo        Or run:  kill_codex.bat -Watch
)
if "%~1"=="" pause
exit /b %RC%
