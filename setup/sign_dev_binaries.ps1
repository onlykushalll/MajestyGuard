# ==============================================================================
# MajestyGuard — Developer Self-Signing & Authenticode Setup
# ==============================================================================
# Generates a trusted local Code Signing Certificate for MajestyGuard, installs
# it into the LocalMachine TrustedPublisher certificate store ONLY, and signs
# all compiled executables (.exe) and dynamic libraries (.dll).
#
# SECURITY (MG-C-06/MG-E-03): the certificate is deliberately NOT provisioned
# into LocalMachine\Root or TrustedPeople. Root is the machine-wide trust
# anchor store: a self-signed CodeSigning cert there makes every binary signed
# with its private key chain-trusted for ALL local validation, which turns a
# dev key into a machine-wide trust-widening primitive. TrustedPublisher is
# the correct (and sufficient) store for dev Authenticode publisher trust.
#
# Requires: Administrator privileges
#
# WHAT THIS DOES NOT DO — READ BEFORE RELYING ON THIS FOR THE CREDENTIAL
# PROVIDER / SAC-BLOCKING PROBLEM:
# This does NOT bypass Smart App Control (SAC). Verified directly against
# Microsoft's own documentation: SAC only trusts a binary if either (a) its
# cloud reputation system already recognizes it (never happens for a
# personal, low-volume app), or (b) it's signed with a certificate from a
# CA that participates in the Microsoft Trusted Root Program -- a
# centrally-curated list Microsoft maintains. Installing a self-signed
# cert into this machine's own LocalMachine\Root store (which this script
# no longer does -- TrustedPublisher only, see the MG-C-06 hardening above)
# would not add it to that program; these are different things
# despite the similar name. Microsoft's own FAQ states there is currently
# no supported way to bypass SAC for an individual app short of a real
# Trusted-Root-Program-chained signature (see docs/signing -- SignPath.io
# Foundation's free OSS tier is the identified path for that) or disabling
# SAC entirely, which is explicitly off the table on the main dev machine.
# What THIS script legitimately does: reduces generic "unknown publisher"
# SmartScreen friction and produces a properly Authenticode-signed binary
# for local testing -- useful on its own, just not a fix for the actual
# SAC block that's been this project's real obstacle.
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
Write-Host "[2/3] Registering certificate in the Trusted Publisher store..." -ForegroundColor Yellow

# SECURITY (MG-C-06/MG-E-03): provision TrustedPublisher ONLY. A self-signed
# code-signing certificate must never be placed in LocalMachine\Root (that
# makes everything signed with its key machine-wide chain-trusted), and
# TrustedPeople is not needed for Authenticode publisher trust.
$storesToProvision = @("TrustedPublisher")
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

$binaries = Get-ChildItem -Path $targetPath -Recurse -Include $extensions -ErrorAction SilentlyContinue | Where-Object {

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
Write-Host "Binaries are now properly Authenticode-signed with the MajestyGuard" -ForegroundColor Green
Write-Host "dev certificate, trusted for publisher identity via LocalMachine" -ForegroundColor Green
Write-Host "TrustedPublisher. Signature status may report 'NotTrusted' because the" -ForegroundColor Yellow
Write-Host "self-signed dev cert is intentionally NOT a machine Root trust anchor" -ForegroundColor Yellow
Write-Host "(MG-C-06 hardening); that is expected and not a signing failure." -ForegroundColor Yellow
Write-Host "This does NOT unblock Smart App Control -- SAC requires a certificate" -ForegroundColor Yellow
Write-Host "from Microsoft's Trusted Root Program specifically, which a locally" -ForegroundColor Yellow
Write-Host "self-signed cert does not chain to. See the header comment above." -ForegroundColor Yellow
