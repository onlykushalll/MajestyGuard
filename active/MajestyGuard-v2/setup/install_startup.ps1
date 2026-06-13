param(
    [string]$TaskName = "MajestyGuard_UserDaemon",
    [string]$PythonwPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Daemon = Join-Path $Root "daemon\mg_monitor.py"
$PolicyAudit = Join-Path $Root "daemon\mg_policy_audit.py"
$StartupCommandName = "MajestyGuard_UserDaemon.cmd"
if ($TaskName -ne "MajestyGuard_UserDaemon") {
    $StartupCommandName = "$TaskName.cmd"
}

function Install-StartupFolderFallback {
    param(
        [string]$CommandName,
        [string]$Command
    )
    $StartupDir = [Environment]::GetFolderPath("Startup")
    if (!(Test-Path $StartupDir)) {
        New-Item -ItemType Directory -Path $StartupDir -Force | Out-Null
    }
    $Path = Join-Path $StartupDir $CommandName
    $Content = @(
        "@echo off",
        "cd /d `"$Root`"",
        $Command
    ) -join "`r`n"
    Set-Content -LiteralPath $Path -Value $Content -Encoding ASCII
    return $Path
}

function Resolve-Pythonw {
    param([string]$Override)
    $candidates = @()
    if ($Override) { $candidates += $Override }
    if ($env:MG_PYTHONW) { $candidates += $env:MG_PYTHONW }
    $candidates += (Join-Path $Root ".venv\Scripts\pythonw.exe")
    $candidates += "C:\tmp\MajestyGuard\src\MajestyGuard.CVEngine\.venv\Scripts\pythonw.exe"
    $python = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($python) { $candidates += $python.Source }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return (Resolve-Path $candidate).Path
        }
    }
    throw "pythonw.exe was not found. Set MG_PYTHONW or pass -PythonwPath."
}

if (!(Test-Path $Daemon)) {
    throw "Daemon not found: $Daemon"
}

$Pythonw = Resolve-Pythonw $PythonwPath
$Python = $Pythonw -replace "pythonw\.exe$", "python.exe"
if (!(Test-Path $Python)) {
    $Python = $Pythonw
}

$env:MG_ENABLE_LOCK = "0"
$env:MG_IDLE_TIMEOUT = "90"
$env:MG_PASSIVE_FPS = "0"
$env:MG_OVERLAY_WATCHDOG = "1"
$env:MG_ENABLE_WHCDF_IPC = "0"
$env:MG_ENABLE_SERVICE_IPC = "0"
$env:MG_ADAFACE_FLIP_FUSION = "1"
& $Python $PolicyAudit
if ($LASTEXITCODE -ne 0) {
    throw "Policy audit failed; startup task was not installed."
}

$Command = "set MG_ENABLE_LOCK=0&& set MG_IDLE_TIMEOUT=90&& set MG_PASSIVE_FPS=0&& set MG_OVERLAY_WATCHDOG=1&& set MG_ENABLE_WHCDF_IPC=0&& set MG_ENABLE_SERVICE_IPC=0&& set MG_ADAFACE_FLIP_FUSION=1&& `"$Pythonw`" `"$Daemon`""
$InstalledMode = "ScheduledTask"
try {
    $Action = New-ScheduledTaskAction -Execute "$env:ComSpec" -Argument "/d /c $Command" -WorkingDirectory $Root
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Days 0)

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Starts the MajestyGuard user-space daemon at logon." `
        -Force | Out-Null
    Write-Host "Installed user startup task: $TaskName"
} catch {
    Write-Warning "Task Scheduler registration failed: $($_.Exception.Message)"
    $InstalledMode = "StartupFolder"
    $FallbackPath = Install-StartupFolderFallback -CommandName $StartupCommandName -Command $Command
    Write-Host "Installed Startup folder fallback: $FallbackPath"
}

Write-Host "Startup mode: $InstalledMode"
Write-Host "Daemon: $Daemon"
Write-Host "Python: $Pythonw"
