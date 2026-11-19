[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [string]$Python = 'python',
    [ValidateRange(5, 300)][int]$StartupTimeoutSeconds = 30
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root '.ui-runtime'
$lock = $null
$serviceProcesses = [System.Collections.Generic.List[object]]::new()
$previousLocation = Get-Location

function Get-ServiceProcess {
    param([int]$ProcessId, [string]$Entry)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if (-not $process -or $process.Name -notmatch '^python(?:w|\d+(?:\.\d+)*)?\.exe$') {
        throw "Port owner PID $ProcessId is not a Python service. Nothing was stopped."
    }
    # Accept the current entry point and this checkout's previous absolute path.
    $entryPattern = '(?:^|\s)(?:"' + [regex]::Escape((Join-Path $root $Entry)) +
        '"|"?' + [regex]::Escape($Entry) + '"?|'+
        [regex]::Escape((Join-Path $root $Entry)) + ')(?:\s|$)'
    $legacyEntry = Join-Path $root (Split-Path -Leaf $Entry)
    $legacyPattern = '(?:^|\s)"?' + [regex]::Escape($legacyEntry) + '"?(?:\s|$)'
    if ($process.CommandLine -notmatch $entryPattern -and $process.CommandLine -notmatch $legacyPattern) {
        throw "Port owner PID $ProcessId is not the expected $Entry service. Nothing was stopped."
    }
    return $process
}

function Wait-Service {
    param($Process, [int]$Port)
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    do {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "Service on port $Port exited with code $($Process.ExitCode). See logs in $runtime."
        }
        try {
            $ping = Invoke-RestMethod "http://127.0.0.1:$Port/api/pvp/ping" -TimeoutSec 2
            $page = Invoke-WebRequest "http://127.0.0.1:$Port/ui/" -UseBasicParsing -TimeoutSec 2
            $listener = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
                Where-Object { $_.OwningProcess -eq $Process.Id })
            if ($ping.status -eq 'ok' -and $page.StatusCode -eq 200 -and $listener.Count -gt 0) {
                return
            }
        } catch {
            # The socket may not be ready yet; retry until the deadline.
        }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    throw "Service on port $Port did not become ready. See logs in $runtime."
}

