# ==============================================================================
# MajestyGuard — Developer Self-Signing & Authenticode Setup
# ==============================================================================
# Generates a trusted local Code Signing Certificate for MajestyGuard, installs
# it into the LocalMachine Root & TrustedPublisher certificate stores, and signs
# all compiled executables (.exe) and dynamic libraries (.dll).
#
# Requires: Administrator privileges
# ==============================================================================

[CmdletBinding()]
param (
    [string]$TargetDir = "$PSScriptRoot\.."
)

# ── 1. Admin Privilege Guard ──────────────────────────────────────────────────
$identity  = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script must be executed as Administrator! Please re-run in an elevated PowerShell prompt."
    exit 1
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " MajestyGuard Developer Authenticode Self-Signing & Trust Provisioning " -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

# ── 2. Certificate Generation / Resolution ────────────────────────────────────
$certSubject = "CN=MajestyGuardDev"
$certStore   = "Cert:\LocalMachine\My"

$existingCert = Get-ChildItem $certStore | Where-Object { $_.Subject -eq $certSubject } | Select-Object -First 1

if ($existingCert) {
    Write-Host "[1/3] Found existing dev signing certificate: $($existingCert.Thumbprint)" -ForegroundColor Green
    $cert = $existingCert
} else {
    Write-Host "[1/3] Generating new self-signed Code Signing Certificate ($certSubject)..." -ForegroundColor Yellow
    $cert = New-SelfSignedCertificate `
        -Subject           $certSubject `
        -CertStoreLocation $certStore `
        -Type              CodeSigningCert `
        -KeyUsage          DigitalSignature `
        -KeyAlgorithm      RSA `
        -KeyLength         2048 `
        -HashAlgorithm     SHA256 `
        -NotAfter          (Get-Date).AddYears(10)
    Write-Host "    Created certificate with Thumbprint: $($cert.Thumbprint)" -ForegroundColor Green
}

# ── 3. Install Certificate in Local Machine Trust Stores ──────────────────────
Write-Host "[2/3] Registering certificate in Trusted Root & Trusted Publisher stores..." -ForegroundColor Yellow

$storesToProvision = @("Root", "TrustedPublisher", "TrustedPeople")
foreach ($storeName in $storesToProvision) {
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($storeName, "LocalMachine")
    try {
        $store.Open("ReadWrite")
        # Check if already present
        $match = $store.Certificates | Where-Object { $_.Thumbprint -eq $cert.Thumbprint }
        if (-not $match) {
            $store.Add($cert)
            Write-Host "    Added to LocalMachine\$storeName" -ForegroundColor Green
        } else {
            Write-Host "    Already present in LocalMachine\$storeName" -ForegroundColor Gray
        }
    } finally {
        $store.Close()
    }
}

# ── 4. Sign All MajestyGuard Compiled Binaries ────────────────────────────────
Write-Host "[3/3] Locating and signing compiled binaries (.dll, .exe)..." -ForegroundColor Yellow

$targetPath = [System.IO.Path]::GetFullPath($TargetDir)
$extensions = @("*.dll", "*.exe")

$binaries = Get-ChildItem -Path $targetPath -Recurse -Include $extensions | Where-Object {
    $_.FullName -notmatch '\\\.venv\\' -and
    $_.FullName -notmatch '\\obj\\' -and
    $_.FullName -notmatch '\\node_modules\\'
}

if (-not $binaries) {
    Write-Host "    No compiled binaries found in $targetPath to sign." -ForegroundColor Yellow
    exit 0
}

$results = @()

foreach ($file in $binaries) {
    try {
        $status = Set-AuthenticodeSignature -FilePath $file.FullName -Certificate $cert -HashAlgorithm SHA256 -ErrorAction Stop
        $results += [PSCustomObject]@{
            File   = $file.Name
            Path   = $file.FullName.Replace($targetPath, "")
            Status = $status.Status
        }
    } catch {
        $results += [PSCustomObject]@{
            File   = $file.Name
            Path   = $file.FullName.Replace($targetPath, "")
            Status = "Failed: $($_.Exception.Message)"
        }
    }
}

Write-Host "`nSigning Results Summary:" -ForegroundColor Cyan
$results | Format-Table -AutoSize

Write-Host "`n[SUCCESS] MajestyGuard Developer Self-Signing Completed!" -ForegroundColor Green
Write-Host "Binaries signed with this certificate are now trusted locally by Windows Defender & SmartScreen.`n" -ForegroundColor Green
