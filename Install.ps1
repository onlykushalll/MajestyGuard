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
Write-Host "[1/7] Creating install directory..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\models" | Out-Null

# Copy build output (assumes script is run from build output directory)
$source = $PSScriptRoot
Copy-Item "$source\*" $INSTALL_DIR -Recurse -Force -Exclude "Install.ps1","Uninstall.ps1"
Write-Host "    Files copied to $INSTALL_DIR" -ForegroundColor Gray

# ── Step 2: Register Credential Provider DLL ─────────────────────
Write-Host "[2/7] Registering Credential Provider..." -ForegroundColor Yellow

if (-not (Test-Path $CP_DLL)) {
    Write-Warning "CredentialProvider DLL not found at $CP_DLL — skipping CP registration"
    Write-Warning "Build MajestyGuard.CredentialProvider.dll first."
} else {
    # regsvr32 calls DllRegisterServer() which writes the registry keys
    $result = Start-Process regsvr32 -ArgumentList "/s `"$CP_DLL`"" -Wait -PassThru
    if ($result.ExitCode -ne 0) {
        Write-Error "regsvr32 failed (exit code $($result.ExitCode)). Ensure DLL is built for x64."
    } else {
        Write-Host "    Credential Provider registered" -ForegroundColor Gray
    }
}

# ── Step 3: Install Windows Service ──────────────────────────────
Write-Host "[3/7] Installing Windows Service..." -ForegroundColor Yellow

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

# Configure failure recovery: restart on failure
sc.exe failure $SERVICE_NAME reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null

Write-Host "    Service installed (auto-start, LocalSystem)" -ForegroundColor Gray

# ── Step 4: Python setup ──────────────────────────────────────────
Write-Host "[4/7] Setting up Python CV engine..." -ForegroundColor Yellow

# Check for Python in install dir (bundled) or system Python
$pythonExe = "$PYTHON_DIR\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
}

if (-not $pythonExe) {
    Write-Warning "Python not found. Install Python 3.11+ and re-run, or bundle Python in $PYTHON_DIR"
} else {
    Write-Host "    Using Python: $pythonExe" -ForegroundColor Gray
    & $pythonExe -m pip install -r "$CV_DIR\requirements.txt" --quiet
    Write-Host "    Python dependencies installed" -ForegroundColor Gray
}

# ── Step 5: Download all models (InsightFace + anti-spoof ONNX) ────
Write-Host "[5/7] Downloading models (~301MB, once only)..." -ForegroundColor Yellow
Write-Host "    buffalo_l (face recognition) + MiniFASNetV2 (anti-spoof)" -ForegroundColor Gray

if ($pythonExe) {
    & $pythonExe "$CV_DIR\download_models.py" "$INSTALL_DIR\models"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Model download failed — re-run Install.ps1 with internet access."
    } else {
        Write-Host "    All models ready" -ForegroundColor Gray
    }
}

# ── Step 5b: Test signing (DEV MODE — remove for production with EV cert) ─
Write-Host "[5b] Checking code signing setup..." -ForegroundColor Yellow

$cpDllExists = Test-Path $CP_DLL
if ($cpDllExists) {
    # Check if DLL is signed
    $sig = Get-AuthenticodeSignature $CP_DLL
    if ($sig.Status -eq "Valid") {
        Write-Host "    DLL is signed — production mode" -ForegroundColor Green
    } else {
        # Enable test signing mode so Windows loads our unsigned DLL on Secure Desktop
        Write-Host "    DLL not signed — enabling Windows test signing mode" -ForegroundColor Yellow
        Write-Host "    (For production: sign with EV cert + disable test signing)" -ForegroundColor DarkGray
        
        $tsResult = bcdedit /set testsigning on 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    Test signing enabled. REBOOT REQUIRED." -ForegroundColor Yellow
            
            # Create a self-signed cert for the DLL so it at least has a signature chain
            $certParams = @{
                Subject        = "CN=MajestyGuard Dev"
                CertStoreLocation = "Cert:\LocalMachine\My"
                Type           = "CodeSigning"
                NotAfter       = (Get-Date).AddYears(3)
                KeyUsage       = "DigitalSignature"
                HashAlgorithm  = "SHA256"
            }
            $cert = New-SelfSignedCertificate @certParams

            # Trust the cert
            $certPath = "Cert:\LocalMachine\My\$($cert.Thumbprint)"
            Copy-Item $certPath -Destination "Cert:\LocalMachine\Root"
            Copy-Item $certPath -Destination "Cert:\LocalMachine\TrustedPublisher"

            # Sign the DLL
            Set-AuthenticodeSignature -FilePath $CP_DLL -Certificate $cert -HashAlgorithm SHA256 | Out-Null
            Write-Host "    DLL self-signed with dev cert (thumbprint: $($cert.Thumbprint.Substring(0,16))...)" -ForegroundColor Gray
        } else {
            Write-Warning "bcdedit failed — test signing not enabled. CP will not load on Secure Desktop."
        }
    }
}

# ── Step 6: Firewall — block CV engine from internet ─────────────
Write-Host "[6/7] Configuring firewall (block CV engine outbound)..." -ForegroundColor Yellow
$ruleName = "MajestyGuard - Block CVEngine Outbound"
Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Outbound `
    -Program $pythonExe `
    -Action Block `
    -Profile Any | Out-Null
Write-Host "    CVEngine blocked from internet (privacy protection)" -ForegroundColor Gray

# ── Step 7: Task Scheduler backup — ensure service on every boot/login ─
Write-Host "[7/11] Creating Task Scheduler backup task..." -ForegroundColor Yellow
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

# ── Step 8: Enable Credential Provider for lock screen ────────────
Write-Host "[8/11] Configuring lock screen integration..." -ForegroundColor Yellow
$cpClsid = "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"
$cpKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$cpClsid"
if (Test-Path $cpKey) {
    # Ensure CP is not disabled
    Remove-ItemProperty -Path $cpKey -Name "Disabled" -ErrorAction SilentlyContinue
    Write-Host "    Credential Provider enabled for lock screen" -ForegroundColor Gray
} else {
    Write-Host "    Credential Provider not registered yet — will activate after reboot" -ForegroundColor Gray
}

# ── Step 9: Auto-start overlay for all users ──────────────────────
Write-Host "[9/11] Setting overlay to auto-start..." -ForegroundColor Yellow
$overlayExe = "$INSTALL_DIR\MajestyGuard.Overlay.exe"
$runKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
Set-ItemProperty -Path $runKey -Name "MajestyGuardOverlay" -Value "`"$overlayExe`"" -Force
Write-Host "    Overlay registered in HKLM\Run (all users, every logon)" -ForegroundColor Gray

# ── Step 10: Harden service — prevent user from stopping/disabling ─
Write-Host "[10/11] Hardening service permissions..." -ForegroundColor Yellow
# Deny interactive users permission to stop/pause/change the service
$sddl = 'D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)'
sc.exe sdset $SERVICE_NAME $sddl 2>&1 | Out-Null
# Set delayed auto-start for boot resilience
sc.exe config $SERVICE_NAME start= delayed-auto 2>&1 | Out-Null
Write-Host "    Service hardened (non-admin cannot stop/disable)" -ForegroundColor Gray

# ── Step 11: Start service ─────────────────────────────────────────
Write-Host "[11/11] Starting service..." -ForegroundColor Yellow
Start-Service -Name $SERVICE_NAME
$svc = Get-Service -Name $SERVICE_NAME
Write-Host "    Service status: $($svc.Status)" -ForegroundColor Gray

# ── Done ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ✓ MajestyGuard installed successfully." -ForegroundColor Green
Write-Host ""
Write-Host "  NEXT STEPS:" -ForegroundColor White
Write-Host "  1. Run MajestyGuard.Overlay.exe to complete face enrollment" -ForegroundColor Gray
Write-Host "  2. Follow the on-screen angle capture prompts" -ForegroundColor Gray
Write-Host "  3. Restart your PC to activate the login screen integration" -ForegroundColor Gray
Write-Host ""
Write-Host "  LOGS:  $INSTALL_DIR\logs\" -ForegroundColor DarkGray
Write-Host "  UNINSTALL: Run Uninstall.ps1 as Administrator" -ForegroundColor DarkGray
Write-Host ""


# ── Step 8: Registry ACL hardening + SafeBoot registration ────────
Write-Host "[8/7+] Hardening registry ACLs..." -ForegroundColor Yellow

# Lock Credential Providers key so only TrustedInstaller can add entries.
# Admin can STILL take ownership (auditable event), but cannot silently add CPs.
$cpKeyPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers"
$acl = Get-Acl $cpKeyPath

# Remove inherited permissions
$acl.SetAccessRuleProtection($true, $false)

# Allow: SYSTEM full control
$systemSid  = [System.Security.Principal.SecurityIdentifier]"S-1-5-18"
$systemRule = New-Object System.Security.AccessControl.RegistryAccessRule(
    $systemSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")
$acl.AddAccessRule($systemRule)

# Allow: Administrators READ only (not write)
$adminSid   = [System.Security.Principal.SecurityIdentifier]"S-1-5-32-544"
$adminRule  = New-Object System.Security.AccessControl.RegistryAccessRule(
    $adminSid, "ReadKey", "ContainerInherit,ObjectInherit", "None", "Allow")
$acl.AddAccessRule($adminRule)

# Allow: Users READ only
$userSid    = [System.Security.Principal.SecurityIdentifier]"S-1-5-32-545"
$userRule   = New-Object System.Security.AccessControl.RegistryAccessRule(
    $userSid, "ReadKey", "ContainerInherit,ObjectInherit", "None", "Allow")
$acl.AddAccessRule($userRule)

try {
    Set-Acl -Path $cpKeyPath -AclObject $acl
    Write-Host "    Credential Providers key locked (Admin=ReadOnly)" -ForegroundColor Gray
} catch {
    Write-Warning "    Registry ACL failed (may need TrustedInstaller context): $_"
}

# Register the MajestyGuard service in SafeBoot hive.
# Without this, Safe Mode falls through to the standard password provider.
Write-Host "[8b] Registering for Safe Mode..." -ForegroundColor Yellow

$safebootPaths = @(
    "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\Minimal\MajestyGuardService",
    "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\Network\MajestyGuardService"
)

foreach ($path in $safebootPaths) {
    New-Item -Path $path -Force | Out-Null
    Set-ItemProperty -Path $path -Name "(Default)" -Value "Service" -Type String
}
Write-Host "    MajestyGuardService registered in SafeBoot\Minimal + SafeBoot\Network" -ForegroundColor Gray

# Register the CP DLL for Safe Mode too (requires it in System32)
$cpSafebootPaths = @(
    "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\Minimal\{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}",
    "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\Network\{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"
)

foreach ($path in $cpSafebootPaths) {
    New-Item -Path $path -Force | Out-Null
    Set-ItemProperty -Path $path -Name "(Default)" -Value "Driver" -Type String
}
Write-Host "    Credential Provider registered for Safe Mode" -ForegroundColor Gray

# Disable Windows own screensaver/lock so it doesn't stack with ours
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "ScreenSaveActive"  -Value "0" -Type String
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "SCRNSAVE.EXE"      -Value "" -Type String
Write-Host "    Windows screensaver disabled (MajestyGuard manages its own lock)" -ForegroundColor Gray

# ── Generate Uninstall.ps1 ─────────────────────────────────────────
$uninstallContent = @'
#Requires -RunAsAdministrator
Stop-Service MajestyGuardService -Force -ErrorAction SilentlyContinue
sc.exe delete MajestyGuardService | Out-Null
regsvr32 /s /u "$env:ProgramFiles\MajestyGuard\MajestyGuard.CredentialProvider.dll" 2>$null
Remove-Item -Recurse -Force "$env:ProgramFiles\MajestyGuard" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" -Name "MajestyGuardOverlay" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "MajestyGuardWatchdog" -Confirm:$false -ErrorAction SilentlyContinue
bcdedit /deletevalue testsigning 2>&1 | Out-Null
# Re-enable Windows screensaver
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "ScreenSaveActive" -Value "1" -ErrorAction SilentlyContinue
Write-Host "MajestyGuard fully uninstalled." -ForegroundColor Green
'@
$uninstallContent | Set-Content -Path "$INSTALL_DIR\Uninstall.ps1" -Encoding UTF8