function Start-ServiceProcess {
    param([string]$Entry, [int]$Port, [string[]]$ExtraArguments = @())
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdout = Join-Path $runtime "restart-$Port-$stamp.out.log"
    $stderr = Join-Path $runtime "restart-$Port-$stamp.err.log"
    $savedInstance = $env:AUTOCOMBAT_UI_INSTANCE
    try {
        $env:AUTOCOMBAT_UI_INSTANCE = [string]$Port
        $arguments = @('-u', ('"' + (Join-Path $root $Entry) + '"')) + $ExtraArguments
        $process = Start-Process -FilePath $script:pythonExe -ArgumentList $arguments `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        # Track immediately so startup failures and Ctrl+C also trigger cleanup.
        $serviceProcesses.Add([pscustomobject]@{ Process = $process; Port = $Port })
    } finally {
        $env:AUTOCOMBAT_UI_INSTANCE = $savedInstance
    }
    Set-Content -LiteralPath (Join-Path $runtime "$Port.pid") -Value $process.Id -Encoding ASCII
    Write-Host "Starting $Entry (PID $($process.Id), port $Port)"
    Write-Host "  stdout: $stdout"
    Write-Host "  stderr: $stderr"
    Wait-Service -Process $process -Port $Port
    return $process
}

try {
    Set-Location -LiteralPath $root
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    # File ownership, rather than stale PID files, serializes restart attempts.
    $lock = [System.IO.File]::Open((Join-Path $runtime 'restart.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    foreach ($entry in @('backend/battle_http_server.py', 'scripts/merge_backend.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $entry))) { throw "Missing $entry" }
    }
    $pythonOutput = & $Python -c 'import sys, flask; print(sys.executable)'
    if ($LASTEXITCODE -ne 0) { throw 'Python with Flask is required. Install Flask with: python -m pip install flask' }
    $script:pythonExe = ($pythonOutput | Select-Object -Last 1).Trim()
    if (-not (Test-Path -LiteralPath $script:pythonExe)) { throw 'Could not resolve the Python executable.' }

    # Complete builds before interrupting the running services.
    if (-not $SkipBuild) {
        $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
        Push-Location -LiteralPath (Join-Path $root 'frontend')
        try {
            if (-not (Test-Path -LiteralPath 'node_modules/vite')) {
                & $npm ci --no-audit --no-fund
                if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            }
            & $npm run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed; running services were left running.' }
        } finally { Pop-Location }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $root 'frontend/dist/index.html'))) {
        throw 'Frontend build is missing. Run again without -SkipBuild.'
    }
    & $script:pythonExe (Join-Path $root 'scripts/merge_backend.py')
    if ($LASTEXITCODE -ne 0) { throw 'Backend generation failed; running services were left running.' }

    $expected = @{ 8889 = 'backend/battle_http_server.py'; 8890 = 'backend/autocombat_backend.py'; 8892 = 'backend/autocombat_backend.py' }
    $targets = @{}
    foreach ($port in @(8889, 8890, 8892)) {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        foreach ($listener in $listeners) {
            $process = Get-ServiceProcess -ProcessId $listener.OwningProcess -Entry $expected[$port]
            $targets[[int]$process.ProcessId] = $expected[$port]
        }
    }
    Write-Host 'Restarting services. In-memory battles will be cleared; persistent data is retained.'
    foreach ($targetId in $targets.Keys) {
        # Revalidate just before stopping; never kill unrelated Python sessions.
        $null = Get-ServiceProcess -ProcessId $targetId -Entry $targets[$targetId]
        & "$env:SystemRoot\System32\taskkill.exe" /PID $targetId /T /F
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $targetId -ErrorAction SilentlyContinue)) {
            throw "Could not stop PID $targetId."
        }
    }
    $deadline = (Get-Date).AddSeconds(10)
    do {
        $remaining = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalPort -in @(8889, 8890, 8892) })
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    if ($remaining.Count -gt 0) { throw 'Service ports are still occupied; restart aborted.' }

    $null = Start-ServiceProcess -Entry 'backend/battle_http_server.py' -Port 8889
    $integrated = Start-ServiceProcess -Entry 'backend/autocombat_backend.py' -Port 8890 `
        -ExtraArguments @('--host', '0.0.0.0', '--port', '8890', '--tcp-port', '8892')
    $tunnel = Invoke-RestMethod 'http://127.0.0.1:8892/api/pvp/ping' -TimeoutSec 5
    $tunnelListener = @(Get-NetTCPConnection -State Listen -LocalPort 8892 |
        Where-Object { $_.OwningProcess -eq $integrated.Id })
    if ($tunnel.status -ne 'ok' -or $tunnelListener.Count -eq 0) { throw 'TCP tunnel health check failed.' }
    Write-Host ''
    Write-Host 'Restart completed. All service checks passed.' -ForegroundColor Green
    Write-Host 'Battle UI:     http://127.0.0.1:8889/ui/'
    Write-Host 'Integrated UI: http://127.0.0.1:8890/ui/'
    Write-Host 'TCP tunnel:    0.0.0.0:8892 -> 127.0.0.1:8890'
    Write-Host 'Monitoring services. Press Ctrl+C to stop all service processes.'
    while ($true) {
        foreach ($service in $serviceProcesses) {
            $service.Process.Refresh()
            if ($service.Process.HasExited) {
                throw "Service on port $($service.Port) exited with code $($service.Process.ExitCode). See logs in $runtime."
            }
        }
        Start-Sleep -Milliseconds 500
    }
} catch {
    Write-Host "Restart failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    # PowerShell runs finally when Ctrl+C interrupts the monitoring loop.
    foreach ($service in $serviceProcesses) {
        try {
            $service.Process.Refresh()
            if (-not $service.Process.HasExited) {
                Write-Host "Stopping service on port $($service.Port) (PID $($service.Process.Id))"
                & "$env:SystemRoot\System32\taskkill.exe" /PID $service.Process.Id /T /F
                if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $service.Process.Id -ErrorAction SilentlyContinue)) {
                    Write-Warning "Could not stop service PID $($service.Process.Id)."
                }
            }
            $pidFile = Join-Path $runtime "$($service.Port).pid"
            if ((Test-Path -LiteralPath $pidFile) -and
                (Get-Content -LiteralPath $pidFile -Raw).Trim() -eq [string]$service.Process.Id) {
                Remove-Item -LiteralPath $pidFile -Force
            }
        } catch {
            Write-Warning "Service cleanup failed: $($_.Exception.Message)"
        } finally {
            $service.Process.Dispose()
        }
    }
    if ($lock) { $lock.Dispose() }
    Set-Location -LiteralPath $previousLocation.Path
}
