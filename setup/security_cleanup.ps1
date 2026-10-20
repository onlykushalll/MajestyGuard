<#
.SYNOPSIS
    MajestyGuard machine-state security cleanup (remediation for already-installed machines).

.DESCRIPTION
    Standalone, idempotent admin script that purges machine-wide security state left
    behind by PREVIOUS MajestyGuard installs (the fixed installers/uninstaller no
    longer leave this state behind):

      1. All three MajestyGuard self-signed code-signing certificates, from every
         LocalMachine store any installer wrote (My, Root, TrustedPublisher,
         TrustedPeople):
             "CN=MajestyGuard Dev"     (Install.ps1 dev signing)
             "CN=MajestyGuard"         (setup/setup_whcdf.ps1 companion MSIX cert)
             "CN=MajestyGuardDev"      (setup/sign_dev_binaries.ps1 dev signing)
         Audit refs: MG-C-06, MG-C-08, MG-E-03.
      2. Machine policy keys silently enabled by setup/setup_whcdf.ps1
         (audit refs MG-E-04):
             HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock
                 AllowDevelopmentWithoutDevLicense  (Developer Mode)
                 AllowAllTrustedApps                (MSIX sideloading)
             HKLM:\SOFTWARE\Policies\Microsoft\Windows\System
                 AllowSecondaryAuthenticationFactor (WHCDF on non-domain machines)
         Only the exact values setup_whcdf.ps1 writes are removed; the shared
         Windows keys themselves are left in place.
      3. The MAJESTYGUARD_MUTUAL_AUTH_KEY Machine-scope environment variable, which
         every local process can read (audit refs MG-C-10, MG-E-08). Only its
         presence is reported - the key VALUE is never printed to console or log.
      4. Optionally (-RemovePfx) the legacy self-signing PFX whose password is
         hardcoded in archive/legacy-cleanup/.../mg_import_cert.py (audit refs
         MG-C-07, MG-E-08):
             C:\tmp\majestyguard\archive\legacy-cleanup\20260606-cleanup-184335\risky-login-system\mg_cert.pfx
             C:\tmp\mg_cert.pfx  (legacy import location referenced by mg_import_cert.py)

    Supports -WhatIf / -Confirm (ShouldProcess) for dry runs. A transcript of every
    run is appended to %LOCALAPPDATA%\MajestyGuard\cleanup.log.

.NOTES
    Run from an elevated PowerShell:
        powershell -ExecutionPolicy Bypass -File .\setup\security_cleanup.ps1 -WhatIf
        powershell -ExecutionPolicy Bypass -File .\setup\security_cleanup.ps1
        powershell -ExecutionPolicy Bypass -File .\setup\security_cleanup.ps1 -RemovePfx

    If the MajestyGuard product itself is still installed and you want it gone, run
    Uninstall.ps1 instead (it now reverts this same machine state). This script is
    the one-shot remediation for machines where MajestyGuard was already installed
    or uninstalled with the older scripts.
#>

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    # Delete the legacy mg_cert.pfx self-signing key material (opt-in).
    [switch]$RemovePfx
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator) -and -not $WhatIfPreference) {
    throw "Machine-state cleanup requires Administrator. Use -WhatIf for a no-machine-state dry run."
}

Write-Host ""
Write-Host "  MajestyGuard Security Cleanup (machine-state remediation)" -ForegroundColor Cyan
Write-Host ""

# ── Transcript logging ─────────────────────────────────────────────────────────
$logDir = Join-Path $env:LOCALAPPDATA "MajestyGuard"
$logPath = Join-Path $logDir "cleanup.log"
$transcriptStarted = $false
try {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Start-Transcript -Path $logPath -Append | Out-Null
    $transcriptStarted = $true
    Write-Host "Transcript logging to: $logPath" -ForegroundColor DarkGray
} catch {
    Write-Warning "Transcript logging unavailable ($logPath): $($_.Exception.Message)"
}

