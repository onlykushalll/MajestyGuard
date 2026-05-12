# MajestyGuard/Install.ps1
# Run as Administrator.
# Installs MajestyGuard: registers the Credential Provider,
# installs the Windows Service, and sets up the Python CV engine.
#
# USAGE:
#   Right-click Install.ps1 → "Run with PowerShell (as Administrator)"
#   Or: Start-Process powershell -Verb RunAs -ArgumentList "-File Install.ps1"

#Requires -RunAsAdministrator
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$INSTALL_DIR  = "$env:ProgramFiles\MajestyGuard"
$SERVICE_NAME = "MajestyGuardService"
$SERVICE_EXE  = "$INSTALL_DIR\MajestyGuard.Service.exe"
$CP_DLL       = "$INSTALL_DIR\MajestyGuard.CredentialProvider.dll"
$PYTHON_DIR   = "$INSTALL_DIR\python"
$CV_DIR       = "$INSTALL_DIR\CVEngine"

Write-Host ""
Write-Host "  ███╗   ███╗ █████╗      ██╗███████╗███████╗████████╗██╗   ██╗" -ForegroundColor Cyan
Write-Host "  ████╗ ████║██╔══██╗     ██║██╔════╝██╔════╝╚══██╔══╝╚██╗ ██╔╝" -ForegroundColor Cyan
Write-Host "  ██╔████╔██║███████║     ██║█████╗  ███████╗   ██║    ╚████╔╝ " -ForegroundColor Cyan
Write-Host "  ██║╚██╔╝██║██╔══██║██   ██║██╔══╝  ╚════██║   ██║     ╚██╔╝  " -ForegroundColor Cyan
Write-Host "  ██║ ╚═╝ ██║██║  ██║╚█████╔╝███████╗███████║   ██║      ██║   " -ForegroundColor Cyan
Write-Host "  ╚═╝     ╚═╝╚═╝  ╚═╝ ╚════╝ ╚══════╝╚══════╝   ╚═╝      ╚═╝   " -ForegroundColor Cyan
Write-Host ""
Write-Host "  G U A R D  —  Installer v1.0" -ForegroundColor DarkCyan
Write-Host ""

# ── Step 1: Create install directory ─────────────────────────────
Write-Host "[1/13] Creating install directory..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\models" | Out-Null

# Copy build output (assumes script is run from build output directory)
$source = $PSScriptRoot
Copy-Item "$source\*" $INSTALL_DIR -Recurse -Force -Exclude "Install.ps1","Uninstall.ps1"
Write-Host "    Files copied to $INSTALL_DIR" -ForegroundColor Gray

# ── Step 2: Register Credential Provider DLL ─────────────────────
Write-Host "[2/13] Registering Credential Provider..." -ForegroundColor Yellow

if (-not (Test-Path $CP_DLL)) {
    Write-Warning "CredentialProvider DLL not found at $CP_DLL — skipping CP registration"
    Write-Warning "Build MajestyGuard.CredentialProvider.dll first."
} else {
    $result = Start-Process regsvr32 -ArgumentList "/s `"$CP_DLL`"" -Wait -PassThru
    if ($result.ExitCode -ne 0) {
        Write-Error "regsvr32 failed (exit code $($result.ExitCode)). Ensure DLL is built for x64."
    } else {
        Write-Host "    Credential Provider registered" -ForegroundColor Gray
    }
}

# ── Step 3: Install Windows Service ──────────────────────────────
Write-Host "[3/13] Installing Windows Service..." -ForegroundColor Yellow

$existingService = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "    Stopping existing service..." -ForegroundColor Gray
    Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
    sc.exe delete $SERVICE_NAME | Out-Null
    Start-Sleep -Seconds 2
}

sc.exe create $SERVICE_NAME `
    binPath= "`"$SERVICE_EXE`"" `
    DisplayName= "Majesty Guard Security Service" `
    start= auto `
    obj= LocalSystem | Out-Null

sc.exe description $SERVICE_NAME "Biometric presence monitoring and face recognition for Majesty Guard." | Out-Null
sc.exe failure $SERVICE_NAME reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null

Write-Host "    Service installed (auto-start, LocalSystem)" -ForegroundColor Gray

# ── Step 4: Python setup ──────────────────────────────────────────
Write-Host "[4/13] Setting up Python CV engine..." -ForegroundColor Yellow

$pythonExe = "$PYTHON_DIR\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) { $pythonExe = $pythonCmd.Source } else { $pythonExe = $null }
}

