@echo off
chcp 65001 >nul
echo ============================================
echo   Kill all battle_http_server.py processes
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$procs = Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='python3.exe'\" | Where-Object { $_.CommandLine -match 'battle_http_server\.py' }; ^
    if ($procs.Count -eq 0) { Write-Host 'No battle_http_server.py processes found.' } ^
    else { ^
        foreach ($p in $procs) { ^
            Write-Host \"Killing PID $($p.ProcessId) - $($p.CommandLine)\"; ^
            Stop-Process -Id $p.ProcessId -Force; ^
            Write-Host '  [OK] Killed.' ^
        } ^
    }"

echo.
echo Done.
pause