try {
    # ── 1. Dev certificates from all LocalMachine stores ───────────────────────
    Write-Host "[1/4] Removing MajestyGuard dev certificates from LocalMachine stores..." -ForegroundColor Yellow
    $devCertSubjects = @("CN=MajestyGuard Dev", "CN=MajestyGuard", "CN=MajestyGuardDev")
    $certStores = @("My", "Root", "TrustedPublisher", "TrustedPeople")
    $certsFound = 0
    $certsRemoved = 0
    foreach ($subject in $devCertSubjects) {
        foreach ($storeName in $certStores) {
            $certs = @(Get-ChildItem -Path "Cert:\LocalMachine\$storeName" -ErrorAction SilentlyContinue |
                Where-Object { $_.Subject -eq $subject })
            foreach ($c in $certs) {
                $certsFound++
                if ($PSCmdlet.ShouldProcess("Cert:\LocalMachine\$storeName\$($c.Thumbprint)", "Remove MajestyGuard dev certificate '$subject'")) {
                    Remove-Item -Path $c.PSPath -Force -ErrorAction SilentlyContinue
                    $certsRemoved++
                    Write-Host "    Removed '$subject' ($($c.Thumbprint)) from LocalMachine\$storeName" -ForegroundColor Gray
                }
            }
        }
    }
    if ($certsFound -eq 0) {
        Write-Host "    No MajestyGuard dev certificates found (already clean)" -ForegroundColor DarkGray
    } elseif ($certsRemoved -eq 0) {
        Write-Host "    Dry run: $certsFound dev certificate entries listed above were not removed (-WhatIf)" -ForegroundColor DarkGray
    }

    # ── 2. Developer Mode / sideloading / WHCDF policy keys ────────────────────
    Write-Host "[2/4] Reverting Developer Mode / sideloading / WHCDF policy keys..." -ForegroundColor Yellow
    $appModelUnlockKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock"
    foreach ($valueName in @("AllowDevelopmentWithoutDevLicense", "AllowAllTrustedApps")) {
        $existing = Get-ItemProperty -Path $appModelUnlockKey -Name $valueName -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            if ($PSCmdlet.ShouldProcess("$appModelUnlockKey :: $valueName", "Remove policy value")) {
                Remove-ItemProperty -Path $appModelUnlockKey -Name $valueName -Force -ErrorAction SilentlyContinue
                Write-Host "    Removed $valueName from $appModelUnlockKey" -ForegroundColor Gray
            }
        } else {
            Write-Host "    $valueName not set (already clean)" -ForegroundColor DarkGray
        }
    }
    $secondaryAuthKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System"
    $existing = Get-ItemProperty -Path $secondaryAuthKey -Name "AllowSecondaryAuthenticationFactor" -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if ($PSCmdlet.ShouldProcess("$secondaryAuthKey :: AllowSecondaryAuthenticationFactor", "Remove policy value")) {
            Remove-ItemProperty -Path $secondaryAuthKey -Name "AllowSecondaryAuthenticationFactor" -Force -ErrorAction SilentlyContinue
            Write-Host "    Removed AllowSecondaryAuthenticationFactor from $secondaryAuthKey" -ForegroundColor Gray
        }
    } else {
        Write-Host "    AllowSecondaryAuthenticationFactor not set (already clean)" -ForegroundColor DarkGray
    }

    # ── 3. Machine-scope mutual auth key env var ───────────────────────────────
    Write-Host "[3/4] Removing machine-scope MAJESTYGUARD_MUTUAL_AUTH_KEY..." -ForegroundColor Yellow
    $machineKey = [Environment]::GetEnvironmentVariable("MAJESTYGUARD_MUTUAL_AUTH_KEY", "Machine")
    if ($null -ne $machineKey) {
        # NOTE: the key value is deliberately never printed (MG-C-10 legacy leak).
        if ($PSCmdlet.ShouldProcess("MAJESTYGUARD_MUTUAL_AUTH_KEY (Machine scope)", "Remove environment variable")) {
            [Environment]::SetEnvironmentVariable("MAJESTYGUARD_MUTUAL_AUTH_KEY", $null, "Machine")
            Write-Host "    Machine-scope MAJESTYGUARD_MUTUAL_AUTH_KEY removed" -ForegroundColor Gray
        }
    } else {
        Write-Host "    Machine-scope MAJESTYGUARD_MUTUAL_AUTH_KEY not set (already clean)" -ForegroundColor DarkGray
    }

    # ── 4. Legacy self-signing PFX (opt-in) ────────────────────────────────────
    Write-Host "[4/4] Legacy self-signing PFX (mg_cert.pfx)..." -ForegroundColor Yellow
    $pfxPaths = @(
        "C:\tmp\majestyguard\archive\legacy-cleanup\20260606-cleanup-184335\risky-login-system\mg_cert.pfx",
        "C:\tmp\mg_cert.pfx"
    )
    if (-not $RemovePfx) {
        Write-Host "    Skipped. Pass -RemovePfx to delete the legacy code-signing PFX" -ForegroundColor DarkGray
        Write-Host "    (its password is hardcoded in the archived legacy scripts)." -ForegroundColor DarkGray
    } else {
        foreach ($pfxPath in $pfxPaths) {
            if (Test-Path -LiteralPath $pfxPath) {
                if ($PSCmdlet.ShouldProcess($pfxPath, "Delete legacy code-signing PFX (exportable private key material)")) {
                    Remove-Item -LiteralPath $pfxPath -Force -ErrorAction SilentlyContinue
                    Write-Host "    Deleted: $pfxPath" -ForegroundColor Gray
                }
            } else {
                Write-Host "    Not present: $pfxPath" -ForegroundColor DarkGray
            }
        }
    }

    Write-Host ""
    Write-Host "  MajestyGuard security cleanup complete." -ForegroundColor Green
    if ($certsRemoved -gt 0) {
        Write-Host "  Removed $certsRemoved dev certificate entry/entries; a reboot clears cached trust." -ForegroundColor DarkGray
    }
    Write-Host "  Transcript log: $logPath" -ForegroundColor DarkGray
    Write-Host "  If MajestyGuard is still installed and unwanted, also run Uninstall.ps1" -ForegroundColor DarkGray
    Write-Host "  (use -DisableTestSigning there if the installer enabled test signing)." -ForegroundColor DarkGray
    Write-Host ""
} finally {
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch { }
    }
}
