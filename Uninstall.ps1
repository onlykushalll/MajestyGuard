# MajestyGuard/Uninstall.ps1
# Run as Administrator for machine-level uninstall. Use -WhatIf for dry run.

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [switch]$DisableTestSigning,
    [switch]$RestoreScreensaver,
    [switch]$RemoveUserData
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator) -and -not $WhatIfPreference) {
    throw "Machine-level uninstall requires Administrator. Use -WhatIf for a no-machine-state dry run."
}

$INSTALL_DIR  = "$env:ProgramFiles\MajestyGuard"
if (Test-Path "HKLM:\SOFTWARE\MajestyGuard") {
    try {
        $regKey = Get-Item -Path "HKLM:\SOFTWARE\MajestyGuard" -ErrorAction SilentlyContinue
        if ($regKey -and $regKey.GetValue("InstallPath")) {
            $INSTALL_DIR = $regKey.GetValue("InstallPath")
        }
    } catch {}
}
$programDataBase = if ($env:ProgramData) { $env:ProgramData } else { "C:\ProgramData" }
$PROGRAMDATA_DIR = Join-Path $programDataBase "MajestyGuard"
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

Write-Host "Removing development code signing certificates..." -ForegroundColor Yellow
# MG-E-03: three MajestyGuard self-signed code-signing subjects exist, one per
# installer: Install.ps1 ("CN=MajestyGuard Dev"), setup/sign_dev_binaries.ps1
# ("CN=MajestyGuardDev") and setup/setup_whcdf.ps1 ("CN=MajestyGuard"). Remove
# ALL THREE subjects from every LocalMachine store any installer wrote
# (My, Root, TrustedPublisher, TrustedPeople) so no MajestyGuard trust anchor
# outlives the product. Idempotent: absent subjects/stores are skipped.
if ($PSCmdlet.ShouldProcess("MajestyGuard dev certificates", "Remove self-signed code signing certificates (CN=MajestyGuard Dev, CN=MajestyGuard, CN=MajestyGuardDev) from My, Root, TrustedPublisher, and TrustedPeople stores")) {
    foreach ($subject in @("CN=MajestyGuard Dev", "CN=MajestyGuard", "CN=MajestyGuardDev")) {
        foreach ($storeName in @("My", "Root", "TrustedPublisher", "TrustedPeople")) {
            $certs = Get-ChildItem -Path "Cert:\LocalMachine\$storeName" -ErrorAction SilentlyContinue | Where-Object { $_.Subject -eq $subject }
            foreach ($c in $certs) {
                Remove-Item -Path $c.PSPath -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
Write-Host "    Development certificates removed" -ForegroundColor Gray

Write-Host "Reverting WHCDF / Developer Mode machine policy keys..." -ForegroundColor Yellow
# MG-E-04: setup/setup_whcdf.ps1 enables Developer Mode
# (AllowDevelopmentWithoutDevLicense), MSIX sideloading (AllowAllTrustedApps)
# and AllowSecondaryAuthenticationFactor. These exact machine-wide policy
# values are reverted here (values only; the shared Windows keys themselves
# are left in place) so uninstalling does not leave the OS weakened.
if ($PSCmdlet.ShouldProcess("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock", "Remove AllowDevelopmentWithoutDevLicense and AllowAllTrustedApps values")) {
    $appModelUnlockKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock"
    Remove-ItemProperty -Path $appModelUnlockKey -Name "AllowDevelopmentWithoutDevLicense" -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $appModelUnlockKey -Name "AllowAllTrustedApps" -ErrorAction SilentlyContinue
}
if ($PSCmdlet.ShouldProcess("HKLM:\SOFTWARE\Policies\Microsoft\Windows\System", "Remove AllowSecondaryAuthenticationFactor value")) {
    Remove-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System" -Name "AllowSecondaryAuthenticationFactor" -ErrorAction SilentlyContinue
}
Write-Host "    WHCDF / Developer Mode policy keys reverted" -ForegroundColor Gray

Write-Host "Removing machine-scope mutual auth key..." -ForegroundColor Yellow
# MG-C-10/MG-E-08: setup/setup_whcdf.ps1 (-AllowInsecureEnvKey) and the legacy
# archive/legacy-cleanup/.../mg_genkey.ps1 write MAJESTYGUARD_MUTUAL_AUTH_KEY at
# Machine scope, where every local process can read it. Clear it on uninstall.
if ($PSCmdlet.ShouldProcess("MAJESTYGUARD_MUTUAL_AUTH_KEY", "Remove Machine-scope environment variable")) {
    [Environment]::SetEnvironmentVariable('MAJESTYGUARD_MUTUAL_AUTH_KEY', $null, 'Machine')
}
Write-Host "    Machine-scope mutual auth key removed" -ForegroundColor Gray

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

if ($PSCmdlet.ShouldProcess("HKLM:\SOFTWARE\MajestyGuard", "Remove MajestyGuard registry configuration")) {
    Remove-Item -Path "HKLM:\SOFTWARE\MajestyGuard" -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "  MajestyGuard uninstall complete." -ForegroundColor Green
Write-Host "  Restart recommended to complete Credential Provider removal." -ForegroundColor DarkGray
Write-Host ""