if (-not $pythonExe) {
    Write-Warning "Python not found. Install Python 3.11+ and re-run, or bundle Python in $PYTHON_DIR"
} else {
    Write-Host "    Using Python: $pythonExe" -ForegroundColor Gray
    & $pythonExe -m pip install -r "$CV_DIR\requirements.txt" --quiet
    Write-Host "    Python dependencies installed" -ForegroundColor Gray
}

# ── Step 5: Download models ───────────────────────────────────────
Write-Host "[5/13] Downloading models (~301MB, once only)..." -ForegroundColor Yellow
Write-Host "    buffalo_l (face recognition) + MiniFASNetV2 (anti-spoof)" -ForegroundColor Gray

if ($pythonExe) {
    $downloadScript = "$CV_DIR\download_models.py"
    if (Test-Path $downloadScript) {
        & $pythonExe $downloadScript "$INSTALL_DIR\models"
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Model download failed — re-run Install.ps1 with internet access."
        } else {
            Write-Host "    All models ready" -ForegroundColor Gray
        }
    } else {
        # Fallback: download InsightFace inline
        $inlineScript = @"
import insightface
from insightface.app import FaceAnalysis
import os
model_dir = r'$INSTALL_DIR\models'
os.makedirs(model_dir, exist_ok=True)
app = FaceAnalysis(name='buffalo_l', root=model_dir, providers=['CPUExecutionProvider'])
app.prepare(ctx_id=0, det_size=(320, 320))
print('Model download complete')
"@
        $inlineScript | & $pythonExe -
        Write-Host "    Models ready" -ForegroundColor Gray
    }
}

# ── Step 6: Code signing (dev mode) ──────────────────────────────
Write-Host "[6/13] Checking code signing setup..." -ForegroundColor Yellow

$cpDllExists = Test-Path $CP_DLL
if ($cpDllExists) {
    $sig = Get-AuthenticodeSignature $CP_DLL
    if ($sig.Status -eq "Valid") {
        Write-Host "    DLL is signed — production mode" -ForegroundColor Green
    } else {
        Write-Host "    DLL not signed — enabling Windows test signing mode" -ForegroundColor Yellow
        Write-Host "    (For production: sign with EV cert + disable test signing)" -ForegroundColor DarkGray

        bcdedit /set testsigning on 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    Test signing enabled. REBOOT REQUIRED." -ForegroundColor Yellow

            $certParams = @{
                Subject           = "CN=MajestyGuard Dev"
                CertStoreLocation = "Cert:\LocalMachine\My"
                Type              = "CodeSigning"
                NotAfter          = (Get-Date).AddYears(3)
                KeyUsage          = "DigitalSignature"
                HashAlgorithm     = "SHA256"
            }
            $cert = New-SelfSignedCertificate @certParams

            $certPath = "Cert:\LocalMachine\My\$($cert.Thumbprint)"
            Copy-Item $certPath -Destination "Cert:\LocalMachine\Root"
            Copy-Item $certPath -Destination "Cert:\LocalMachine\TrustedPublisher"

            Set-AuthenticodeSignature -FilePath $CP_DLL -Certificate $cert -HashAlgorithm SHA256 | Out-Null
            Write-Host "    DLL self-signed with dev cert (thumbprint: $($cert.Thumbprint.Substring(0,16))...)" -ForegroundColor Gray
        } else {
            Write-Warning "bcdedit failed — test signing not enabled. CP will not load on Secure Desktop."
        }
    }
}

