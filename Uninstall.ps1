# MajestyGuard/Uninstall.ps1
# Run as Administrator. Fully removes MajestyGuard.

#Requires -RunAsAdministrator
Set-StrictMode -Version Latest
$ErrorActionPreference = "SilentlyContinue"

$INSTALL_DIR  = "$env:ProgramFiles\MajestyGuard"
$SERVICE_NAME = "MajestyGuardService"
$CP_DLL       = "$INSTALL_DIR\MajestyGuard.CredentialProvider.dll"
$CP_CLSID     = "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"

Write-Host ""
Write-Host "  MajestyGuard Uninstaller" -ForegroundColor Red
Write-Host ""

# 1. Stop and delete service
Write-Host "[1/7] Stopping service..." -ForegroundColor Yellow
Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
sc.exe delete $SERVICE_NAME 2>&1 | Out-Null
Write-Host "    Service removed" -ForegroundColor Gray

# 2. Unregister Credential Provider DLL
Write-Host "[2/7] Unregistering Credential Provider..." -ForegroundColor Yellow
if (Test-Path $CP_DLL) {
    Start-Process regsvr32 -ArgumentList "/s /u `"$CP_DLL`"" -Wait
}
Remove-Item -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$CP_CLSID" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path "HKLM:\SOFTWARE\Classes\CLSID\$CP_CLSID" -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "    Credential Provider unregistered" -ForegroundColor Gray

# 3. Remove overlay auto-start
Write-Host "[3/7] Removing auto-start entries..." -ForegroundColor Yellow
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" -Name "MajestyGuardOverlay" -ErrorAction SilentlyContinue
Write-Host "    Auto-start removed" -ForegroundColor Gray

# 4. Remove Task Scheduler task
Write-Host "[4/7] Removing scheduled tasks..." -ForegroundColor Yellow
Unregister-ScheduledTask -TaskName "MajestyGuard_ServiceGuard" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "    Scheduled tasks removed" -ForegroundColor Gray

# 5. Remove firewall rules
Write-Host "[5/7] Removing firewall rules..." -ForegroundColor Yellow
Remove-NetFirewallRule -DisplayName "MajestyGuard - Block CVEngine Outbound" -ErrorAction SilentlyContinue
Write-Host "    Firewall rules removed" -ForegroundColor Gray

# 6. Revert hosts file (in case SocialLock was active)
Write-Host "[6/7] Reverting hosts file..." -ForegroundColor Yellow
$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
if (Test-Path $hostsPath) {
    $lines = Get-Content $hostsPath | Where-Object { $_ -notmatch "# MajestyGuard SocialLock" }
    $lines | Set-Content $hostsPath
    ipconfig /flushdns 2>&1 | Out-Null
}
Write-Host "    Hosts file clean" -ForegroundColor Gray

# 7. Delete install directory and user data
Write-Host "[7/7] Removing files..." -ForegroundColor Yellow
Remove-Item -Path $INSTALL_DIR -Recurse -Force -ErrorAction SilentlyContinue
$appData = "$env:LOCALAPPDATA\MajestyGuard"
Remove-Item -Path $appData -Recurse -Force -ErrorAction SilentlyContinue
$roaming = "$env:APPDATA\MajestyGuard"
Remove-Item -Path $roaming -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "    Files removed" -ForegroundColor Gray

Write-Host ""
Write-Host "  MajestyGuard fully uninstalled." -ForegroundColor Green
Write-Host "  Restart recommended to complete Credential Provider removal." -ForegroundColor DarkGray
Write-Host ""
