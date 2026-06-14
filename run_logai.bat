@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

echo ============================================
echo   LogAI 4.4.3 - TRPG Log Analysis Server
echo ============================================
echo.

:: ============================================================
::  CONFIG - Edit your settings below
:: ============================================================
:: AI API Key (DeepSeek / OpenAI compatible)
set CFG_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
:: AI Base URL and model names
set CFG_API_BASE_URL=https://api.deepseek.com
set CFG_AI_MODEL=deepseek-v4-flash
set CFG_AI_MODEL_PRO=deepseek-v4-pro
:: Image generation API Key (NovelAI)
set CFG_IMAGE_API_KEY=pst-zzzzzzzzzzzzzzzzzzzz
:: NapCat HTTP / WebSocket connection
set CFG_NAPCAT_URL=http://127.0.0.1:8084
set CFG_NAPCAT_TOKEN=1
set CFG_WS_URL=ws://127.0.0.1:3001
set CFG_WS_TOKEN=
:: Server listen address and port
set CFG_HOST=0.0.0.0
set CFG_PORT=8000
:: Internal parameters (usually leave as-is)
set CFG_BRIDGE_TOKEN=
set CFG_BRIDGE_PUBLIC_BASE=
set CFG_WS_ENABLED=1
set CFG_BRIDGE_MODE=0
:: ============================================================

:: --- Python-Build-Standalone config ---
set PBS_TAG=20260211
set PBS_REPO=astral-sh/python-build-standalone
set PBS_FILENAME=cpython-3.11.14+%PBS_TAG%-x86_64-pc-windows-msvc-install_only.tar.gz

:: Mirror URLs (tried in order)
set MIRROR1=https://gitee.com/masx200/python-build-standalone/releases/download/%PBS_TAG%/%PBS_FILENAME%
set MIRROR2=https://ghproxy.cc/https://github.com/%PBS_REPO%/releases/download/%PBS_TAG%/%PBS_FILENAME%
set MIRROR3=https://mirror.ghproxy.com/https://github.com/%PBS_REPO%/releases/download/%PBS_TAG%/%PBS_FILENAME%
set DIRECT_URL=https://github.com/%PBS_REPO%/releases/download/%PBS_TAG%/%PBS_FILENAME%

:: --- Locate or install Python ---
set PYTHON_DIR=%~dp0python
set PYTHON_EXE=%PYTHON_DIR%\python.exe

if exist "%PYTHON_EXE%" (
    echo [OK] Using local Python: %PYTHON_EXE%
    goto :deps
)

:: --- Auto-install portable Python ---
echo [INFO] Portable Python not found, downloading Python 3.11...
echo [INFO] Target: Windows x86_64
echo.

set PYTHON_ARCHIVE=%TEMP%\python-portable.tar.gz
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

set DOWNLOAD_SUCCESS=0
for %%M in ("%MIRROR1%" "%MIRROR2%" "%MIRROR3%" "%DIRECT_URL%") do (
    if !DOWNLOAD_SUCCESS! equ 0 (
        echo [INFO] Trying: %%~M
        powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%%~M' -OutFile '%PYTHON_ARCHIVE%'}" 2>nul
        if !errorlevel! equ 0 (
            if exist "%PYTHON_ARCHIVE%" (
                set DOWNLOAD_SUCCESS=1
                echo [OK] Download success
            )
        )
    )
)

if !DOWNLOAD_SUCCESS! equ 0 (
    echo [ERROR] Failed to download Python.
    echo.
    echo Manual install options:
    echo   1. Open: https://github.com/%PBS_REPO%/releases/tag/%PBS_TAG%
    echo   2. Download: %PBS_FILENAME%
    echo   3. Place it at: %PYTHON_ARCHIVE%
    echo   4. Re-run this script
    echo.
    pause
    exit /b 1
)

echo [INFO] Extracting Python to %PYTHON_DIR% ...
:: --strip-components=1 removes the top-level directory inside the archive (matching .sh behavior)
tar -xf "%PYTHON_ARCHIVE%" -C "%PYTHON_DIR%" --strip-components=1 2>nul
if !errorlevel! neq 0 (
    powershell -Command "Expand-Archive -Path '%PYTHON_ARCHIVE%' -DestinationPath '%PYTHON_DIR%' -Force" 2>nul
)
del /q "%PYTHON_ARCHIVE%" 2>nul