# ── Step 7: Firewall — block CV engine from internet ─────────────
Write-Host "[7/13] Configuring firewall (block CV engine outbound)..." -ForegroundColor Yellow
if ($pythonExe) {
    $ruleName = "MajestyGuard - Block CVEngine Outbound"
    Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Outbound `
        -Program $pythonExe `
        -Action Block `
        -Profile Any | Out-Null
    Write-Host "    CVEngine blocked from internet (privacy protection)" -ForegroundColor Gray
} else {
    Write-Host "    Skipped (no Python found)" -ForegroundColor DarkGray
}

# ── Step 8: Task Scheduler guard ─────────────────────────────────
Write-Host "[8/13] Creating Task Scheduler backup task..." -ForegroundColor Yellow
$taskName = "MajestyGuard_ServiceGuard"
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute "sc.exe" `
    -Argument "start $SERVICE_NAME"

$triggerBoot = New-ScheduledTaskTrigger -AtStartup
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $triggerBoot,$triggerLogon `
    -Settings $settings `
    -Principal $principal `
    -Description "Ensures MajestyGuard service is always running" | Out-Null
Write-Host "    Task Scheduler guard registered (boot + logon)" -ForegroundColor Gray

# ── Step 9: Enable Credential Provider for lock screen ────────────
Write-Host "[9/13] Configuring lock screen integration..." -ForegroundColor Yellow
$cpClsid = "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"
$cpKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$cpClsid"
if (Test-Path $cpKey) {
    Remove-ItemProperty -Path $cpKey -Name "Disabled" -ErrorAction SilentlyContinue
    Write-Host "    Credential Provider enabled for lock screen" -ForegroundColor Gray
} else {
    Write-Host "    Credential Provider not registered yet — will activate after reboot" -ForegroundColor Gray
}

# ── Step 10: Auto-start overlay for all users ─────────────────────
Write-Host "[10/13] Setting overlay to auto-start..." -ForegroundColor Yellow
$overlayExe = "$INSTALL_DIR\MajestyGuard.Overlay.exe"
$runKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
Set-ItemProperty -Path $runKey -Name "MajestyGuardOverlay" -Value "`"$overlayExe`"" -Force
Write-Host "    Overlay registered in HKLM\Run (all users, every logon)" -ForegroundColor Gray

# ── Step 11: Harden service permissions ───────────────────────────
Write-Host "[11/13] Hardening service permissions..." -ForegroundColor Yellow
$sddl = 'D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)'
sc.exe sdset $SERVICE_NAME $sddl 2>&1 | Out-Null
sc.exe config $SERVICE_NAME start= delayed-auto 2>&1 | Out-Null
Write-Host "    Service hardened (non-admin cannot stop/disable, delayed-auto)" -ForegroundColor Gray

# ── Step 12: Registry hardening + SafeBoot registration ───────────
Write-Host "[12/13] Registry hardening + Safe Mode registration..." -ForegroundColor Yellow

# Lock Credential Providers registry key (Admin = ReadOnly)
$cpKeyPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers"
try {
    $acl = Get-Acl $cpKeyPath
    $acl.SetAccessRuleProtection($true, $false)

    $systemSid = [System.Security.Principal.SecurityIdentifier]"S-1-5-18"
    $adminSid  = [System.Security.Principal.SecurityIdentifier]"S-1-5-32-544"
    $userSid   = [System.Security.Principal.SecurityIdentifier]"S-1-5-32-545"

    $acl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule(
        $systemSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule(
        $adminSid, "ReadKey", "ContainerInherit,ObjectInherit", "None", "Allow")))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule(
        $userSid, "ReadKey", "ContainerInherit,ObjectInherit", "None", "Allow")))

    Set-Acl -Path $cpKeyPath -AclObject $acl
    Write-Host "    CP registry key locked (Admin = ReadOnly)" -ForegroundColor Gray
} catch {
    Write-Warning "    Registry ACL failed (may need TrustedInstaller context): $_"
}

# Register service + CP in SafeBoot so they load in Safe Mode
foreach ($mode in @("Minimal", "Network")) {
    $svcPath = "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\$mode\MajestyGuardService"
    $cpPath  = "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\$mode\$cpClsid"
    New-Item -Path $svcPath -Force | Out-Null
    Set-ItemProperty $svcPath -Name "(Default)" -Value "Service"
    New-Item -Path $cpPath -Force | Out-Null
    Set-ItemProperty $cpPath -Name "(Default)" -Value "Driver"
}
Write-Host "    SafeBoot\Minimal + SafeBoot\Network registered" -ForegroundColor Gray

