#Requires -RunAsAdministrator
<#
.SYNOPSIS
    MajestyGuard WHCDF setup script.
    Run once as Administrator before building/installing the companion app.

.DESCRIPTION
    1. Enables Developer Mode (required for sideloading MSIX)
    2. Sets the registry key that allows secondaryAuthenticationFactor on personal machines
    3. Creates a self-signed code signing certificate for the MSIX package
    4. Prints the cert thumbprint for build configuration
    5. Does not publish MutualAuthKey by default. Use -AllowInsecureEnvKey
       only for isolated dev experiments.

    After running this script:
    a) Build MajestyGuard.Companion in Visual Studio (Release|x64)
    b) Install the MSIX: Add-AppxPackage .\MajestyGuard.Companion_1.0.0.0_x64.msix
    c) Launch the app and click "Register Device"
    d) Configure a secure daemon key handoff before enabling WHCDF IPC
#>

param(
    [switch]$AllowInsecureEnvKey
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  MajestyGuard WHCDF Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Developer Mode ─────────────────────────────────────────────────────────
Write-Host "[1/5] Enabling Developer Mode..." -ForegroundColor Yellow
$devKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock"
if (-not (Test-Path $devKey)) { New-Item -Path $devKey -Force | Out-Null }
Set-ItemProperty -Path $devKey -Name "AllowDevelopmentWithoutDevLicense" -Value 1 -Type DWord
Set-ItemProperty -Path $devKey -Name "AllowAllTrustedApps"               -Value 1 -Type DWord
Write-Host "    Developer Mode: ON" -ForegroundColor Green

# ── 2. WHCDF policy registry key ──────────────────────────────────────────────
Write-Host "[2/5] Setting WHCDF policy registry key..." -ForegroundColor Yellow
$whcdfKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WinBio\Credentials"
if (-not (Test-Path $whcdfKey)) { New-Item -Path $whcdfKey -Force | Out-Null }
Set-ItemProperty -Path $whcdfKey -Name "Domain" -Value 0 -Type DWord
# This key signals Windows to allow secondary authentication factor on non-domain machines
$saKey = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System"
if (-not (Test-Path $saKey)) { New-Item -Path $saKey -Force | Out-Null }
Set-ItemProperty -Path $saKey -Name "AllowSecondaryAuthenticationFactor" -Value 1 -Type DWord
Write-Host "    WHCDF policy: SET" -ForegroundColor Green

# ── 3. Self-signed certificate ────────────────────────────────────────────────
Write-Host "[3/5] Creating self-signed code signing certificate..." -ForegroundColor Yellow
$certSubject = "CN=MajestyGuard"
$certStore   = "Cert:\LocalMachine\My"

# Remove any existing MajestyGuard cert
Get-ChildItem $certStore | Where-Object { $_.Subject -eq $certSubject } | Remove-Item -Force

$cert = New-SelfSignedCertificate `
    -Subject          $certSubject `
    -CertStoreLocation $certStore `
    -Type              CodeSigningCert `
    -KeyUsage          DigitalSignature `
    -KeyAlgorithm      RSA `
    -KeyLength         2048 `
    -HashAlgorithm     SHA256 `
    -NotAfter          (Get-Date).AddYears(10)

# Trust it
$trustedPeopleStore = New-Object System.Security.Cryptography.X509Certificates.X509Store(
    "TrustedPeople", "LocalMachine")
$trustedPeopleStore.Open("ReadWrite")
$trustedPeopleStore.Add($cert)
$trustedPeopleStore.Close()

$rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store(
    "Root", "LocalMachine")
$rootStore.Open("ReadWrite")
$rootStore.Add($cert)
$rootStore.Close()

$thumbprint = $cert.Thumbprint
Write-Host "    Certificate thumbprint: $thumbprint" -ForegroundColor Green

# ── 4. Generate MutualAuthKey ─────────────────────────────────────────────────
Write-Host "[4/5] Generating MutualAuthKey..." -ForegroundColor Yellow
$keyBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($keyBytes)
$keyHex   = ($keyBytes | ForEach-Object { $_.ToString("x2") }) -join ""
Write-Host "    Key generated (32 bytes)" -ForegroundColor Green

# ── 5. Set machine environment variable ───────────────────────────────────────
Write-Host "[5/5] Secure MutualAuthKey handoff..." -ForegroundColor Yellow
if ($AllowInsecureEnvKey) {
    [System.Environment]::SetEnvironmentVariable(
        "MAJESTYGUARD_MUTUAL_AUTH_KEY", $keyHex,
        [System.EnvironmentVariableTarget]::Machine)
    Write-Warning "Insecure dev override enabled: MAJESTYGUARD_MUTUAL_AUTH_KEY was written at Machine scope."
    Write-Warning "Use MG_ALLOW_INSECURE_WHCDF_ENV_KEY=1 and MG_WHCDF_ALLOW_LOCAL_PIPE_CLIENTS=1 only in isolated tests."
} else {
    Write-Host "    No environment key was written." -ForegroundColor Green
    Write-Host "    WHCDF IPC remains fail-closed until a secure DPAPI/Credential Manager handoff is implemented." -ForegroundColor Gray
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Complete" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Certificate thumbprint (for csproj):" -ForegroundColor White
Write-Host "  $thumbprint" -ForegroundColor Yellow
Write-Host ""
if ($AllowInsecureEnvKey) {
    Write-Host "MutualAuthKey was generated and stored through the insecure dev override." -ForegroundColor Yellow
    Write-Host "Do not use this mode for login-screen or production unlock testing." -ForegroundColor Yellow
    Write-Host ""
}
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Open companion\MajestyGuard.Companion.csproj in Visual Studio" -ForegroundColor Gray
Write-Host "  2. Set MajestyGuardCertThumbprint=$thumbprint in project properties" -ForegroundColor Gray
Write-Host "  3. Build Release|x64" -ForegroundColor Gray
Write-Host "  4. Install: Add-AppxPackage <path-to-msix>" -ForegroundColor Gray
Write-Host "  5. Launch app, click Register Device" -ForegroundColor Gray
Write-Host "  6. Restart the Python daemon" -ForegroundColor Gray
Write-Host ""
Write-Host "NOTE: A reboot is recommended after this script." -ForegroundColor Magenta
