@echo off
setlocal
pushd "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart_services.ps1" %*
set "restart_exit=%errorlevel%"
popd
echo.
if not "%restart_exit%"=="0" echo Restart failed. See the message above and .ui-runtime logs.
pause
exit /b %restart_exit%
