<#
.SYNOPSIS
    Force-stop every OpenAI Codex process on this machine, then verify none remain.

.DESCRIPTION
    Codex runs as several different process types:
      * codex.exe                     - CLI sessions and the VS Code ChatGPT/Codex extension app-server
      * codex-code-mode-host.exe      - gRPC/stdio helper spawned by codex.exe
      * codex-computer-use*.exe       - computer-use helpers
      * codex-windows-sandbox-service.exe - Windows service (CodexSandboxService.OpenAI.Codex)
      * node.exe / node_repl.exe      - runtimes launched from OpenAI\Codex, %USERPROFILE%\.codex,
                                        %USERPROFILE%\.cache\codex-runtimes or the chatgpt extension

    Killing a codex.exe often makes the VS Code extension host launch a new one a few
    seconds later, so this script sweeps repeatedly (MaxPasses) and re-checks after a
    settle delay. When it reports "Clean", nothing is left running.

    Exit codes: 0 = clean, 1 = processes are still alive (normally the extension respawning).

.PARAMETER ListOnly
    Only list matching processes. Nothing is stopped.

.PARAMETER DryRun
    Show what would be killed without killing anything.

.PARAMETER DisableService
    Also set CodexSandboxService.OpenAI.Codex startup type to Disabled so it does not
    come back after a reboot. Needs an elevated prompt.

.PARAMETER Watch
    Keep sweeping every IntervalSeconds until Ctrl+C. Useful against extensions that
    aggressively respawn codex.

.PARAMETER IntervalSeconds
    Watch-mode sweep interval (default 10).

.PARAMETER MaxPasses
    Maximum kill passes per sweep (default 5).

.PARAMETER SettleSeconds
    Wait between passes and before the final check (default 2).

.EXAMPLE
    .\kill_codex.ps1

.EXAMPLE
    .\kill_codex.ps1 -ListOnly

.EXAMPLE
    .\kill_codex.ps1 -DisableService

.EXAMPLE
    .\kill_codex.ps1 -Watch -IntervalSeconds 5
#>
[CmdletBinding()]
param(
    [switch]$ListOnly,
    [switch]$DryRun,
    [switch]$DisableService,
    [switch]$Watch,
    [ValidateRange(1, 3600)][int]$IntervalSeconds = 10,
    [ValidateRange(1, 50)][int]$MaxPasses = 5,
    [ValidateRange(0, 30)][int]$SettleSeconds = 2
)

$ErrorActionPreference = 'Continue'

$ServiceName = 'CodexSandboxService.OpenAI.Codex'
$script:TotalKilled = 0
$script:TotalFailed = 0

function Get-CodexProcesses {
    <# Returns every process that belongs to a Codex install, service or runtime. #>
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $name = $_.Name
        $exe = $_.ExecutablePath
        $cmd = $_.CommandLine

        if (-not $name) { return $false }

        # codex.exe, codex-code-mode-host.exe, codex-computer-use*.exe,
        # codex-windows-sandbox-service.exe, ...
        if ($name -match '(?i)^codex') { return $true }

        # node.exe / node_repl.exe / helpers living inside Codex install or cache folders
        if ($exe -and $exe -match '(?i)\\(OpenAI\\Codex|\.codex|\.cache\\codex-runtimes|openai\.chatgpt-[^\\]+)\\') {
            return $true
        }

        # Global / npm entry points whose executable is a generic host
        # (our own kill_codex.ps1 / .bat does not match: the backslash before
        #  "codex" is required, and there it is an underscore)
        if ($cmd -and $cmd -match '(?i)(@openai[/\\]codex|\\codex\.(exe|cmd|ps1)\b)') { return $true }

        return $false
    }
}

function Format-CodexProcess {
    param($Process)
    $exe = $Process.ExecutablePath
    if (-not $exe) { $exe = '<path unavailable>' }
    '  {0,-30} PID {1,-6} parent {2,-6} {3}' -f $Process.Name, $Process.ProcessId, $Process.ParentProcessId, $exe
}

