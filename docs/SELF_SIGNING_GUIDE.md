# MajestyGuard — Developer Self-Signing & Authenticode Guide

This guide explains how to provision a local trusted Code Signing Certificate and sign all MajestyGuard binaries (`.dll` and `.exe`) to prevent **Windows Defender**, **SmartScreen**, and **LSASS (LogonUI)** from blocking unsigned binaries.

---

## 1. Why Self-Signing is Required

Windows OS enforces strict security checks on system-level components:
1. **LogonUI / Credential Provider (`MajestyGuard.CredentialProvider.dll`)**: Windows LogonUI loads 3rd-party credential providers into sensitive security contexts. Unsigned DLLs are blocked by Windows Code Integrity / SmartScreen unless signed by a trusted certificate.
2. **Background Service (`MajestyGuard.Service.exe`)**: Windows Defender SmartScreen displays warnings or quarantines new executable files that lack an Authenticode digital signature.
3. **Win32 Input Hooks (`WH_KEYBOARD_LL` / `WH_MOUSE_LL`)**: User Interface Privilege Isolation (UIPI) trusts signed elevated processes to handle system-wide hooks.

---

## 2. Automated Self-Signing (Recommended)

Run the automated provisioning script from an **Elevated PowerShell Prompt (Run as Administrator)**:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup\sign_dev_binaries.ps1
```

### What this script does automatically:
1. Generates an RSA-2048 SHA256 Code Signing Certificate (`CN=MajestyGuardDev`).
2. Installs the certificate into your local machine's:
   - `Cert:\LocalMachine\My` (Personal)
   - `Cert:\LocalMachine\Root` (Trusted Root Certification Authorities)
   - `Cert:\LocalMachine\TrustedPublisher` (Trusted Publishers)
3. Recursively locates all `.dll` and `.exe` binaries in the project and signs them with `Set-AuthenticodeSignature`.

---

## 3. Manual Step-by-Step Self-Signing (Alternative)

If you prefer to perform each step manually:

### Step 1: Create a Code Signing Certificate
Open PowerShell as Administrator:
```powershell
$cert = New-SelfSignedCertificate `
    -Subject "CN=MajestyGuardDev" `
    -CertStoreLocation "Cert:\LocalMachine\My" `
    -Type CodeSigningCert `
    -KeyUsage DigitalSignature `
    -KeyAlgorithm RSA `
    -KeyLength 2048 `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddYears(10)
```

### Step 2: Trust the Certificate
Add the certificate to the Trusted Root & Trusted Publisher stores so Windows considers it genuine:
```powershell
$rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "LocalMachine")
$rootStore.Open("ReadWrite")
$rootStore.Add($cert)
$rootStore.Close()

$pubStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPublisher", "LocalMachine")
$pubStore.Open("ReadWrite")
$pubStore.Add($cert)
$pubStore.Close()
```

### Step 3: Sign a Binary
Sign any binary (`MajestyGuard.CredentialProvider.dll`, `MajestyGuard.Service.exe`, etc.):
```powershell
Set-AuthenticodeSignature -FilePath "C:\tmp\MajestyGuard\src\MajestyGuard.CredentialProvider\x64\Release\MajestyGuardCredentialProvider.dll" -Certificate $cert -HashAlgorithm SHA256
```

### Step 4: Verify Signature
Check that Windows verifies the binary as valid and trusted:
```powershell
Get-AuthenticodeSignature "C:\tmp\MajestyGuard\src\MajestyGuard.CredentialProvider\x64\Release\MajestyGuardCredentialProvider.dll"
```
*Expected Status:* `Valid`.

---

## 4. Windows Defender & Exclusion Setup

If you are developing locally without signing, you can temporarily add a Windows Defender Exclusion for the project path:

```powershell
Add-MpPreference -ExclusionPath "C:\tmp\MajestyGuard"
```

To remove the exclusion later:
```powershell
Remove-MpPreference -ExclusionPath "C:\tmp\MajestyGuard"
```

---

## 5. Production Code Signing (Commercial Deployment)

For public distribution outside local development, replace `CN=MajestyGuardDev` with an **EV (Extended Validation) Code Signing Certificate** from an accredited Certificate Authority (e.g. DigiCert, Sectigo).