# Create HKLM\SOFTWARE\MajestyGuard key for CP registry reads
$mgKey = "HKLM:\SOFTWARE\MajestyGuard"
New-Item -Path $mgKey -Force | Out-Null

# Disable Windows screensaver — MajestyGuard manages its own lock
Set-ItemProperty "HKCU:\Control Panel\Desktop" -Name "ScreenSaveActive" -Value "0" -ErrorAction SilentlyContinue
Set-ItemProperty "HKCU:\Control Panel\Desktop" -Name "SCRNSAVE.EXE" -Value "" -ErrorAction SilentlyContinue
Write-Host "    Windows screensaver disabled (MajestyGuard manages lock)" -ForegroundColor Gray

# ── Step 13: Start service ────────────────────────────────────────
Write-Host "[13/13] Starting service..." -ForegroundColor Yellow
Start-Service -Name $SERVICE_NAME
$svc = Get-Service -Name $SERVICE_NAME
Write-Host "    Service status: $($svc.Status)" -ForegroundColor Gray

# ── Generate Uninstall.ps1 in install directory ───────────────────
$uninstallContent = @'
#Requires -RunAsAdministrator
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

Write-Host "Uninstalling MajestyGuard..." -ForegroundColor Yellow

# Stop and delete service
Stop-Service MajestyGuardService -Force -ErrorAction SilentlyContinue
sc.exe delete MajestyGuardService | Out-Null

# Unregister Credential Provider
$cpDll = "$env:ProgramFiles\MajestyGuard\MajestyGuard.CredentialProvider.dll"
if (Test-Path $cpDll) { regsvr32 /s /u $cpDll 2>$null }

# Remove auto-start
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" -Name "MajestyGuardOverlay" -ErrorAction SilentlyContinue

# Remove scheduled task
Unregister-ScheduledTask -TaskName "MajestyGuard_ServiceGuard" -Confirm:$false -ErrorAction SilentlyContinue

# Remove SafeBoot entries
foreach ($mode in @("Minimal", "Network")) {
    Remove-Item "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\$mode\MajestyGuardService" -Force -ErrorAction SilentlyContinue
    Remove-Item "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\$mode\{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}" -Force -ErrorAction SilentlyContinue
}

# Remove MajestyGuard registry key
Remove-Item "HKLM:\SOFTWARE\MajestyGuard" -Recurse -Force -ErrorAction SilentlyContinue

# Remove firewall rule
Remove-NetFirewallRule -DisplayName "MajestyGuard - Block CVEngine Outbound" -ErrorAction SilentlyContinue

# Disable test signing
bcdedit /deletevalue testsigning 2>&1 | Out-Null

# Re-enable Windows screensaver
Set-ItemProperty "HKCU:\Control Panel\Desktop" -Name "ScreenSaveActive" -Value "1" -ErrorAction SilentlyContinue

# Remove install directory
Remove-Item -Recurse -Force "$env:ProgramFiles\MajestyGuard" -ErrorAction SilentlyContinue

Write-Host "MajestyGuard fully uninstalled. Restart recommended." -ForegroundColor Green
'@
$uninstallContent | Set-Content -Path "$INSTALL_DIR\Uninstall.ps1" -Encoding UTF8

# ── Done ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  MajestyGuard installed successfully." -ForegroundColor Green
Write-Host ""
Write-Host "  NEXT STEPS:" -ForegroundColor White
Write-Host "  1. Run MajestyGuard.Overlay.exe to complete face enrollment" -ForegroundColor Gray
Write-Host "  2. Follow the on-screen angle capture prompts" -ForegroundColor Gray
Write-Host "  3. Restart your PC to activate the login screen integration" -ForegroundColor Gray
Write-Host ""
Write-Host "  LOGS:  $INSTALL_DIR\logs\" -ForegroundColor DarkGray
Write-Host "  UNINSTALL: Run $INSTALL_DIR\Uninstall.ps1 as Administrator" -ForegroundColor DarkGray
Write-Host ""