if not exist "%PYTHON_EXE%" (
    for /r "%PYTHON_DIR%" %%F in (python.exe) do (
        set PYTHON_EXE=%%F
        goto :found_python
    )
    echo [ERROR] python.exe not found after extraction
    echo [DEBUG] Contents of %PYTHON_DIR%:
    dir "%PYTHON_DIR%" /b /s | findstr "python.exe"
    pause
    exit /b 1
)
:found_python

echo [OK] Python installed: %PYTHON_EXE%

:deps
echo [INFO] Python version:
%PYTHON_EXE% --version
echo.

echo [INFO] Installing dependencies (using Tsinghua mirror)...
%PYTHON_EXE% -m ensurepip --upgrade 2>nul
%PYTHON_EXE% -m pip install --disable-pip-version-check ^
    -i https://pypi.tuna.tsinghua.edu.cn/simple/ ^
    --trusted-host pypi.tuna.tsinghua.edu.cn ^
    flask requests pillow openai python-docx PyPDF2 pymupdf websockets

if !errorlevel! equ 0 (
    echo [OK] Dependencies ready
) else (
    echo [WARN] Some dependencies may have failed to install, trying to continue...
    echo [TIP] Run manually: %PYTHON_EXE% -m pip install flask requests pillow openai python-docx PyPDF2 pymupdf websockets
)
echo.

:: --- Build CLI args from config ---
set CLI_ARGS=
if not "%CFG_API_KEY%"==""            set CLI_ARGS=%CLI_ARGS% --api-key "%CFG_API_KEY%"
if not "%CFG_API_BASE_URL%"==""       set CLI_ARGS=%CLI_ARGS% --api-base-url "%CFG_API_BASE_URL%"
if not "%CFG_AI_MODEL%"==""           set CLI_ARGS=%CLI_ARGS% --ai-model "%CFG_AI_MODEL%"
if not "%CFG_AI_MODEL_PRO%"==""       set CLI_ARGS=%CLI_ARGS% --ai-model-pro "%CFG_AI_MODEL_PRO%"
if not "%CFG_IMAGE_API_KEY%"==""      set CLI_ARGS=%CLI_ARGS% --image-api-key "%CFG_IMAGE_API_KEY%"
if not "%CFG_HOST%"==""               set CLI_ARGS=%CLI_ARGS% --host "%CFG_HOST%"
if not "%CFG_PORT%"==""               set CLI_ARGS=%CLI_ARGS% --port "%CFG_PORT%"
if not "%CFG_NAPCAT_URL%"==""         set CLI_ARGS=%CLI_ARGS% --napcat-url "%CFG_NAPCAT_URL%"
if not "%CFG_NAPCAT_TOKEN%"==""       set CLI_ARGS=%CLI_ARGS% --napcat-token "%CFG_NAPCAT_TOKEN%"
if not "%CFG_WS_URL%"==""             set CLI_ARGS=%CLI_ARGS% --ws-url "%CFG_WS_URL%"
if not "%CFG_WS_TOKEN%"==""           set CLI_ARGS=%CLI_ARGS% --ws-token "%CFG_WS_TOKEN%"
if not "%CFG_BRIDGE_TOKEN%"==""       set CLI_ARGS=%CLI_ARGS% --bridge-token "%CFG_BRIDGE_TOKEN%"
if not "%CFG_BRIDGE_PUBLIC_BASE%"=="" set CLI_ARGS=%CLI_ARGS% --bridge-public-base "%CFG_BRIDGE_PUBLIC_BASE%"
if not "%CFG_WS_ENABLED%"==""         set CLI_ARGS=%CLI_ARGS% --ws-enabled "%CFG_WS_ENABLED%"
if not "%CFG_BRIDGE_MODE%"==""        set CLI_ARGS=%CLI_ARGS% --bridge-mode "%CFG_BRIDGE_MODE%"

:: --- Launch server ---
echo [INFO] Starting LogAI server (Ctrl+C to stop)...
echo.
cd /d "%~dp0"
<NUL ( %PYTHON_EXE% logai_server_release.py %CLI_ARGS% %* )
