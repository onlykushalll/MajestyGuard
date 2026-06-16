param(
    [switch]$InstallStartup = $true,
    [string]$PythonwPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PolicyAudit = Join-Path $Root "daemon\mg_policy_audit.py"
$InstallStartupScript = Join-Path $PSScriptRoot "install_startup.ps1"

$env:MG_ENABLE_LOCK = "0"
$env:MG_IDLE_TIMEOUT = "90"
$env:MG_PASSIVE_FPS = "0"
$env:MG_OVERLAY_WATCHDOG = "1"
$env:MG_ENABLE_WHCDF_IPC = "0"
$env:MG_ENABLE_SERVICE_IPC = "0"
$env:MG_ADAFACE_FLIP_FUSION = "1"

python $PolicyAudit
if ($LASTEXITCODE -ne 0) {
    throw "Policy audit failed."
}

if ($InstallStartup) {
    & $InstallStartupScript -PythonwPath $PythonwPath
}

Write-Host "MajestyGuard user-space setup complete."
