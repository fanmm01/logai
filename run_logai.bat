@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   LogAI 4.1.0 - TRPG Log Analysis Server
echo ============================================
echo.

:: ============================================================
::  配置区域 — 在此修改默认值
:: ============================================================
:: 请将使用的AI API令牌替换下面的"sk-………"。切勿将此令牌透露给其他人！
set CFG_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
:: 使用的AI的相关信息，包括base url与ai模型名称
set CFG_API_BASE_URL=https://api.deepseek.com
set CFG_AI_MODEL=deepseek-v4-flash
set CFG_AI_MODEL_PRO=deepseek-v4-pro
:: 文生图AI的API令牌。同样切勿透露！
set CFG_IMAGE_API_KEY=pst-zzzzzzzzzzzzzzzzzzzz
::#以下为Napcat的http/ws连接url及token。请按需修改。
set CFG_NAPCAT_URL=http://127.0.0.1:8084
set CFG_NAPCAT_TOKEN=1
set CFG_WS_URL=ws://127.0.0.1:3001
set CFG_WS_TOKEN=
set CFG_HOST=0.0.0.0
:: logai后端运行的端口号。如提示端口被占用请修改。但是这是不建议的行动；此处若进行了修改，则需要把前端配置进行同样的修改。
set CFG_PORT=8000
set CFG_BRIDGE_TOKEN=
set CFG_BRIDGE_PUBLIC_BASE=
set CFG_WS_ENABLED=1
set CFG_BRIDGE_MODE=0
:: ============================================================

:: --- 配置 Python-Build-Standalone ---
set PBS_TAG=20260211
set PBS_REPO=astral-sh/python-build-standalone
set PBS_FILENAME=cpython-3.11.14+%PBS_TAG%-x86_64-pc-windows-msvc-install_only.tar.gz

:: 国内镜像加速（按优先级尝试）
set MIRROR1=https://ghproxy.cc/https://github.com/%PBS_REPO%/releases/download/%PBS_TAG%/%PBS_FILENAME%
set MIRROR2=https://mirror.ghproxy.com/https://github.com/%PBS_REPO%/releases/download/%PBS_TAG%/%PBS_FILENAME%
set DIRECT_URL=https://github.com/%PBS_REPO%/releases/download/%PBS_TAG%/%PBS_FILENAME%

:: --- Locate or install Python ---
set PYTHON_DIR=%~dp0python
set PYTHON_EXE=%PYTHON_DIR%\python.exe

:: 仅检测本地便携 Python
if exist "%PYTHON_EXE%" (
    echo [OK] 使用本地 Python: %PYTHON_EXE%
    goto :deps
)

:: --- Auto-install portable Python (python-build-standalone) ---
echo [INFO] 未找到本地便携 Python，正在自动下载 Python 3.11...
echo [INFO] 目标: Windows x86_64
echo.

set PYTHON_ARCHIVE=%TEMP%\python-portable.tar.gz
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

:: 尝试镜像下载
set DOWNLOAD_SUCCESS=0
for %%M in ("%MIRROR1%" "%MIRROR2%" "%DIRECT_URL%") do (
    if !DOWNLOAD_SUCCESS! equ 0 (
        echo [INFO] 尝试下载: %%~M
        powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%%~M' -OutFile '%PYTHON_ARCHIVE%'}" 2>nul
        if !errorlevel! equ 0 (
            if exist "%PYTHON_ARCHIVE%" (
                set DOWNLOAD_SUCCESS=1
                echo [OK] 下载成功
            )
        )
    )
)

if !DOWNLOAD_SUCCESS! equ 0 (
    echo [ERROR] ❌ 下载 Python 失败。
    echo.
    echo 请选择以下方案：
    echo.
    echo 方案A — 手动下载并放置：
    echo   1. 打开: https://github.com/%PBS_REPO%/releases/tag/%PBS_TAG%
    echo   2. 下载文件: %PBS_FILENAME%
    echo   3. 将压缩包放到: %PYTHON_ARCHIVE%
    echo   4. 重新运行此脚本
    echo.
    pause
    exit /b 1
)

echo [INFO] 解压 Python 到 %PYTHON_DIR% ...
:: 使用 tar 解压（Windows 10+ 自带）
tar -xf "%PYTHON_ARCHIVE%" -C "%PYTHON_DIR%" 2>nul
if !errorlevel! neq 0 (
    :: 尝试用 PowerShell 解压
    powershell -Command "Expand-Archive -Path '%PYTHON_ARCHIVE%' -DestinationPath '%PYTHON_DIR%' -Force" 2>nul
)
del /q "%PYTHON_ARCHIVE%" 2>nul

:: 查找 python.exe（python-build-standalone 解压后可能有嵌套目录）
if not exist "%PYTHON_EXE%" (
    for /r "%PYTHON_DIR%" %%F in (python.exe) do (
        set PYTHON_EXE=%%F
        goto :found_python
    )
    echo [ERROR] Python 解压后未找到 python.exe
    echo [DEBUG] %PYTHON_DIR% 内容:
    dir "%PYTHON_DIR%" /b /s | findstr "python.exe"
    pause
    exit /b 1
)
:found_python

echo [OK] Python 安装完成: %PYTHON_EXE%

:deps
echo [INFO] Python 版本:
%PYTHON_EXE% --version
echo.

echo [INFO] 检查并安装依赖包（使用清华镜像加速）...
%PYTHON_EXE% -m pip install -q --disable-pip-version-check ^
    -i https://pypi.tuna.tsinghua.edu.cn/simple/ ^
    --trusted-host pypi.tuna.tsinghua.edu.cn ^
    flask requests pillow openai python-docx PyPDF2 pymupdf websockets 2>nul

if !errorlevel! equ 0 (
    echo [OK] 依赖包准备完毕
) else (
    echo [WARN] 部分依赖包安装可能失败，尝试继续启动...
    echo [TIP] 可手动运行: %PYTHON_EXE% -m pip install -r requirements.txt
)
echo.

:: --- Build CLI args from config variables ---
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
echo [INFO] 启动 LogAI 服务器...
cd /d "%~dp0"
%PYTHON_EXE% logai_server_release.py %CLI_ARGS% %*

pause
