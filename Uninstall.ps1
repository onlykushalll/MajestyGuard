# MajestyGuard/Uninstall.ps1
# Run as Administrator for machine-level uninstall. Use -WhatIf for dry run.

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [switch]$DisableTestSigning,
    [switch]$RestoreScreensaver,
    [switch]$RemoveUserData
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "SilentlyContinue"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator) -and -not $WhatIfPreference) {
    throw "Machine-level uninstall requires Administrator. Use -WhatIf for a no-machine-state dry run."
}

$INSTALL_DIR  = "$env:ProgramFiles\MajestyGuard"
$PROGRAMDATA_DIR = Join-Path $env:ProgramData "MajestyGuard"
$SERVICE_NAME = "MajestyGuardService"
$CP_DLL       = "$INSTALL_DIR\MajestyGuard.CredentialProvider.dll"
$CP_CLSID     = "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"

Write-Host ""
Write-Host "  MajestyGuard Uninstaller" -ForegroundColor Red
Write-Host ""

Write-Host "[1/7] Stopping service..." -ForegroundColor Yellow
if ($PSCmdlet.ShouldProcess($SERVICE_NAME, "Stop and delete Windows service")) {
    Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
    sc.exe delete $SERVICE_NAME 2>&1 | Out-Null
}
Write-Host "    Service removed" -ForegroundColor Gray

Write-Host "[2/7] Unregistering Credential Provider..." -ForegroundColor Yellow
if ($PSCmdlet.ShouldProcess($CP_CLSID, "Unregister Credential Provider")) {
    if (Test-Path $CP_DLL) {
        Start-Process regsvr32 -ArgumentList "/s /u `"$CP_DLL`"" -Wait
    }
    Remove-Item -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$CP_CLSID" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path "HKLM:\SOFTWARE\Classes\CLSID\$CP_CLSID" -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "    Credential Provider unregistered" -ForegroundColor Gray

Write-Host "[3/7] Removing auto-start entries..." -ForegroundColor Yellow
if ($PSCmdlet.ShouldProcess("HKLM Run\MajestyGuardOverlay", "Remove overlay auto-start entry")) {
    Remove-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" -Name "MajestyGuardOverlay" -ErrorAction SilentlyContinue
}
Write-Host "    Auto-start removed" -ForegroundColor Gray

Write-Host "[4/7] Removing scheduled tasks..." -ForegroundColor Yellow
if ($PSCmdlet.ShouldProcess("MajestyGuard_ServiceGuard", "Unregister scheduled task")) {
    Unregister-ScheduledTask -TaskName "MajestyGuard_ServiceGuard" -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Host "    Scheduled tasks removed" -ForegroundColor Gray

if ($PSCmdlet.ShouldProcess("MajestyGuard SafeBoot entries", "Remove service and Credential Provider SafeBoot entries")) {
    foreach ($mode in @("Minimal", "Network")) {
        Remove-Item -Path "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\$mode\$SERVICE_NAME" -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\$mode\$CP_CLSID" -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "    SafeBoot entries removed" -ForegroundColor Gray

Write-Host "[5/7] Removing firewall rules..." -ForegroundColor Yellow
if ($PSCmdlet.ShouldProcess("MajestyGuard - Block CVEngine Outbound", "Remove firewall rule")) {
    Remove-NetFirewallRule -DisplayName "MajestyGuard - Block CVEngine Outbound" -ErrorAction SilentlyContinue
}
Write-Host "    Firewall rules removed" -ForegroundColor Gray

if ($DisableTestSigning) {
    if ($PSCmdlet.ShouldProcess("Windows boot configuration", "Disable test signing")) {
        bcdedit /deletevalue testsigning 2>&1 | Out-Null
    }
    Write-Host "    Windows test signing disabled (reboot may be required)" -ForegroundColor Gray
} else {
    Write-Host "    Test signing left unchanged. Pass -DisableTestSigning to turn it off." -ForegroundColor DarkGray
}

Write-Host "[6/7] Reverting hosts file..." -ForegroundColor Yellow
$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
if ($PSCmdlet.ShouldProcess($hostsPath, "Remove MajestyGuard SocialLock hosts entries and flush DNS")) {
    if (Test-Path $hostsPath) {
        $lines = Get-Content $hostsPath | Where-Object { $_ -notmatch "# MajestyGuard SocialLock" }
        $lines | Set-Content $hostsPath
        ipconfig /flushdns 2>&1 | Out-Null
    }
}
Write-Host "    Hosts file clean" -ForegroundColor Gray

if ($RestoreScreensaver) {
    if ($PSCmdlet.ShouldProcess("HKCU:\Control Panel\Desktop", "Restore Windows screensaver")) {
        Set-ItemProperty "HKCU:\Control Panel\Desktop" -Name "ScreenSaveActive" -Value "1" -ErrorAction SilentlyContinue
    }
    Write-Host "    Windows screensaver restored" -ForegroundColor Gray
} else {
    Write-Host "    Screensaver setting left unchanged. Pass -RestoreScreensaver to restore it." -ForegroundColor DarkGray
}

Write-Host "[7/7] Removing installed files..." -ForegroundColor Yellow
if ($PSCmdlet.ShouldProcess($INSTALL_DIR, "Remove installed MajestyGuard files")) {
    Remove-Item -Path $INSTALL_DIR -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "    Installed files removed" -ForegroundColor Gray

if ($PSCmdlet.ShouldProcess($PROGRAMDATA_DIR, "Remove MajestyGuard ProgramData runtime files")) {
    Remove-Item -Path $PROGRAMDATA_DIR -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "    ProgramData runtime files removed" -ForegroundColor Gray

$localData = "$env:LOCALAPPDATA\MajestyGuard"
$roamingData = "$env:APPDATA\MajestyGuard"
if ($RemoveUserData) {
    if ($PSCmdlet.ShouldProcess("$localData; $roamingData", "Remove MajestyGuard user data")) {
        Remove-Item -Path $localData -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $roamingData -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "    User data removed" -ForegroundColor Gray
} else {
    Write-Host "    Preserving user data: $localData" -ForegroundColor DarkGray
    Write-Host "    Preserving user data: $roamingData" -ForegroundColor DarkGray
    Write-Host "    Pass -RemoveUserData to remove enrollment/config data." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  MajestyGuard uninstall complete." -ForegroundColor Green
Write-Host "  Restart recommended to complete Credential Provider removal." -ForegroundColor DarkGray
Write-Host ""