function Show-CodexProcesses {
    $procs = @(Get-CodexProcesses | Sort-Object Name, ProcessId)
    if ($procs.Count -eq 0) {
        Write-Host 'No Codex processes running.'
        return
    }
    Write-Host ("{0} Codex process(es) running:" -f $procs.Count)
    foreach ($p in $procs) { Write-Host (Format-CodexProcess $p) }
}

function Stop-CodexService {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) { return }

    if ($svc.Status -ne 'Stopped') {
        if ($DryRun) {
            Write-Host "[dry-run] would stop service $ServiceName"
        } else {
            try {
                Stop-Service -Name $ServiceName -Force -ErrorAction Stop
                Write-Host "Stopped service $ServiceName."
            } catch {
                Write-Warning "Could not stop service ${ServiceName}: $($_.Exception.Message) (run from an elevated prompt)"
            }
        }
    }

    if ($DisableService) {
        if ($DryRun) {
            Write-Host "[dry-run] would set service $ServiceName startup type to Disabled"
        } else {
            try {
                Set-Service -Name $ServiceName -StartupType Disabled -ErrorAction Stop
                Write-Host "Service ${ServiceName} will no longer start automatically."
            } catch {
                Write-Warning "Could not disable service ${ServiceName}: $($_.Exception.Message) (run from an elevated prompt)"
            }
        }
    }
}

function Invoke-CodexSweep {
    for ($pass = 1; $pass -le $MaxPasses; $pass++) {
        $procs = @(Get-CodexProcesses | Sort-Object Name, ProcessId)
        if ($procs.Count -eq 0) { break }

        Write-Host ("Pass {0}/{1}: terminating {2} process(es)" -f $pass, $MaxPasses, $procs.Count)

        foreach ($p in $procs) {
            $label = '{0} (PID {1})' -f $p.Name, $p.ProcessId

            if ($DryRun) {
                Write-Host "  [dry-run] would kill $label"
                $script:TotalKilled++
                continue
            }

            & taskkill.exe /PID $p.ProcessId /T /F *> $null

            # /T already reaped most children, so a non-zero exit code is normal.
            # Judge by whether the process is actually gone, not by taskkill's code.
            $stillAlive = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ProcessId)" -ErrorAction SilentlyContinue
            if ($stillAlive) {
                Write-Host "  [fail] $label (still running - access denied?)"
                $script:TotalFailed++
            } else {
                Write-Host "  [ok]   $label"
                $script:TotalKilled++
            }
        }

        if ($DryRun) { break }
        if ($SettleSeconds -gt 0) { Start-Sleep -Seconds $SettleSeconds }
    }
}

Write-Host '=== Codex process cleanup ==='
Write-Host ("Time: {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Host ''

if ($ListOnly) {
    Show-CodexProcesses
    exit 0
}

if ($Watch) {
    Write-Host ("Watch mode: sweeping every {0}s. Press Ctrl+C to stop." -f $IntervalSeconds)
    Stop-CodexService
    while ($true) {
        $before = $script:TotalKilled
        Invoke-CodexSweep
        $remaining = @(Get-CodexProcesses)
        if ($script:TotalKilled -gt $before) {
            Write-Host ("Terminated {0} process(es); {1} still running." -f ($script:TotalKilled - $before), $remaining.Count)
        }
        if ($remaining.Count -gt 0 -and $SettleSeconds -gt 0) {
            Write-Host ("Still running after sweep: {0} (retrying in {1}s)" -f $remaining.Count, $IntervalSeconds)
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
}

Stop-CodexService
Invoke-CodexSweep

if ($SettleSeconds -gt 0) { Start-Sleep -Seconds $SettleSeconds }

$remaining = @(Get-CodexProcesses)
Write-Host ''

if ($remaining.Count -eq 0) {
    Write-Host ("Clean. {0} process(es) terminated; no Codex process is running." -f $script:TotalKilled)
    exit 0
}

Write-Warning ("{0} Codex process(es) are still alive:" -f $remaining.Count)
foreach ($p in $remaining) { Write-Host (Format-CodexProcess $p) }
Write-Host ''
Write-Host 'These are most likely being restarted by the VS Code ChatGPT/Codex extension host.'
Write-Host 'Close the ChatGPT/Codex panel in VS Code (or disable the extension), then run again.'
Write-Host 'Use -Watch to keep sweeping while the extension is closed.'
exit 1
