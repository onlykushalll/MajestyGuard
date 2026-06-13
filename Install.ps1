# MajestyGuard/Install.ps1
# Run as Administrator for machine-level install.
# Safe, staged installer for local development.

param(
    [switch]$AcknowledgeLoginRisk,
    [switch]$CopyOnly,
    [string]$InstallDir,
    [switch]$EnableCredentialProvider,
    [switch]$InstallService,
    [switch]$StartServiceAfterInstall,
    [switch]$UseDotnetServiceHost,
    [switch]$EnableDevSigning,
    [switch]$EnableTestSigning,
    [switch]$AutoStartOverlay,
    [switch]$EnableRegistryHardening,
    [switch]$EnableSafeBoot,
    [switch]$DisableServiceOverlayLaunch,
    [switch]$SkipPythonSetup,
    [switch]$SkipModelDownload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ScExe {
    param(
        [Parameter(Mandatory=$true)]
        [string[]]$Arguments
    )

    $output = & sc.exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $joined = $Arguments -join " "
        throw "sc.exe $joined failed with exit code $LASTEXITCODE. Output: $($output -join [Environment]::NewLine)"
    }
    return $output
}

function Wait-ServiceDeleted {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Name
    )

    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-Service -Name $Name -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Timed out waiting for Windows service '$Name' to be deleted."
}

function Test-CvPython311 {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }

    try {
        $version = & $Path --version 2>&1
        return ($LASTEXITCODE -eq 0 -and ([string]$version).Trim().StartsWith("Python 3.11"))
    } catch {
        return $false
    }
}

function Resolve-CvPythonPath {
    $knownGoodVenv = "C:\tmp\MajestyGuard\src\MajestyGuard.CVEngine\.venv\Scripts\python.exe"

    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $candidates = @(
        $knownGoodVenv,
        (Join-Path $repoRoot "src\MajestyGuard.CVEngine\.venv\Scripts\python.exe"),
        (Join-Path $PROGRAMDATA_CV_DIR ".venv\Scripts\python.exe"),
        (Join-Path $INSTALL_DIR "CVEngine\.venv\Scripts\python.exe")
    )

    foreach ($candidate in ($candidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)) {
        if (Test-CvPython311 -Path $candidate) {
            return [string]$candidate
        }
    }

    throw "No Python 3.11 CV venv found. Expected: $knownGoodVenv"
}

function Copy-DirectoryContentsFiltered {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Destination,
        [string[]]$ExcludeNames = @(),
        [string[]]$ExcludeFiles = @()
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Source directory not found: $Source"
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    $sourceRoot = (Resolve-Path -LiteralPath $Source).ProviderPath
    foreach ($item in (Get-ChildItem -LiteralPath $sourceRoot -Force -Recurse)) {
        $relativePath = $item.FullName.Substring($sourceRoot.Length).TrimStart('\')
        $segments = $relativePath -split '\\'
        $skip = $false

        foreach ($segment in $segments) {
            if ($ExcludeNames -contains $segment) {
                $skip = $true
                break
            }
        }

        if ($skip) {
            continue
        }

        if (-not $item.PSIsContainer) {
            foreach ($pattern in $ExcludeFiles) {
                if ($item.Name -like $pattern) {
                    $skip = $true
                    break
                }
            }
        }

        if ($skip) {
            continue
        }

        $target = Join-Path $Destination $relativePath
        if ($item.PSIsContainer) {
            New-Item -ItemType Directory -Force -Path $target | Out-Null
        } else {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $item.FullName -Destination $target -Force
        }
    }
}

function Resolve-CvEngineSource {
    $stagedSource = Join-Path $PSScriptRoot "CVEngine"
    if (Test-Path -LiteralPath $stagedSource) {
        return $stagedSource
    }

    $repoSource = Join-Path $PSScriptRoot "src\MajestyGuard.CVEngine"
    if (Test-Path -LiteralPath $repoSource) {
        return $repoSource
    }

    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $repoSource = Join-Path $repoRoot "src\MajestyGuard.CVEngine"
    if (Test-Path -LiteralPath $repoSource) {
        return $repoSource
    }

    throw "CVEngine source directory not found."
}

function Set-ExplicitProgramDataAcl {
    New-Item -ItemType Directory -Force -Path $PROGRAMDATA_DIR | Out-Null
    icacls $PROGRAMDATA_DIR /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "Users:(OI)(CI)R" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to set explicit ACL on $PROGRAMDATA_DIR"
    }
}

function Clear-ProgramDataEfsEncryption {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $encryptedFiles = @(
        Get-ChildItem -LiteralPath $Path -File -Force -Recurse -ErrorAction SilentlyContinue |
            Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::Encrypted) -ne 0 }
    )

    foreach ($file in $encryptedFiles) {
        try {
            [System.IO.File]::Decrypt($file.FullName)
            Write-Host "    Decrypted EFS runtime file: $($file.FullName)" -ForegroundColor Gray
        } catch {
            throw "Failed to decrypt EFS-encrypted runtime file '$($file.FullName)': $($_.Exception.Message)"
        }
    }
}

function Install-ProgramDataCvRuntime {
    $cvSource = Resolve-CvEngineSource

    New-Item -ItemType Directory -Force -Path $PROGRAMDATA_CV_DIR | Out-Null
    New-Item -ItemType Directory -Force -Path $PROGRAMDATA_MODEL_DIR | Out-Null

    Copy-DirectoryContentsFiltered `
        -Source $cvSource `
        -Destination $PROGRAMDATA_CV_DIR `
        -ExcludeNames @(".venv", ".pytest_cache", "__pycache__", "models") `
        -ExcludeFiles @("*.pyc", "*.pyo", ".coverage", "test_*.py")

    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $modelSources = @(
        (Join-Path $cvSource "models"),
        (Join-Path $repoRoot "src\MajestyGuard.CVEngine\models")
    )
    foreach ($modelSource in ($modelSources | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $modelSource) {
            Copy-DirectoryContentsFiltered -Source $modelSource -Destination $PROGRAMDATA_MODEL_DIR
            break
        }
    }

    Clear-ProgramDataEfsEncryption -Path $PROGRAMDATA_DIR
    Set-ExplicitProgramDataAcl
}

function Set-JsonProperty {
    param(
        [Parameter(Mandatory=$true)]
        [psobject]$Target,
        [Parameter(Mandatory=$true)]
        [string]$Name,
        [AllowNull()]
        [object]$Value
    )

    if ($Target.PSObject.Properties.Name -contains $Name) {
        $Target.$Name = $Value
    } else {
        $Target | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Sync-ServiceProfileConfig {
    $userConfigPath = Join-Path $env:APPDATA "MajestyGuard\config.json"
    $serviceConfigPath = "$env:WINDIR\System32\config\systemprofile\AppData\Roaming\MajestyGuard\config.json"

    if (-not (Test-Path -LiteralPath $userConfigPath)) {
        Write-Warning "User config not found at $userConfigPath. Service may remain dormant until enrollment/config exists."
        return
    }

    $serviceConfigDir = [System.IO.Path]::GetDirectoryName($serviceConfigPath)
    New-Item -ItemType Directory -Force -Path $serviceConfigDir | Out-Null
    Copy-Item -LiteralPath $userConfigPath -Destination $serviceConfigPath -Force

    try {
        $configJson = Get-Content -LiteralPath $serviceConfigPath -Raw | ConvertFrom-Json
        $cvPythonPath = Resolve-CvPythonPath
        if (-not [string]::IsNullOrWhiteSpace($cvPythonPath)) {
            Set-JsonProperty -Target $configJson -Name "CvPythonPath" -Value $cvPythonPath
        } else {
            Write-Warning "No Python executable found for LocalSystem CV launch. Service may log 'Python not found'."
        }

        $cvScriptPath = Join-Path $PROGRAMDATA_CV_DIR "cv_server.py"
        Set-JsonProperty -Target $configJson -Name "CvScriptPath" -Value $cvScriptPath

        $installedModelDir = $PROGRAMDATA_MODEL_DIR
        if (Test-Path -LiteralPath $installedModelDir) {
            Set-JsonProperty -Target $configJson -Name "ModelDirectory" -Value $installedModelDir
        }
        if ($DisableServiceOverlayLaunch) {
            Set-JsonProperty -Target $configJson -Name "EnableOverlayLaunch" -Value $false
        }

        $configJson | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $serviceConfigPath -Encoding UTF8

        $sid = $null
        if ($configJson.PSObject.Properties.Name -contains "EnrolledUserSid") {
            $sid = [string]$configJson.EnrolledUserSid
        }
        if (-not [string]::IsNullOrWhiteSpace($sid)) {
            New-Item -Path "HKLM:\SOFTWARE\MajestyGuard" -Force | Out-Null
            Set-ItemProperty -Path "HKLM:\SOFTWARE\MajestyGuard" -Name "EnrolledUserSid" -Value $sid -Force
        }
    } catch {
        Write-Warning "Copied service config but could not mirror EnrolledUserSid to HKLM: $($_.Exception.Message)"
    }

    Write-Host "    Service profile config synced for LocalSystem" -ForegroundColor Gray
}

function Get-MajestyGuardBinaries {
    $binaryPatterns = @("MajestyGuard*.exe", "MajestyGuard*.dll")
    Get-ChildItem -LiteralPath $INSTALL_DIR -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            $name = $_.Name
            $binaryPatterns | Where-Object { $name -like $_ }
        }
}

function Add-CertificateToLocalMachineStore {
    param(
        [Parameter(Mandatory=$true)]
        [System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate,
        [Parameter(Mandatory=$true)]
        [string]$StoreName
    )

    $store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
        $StoreName,
        [System.Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine
    )
    $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    try {
        $existing = $store.Certificates.Find(
            [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
            $Certificate.Thumbprint,
            $false
        )
        if ($existing.Count -eq 0) {
            $store.Add($Certificate)
        }
    } finally {
        $store.Close()
    }
}

function Get-DevSigningCertificate {
    $subject = "CN=MajestyGuard Dev"
    $cert = Get-ChildItem -Path "Cert:\LocalMachine\My" -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq $subject -and $_.NotAfter -gt (Get-Date) } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1

    if (-not $cert) {
        $certParams = @{
            Subject           = $subject
            CertStoreLocation = "Cert:\LocalMachine\My"
            Type              = "CodeSigning"
            NotAfter          = (Get-Date).AddYears(3)
            KeyUsage          = "DigitalSignature"
            HashAlgorithm     = "SHA256"
        }
        $cert = New-SelfSignedCertificate @certParams
    }

    foreach ($store in @("Root", "TrustedPublisher")) {
        Add-CertificateToLocalMachineStore -Certificate $cert -StoreName $store
    }

    return $cert
}

function Enable-DevCodeSigning {
    if ($EnableTestSigning) {
        bcdedit /set testsigning on 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "bcdedit failed; test signing was not enabled."
        }
        Write-Host "    Test signing boot option enabled. Reboot required before relying on it." -ForegroundColor Yellow
    }

    $cert = Get-DevSigningCertificate
    $binaries = @(Get-MajestyGuardBinaries)
    if ($binaries.Count -eq 0) {
        throw "No MajestyGuard binaries found to sign in $INSTALL_DIR"
    }

    foreach ($binary in $binaries) {
        $sig = Get-AuthenticodeSignature -FilePath $binary.FullName
        if ($sig.Status -eq "Valid") {
            continue
        }

        $signed = Set-AuthenticodeSignature -FilePath $binary.FullName -Certificate $cert -HashAlgorithm SHA256
        if ($signed.Status -ne "Valid") {
            throw "Signing failed for $($binary.FullName): $($signed.Status) $($signed.StatusMessage)"
        }
    }

    Write-Host "    MajestyGuard user-mode binaries signed with local dev certificate" -ForegroundColor Gray
}

function Stop-InstalledMajestyGuardRuntime {
    if ($CopyOnly) { return }

    $existingService = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
    if ($existingService) {
        Write-Host "    Stopping existing service before copying files..." -ForegroundColor Gray
        Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
        Invoke-ScExe -Arguments @("delete", $SERVICE_NAME) | Out-Null
        Wait-ServiceDeleted -Name $SERVICE_NAME
    }

    $installedRoot = [System.IO.Path]::GetFullPath($INSTALL_DIR).TrimEnd('\')
    $dotnetHostNeedle = "MajestyGuard.Service.Host\MajestyGuard.Service.dll"
    $processes = @(Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $exePath = [string]$_.ExecutablePath
        $cmdLine = [string]$_.CommandLine
        ($exePath -and $exePath.StartsWith($installedRoot, [System.StringComparison]::OrdinalIgnoreCase)) -or
        ($cmdLine -and $cmdLine.IndexOf($dotnetHostNeedle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
    })

    foreach ($proc in $processes) {
        if ($proc.ProcessId -eq $PID) { continue }
        Write-Host "    Stopping locked MajestyGuard process $($proc.ProcessId): $($proc.Name)" -ForegroundColor Gray
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }

    if ($processes.Count -gt 0) {
        Start-Sleep -Seconds 2
    }
}

if ($CopyOnly -and [string]::IsNullOrWhiteSpace($InstallDir)) {
    throw "Copy-only mode requires -InstallDir so it never defaults to Program Files."
}

if (-not $CopyOnly -and -not (Test-IsAdministrator)) {
    throw "Machine-level install requires Administrator. For a no-machine-state check, use -CopyOnly -InstallDir <path>."
}

$INSTALL_DIR  = if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    "$env:ProgramFiles\MajestyGuard"
} else {
    [System.IO.Path]::GetFullPath($InstallDir)
}
$SERVICE_NAME = "MajestyGuardService"
$SERVICE_EXE  = "$INSTALL_DIR\MajestyGuard.Service.exe"
$DOTNET_SERVICE_DLL = "$INSTALL_DIR\MajestyGuard.Service.Host\MajestyGuard.Service.dll"
$CP_DLL       = "$INSTALL_DIR\MajestyGuard.CredentialProvider.dll"
$PYTHON_DIR   = "$INSTALL_DIR\python"
$PROGRAMDATA_DIR = Join-Path $env:ProgramData "MajestyGuard"
$PROGRAMDATA_CV_DIR = Join-Path $PROGRAMDATA_DIR "CVEngine"
$PROGRAMDATA_MODEL_DIR = Join-Path $PROGRAMDATA_DIR "models"
$CV_DIR       = $PROGRAMDATA_CV_DIR
$CP_CLSID     = "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"
$ROLLBACK_SRC = "$PSScriptRoot\Uninstall.ps1"
$ROLLBACK_DST = "$INSTALL_DIR\Uninstall.ps1"

if (-not $AcknowledgeLoginRisk) {
    throw @"
MajestyGuard installer is intentionally gated.

This script can register a Windows Credential Provider and change machine-level
security settings. Re-run with -AcknowledgeLoginRisk and only the switches you
want, for example:

  .\Install.ps1 -AcknowledgeLoginRisk -SkipPythonSetup
  .\Install.ps1 -AcknowledgeLoginRisk -InstallService -StartServiceAfterInstall
  .\Install.ps1 -AcknowledgeLoginRisk -InstallService -StartServiceAfterInstall -UseDotnetServiceHost
  .\Install.ps1 -AcknowledgeLoginRisk -EnableCredentialProvider -InstallService
  .\Install.ps1 -AcknowledgeLoginRisk -CopyOnly -InstallDir "$env:TEMP\MajestyGuard-copycheck"

Optional high-risk switches:
  -EnableDevSigning -EnableTestSigning -AutoStartOverlay -EnableRegistryHardening -EnableSafeBoot
"@
}

if (-not (Test-Path -LiteralPath $ROLLBACK_SRC)) {
    throw "Rollback script missing from staged package: $ROLLBACK_SRC. Rebuild with .\Build.ps1 before installing."
}

Write-Host ""
Write-Host "  MajestyGuard Installer v1.0" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/13] Creating install directory..." -ForegroundColor Yellow
Stop-InstalledMajestyGuardRuntime
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\models" | Out-Null

$source = $PSScriptRoot
Copy-Item "$source\*" $INSTALL_DIR -Recurse -Force -Exclude "Install.ps1","Uninstall.ps1","CVEngine"
Copy-Item $ROLLBACK_SRC $ROLLBACK_DST -Force
if (-not (Test-Path -LiteralPath $ROLLBACK_DST)) {
    throw "Rollback script was not copied to $ROLLBACK_DST; aborting install."
}
Write-Host "    Files copied to $INSTALL_DIR" -ForegroundColor Gray

if ($CopyOnly) {
    $copyOnlyCvDir = Join-Path $INSTALL_DIR "CVEngine"
    Copy-DirectoryContentsFiltered `
        -Source (Resolve-CvEngineSource) `
        -Destination $copyOnlyCvDir `
        -ExcludeNames @(".venv", ".pytest_cache", "__pycache__", "models") `
        -ExcludeFiles @("*.pyc", "*.pyo", ".coverage", "test_*.py")

    Write-Host ""
    Write-Host "Copy-only staging complete." -ForegroundColor Green
    Write-Host "No machine-level state was changed." -ForegroundColor DarkGray
    Write-Host "Copied files: $INSTALL_DIR" -ForegroundColor DarkGray
    Write-Host "Rollback script: $ROLLBACK_DST" -ForegroundColor DarkGray
    return
}

Write-Host "    Copying CV runtime to $PROGRAMDATA_CV_DIR" -ForegroundColor Gray
Install-ProgramDataCvRuntime

if ($EnableDevSigning -or $EnableTestSigning) {
    Write-Host "[2/13] Applying dev code signing..." -ForegroundColor Yellow
    Enable-DevCodeSigning
}

Write-Host "[2/13] Registering Credential Provider..." -ForegroundColor Yellow
if (-not $EnableCredentialProvider) {
    Write-Host "    Skipped. Pass -EnableCredentialProvider to register the login provider." -ForegroundColor DarkGray
} elseif (-not (Test-Path $CP_DLL)) {
    Write-Warning "Credential Provider DLL not found at $CP_DLL; skipping registration."
} else {
    $result = Start-Process regsvr32 -ArgumentList "/s `"$CP_DLL`"" -Wait -PassThru
    if ($result.ExitCode -ne 0) {
        throw "regsvr32 failed with exit code $($result.ExitCode)."
    }
    Write-Host "    Credential Provider registered" -ForegroundColor Gray
}

Write-Host "[3/13] Installing Windows Service..." -ForegroundColor Yellow
if (-not $InstallService) {
    Write-Host "    Skipped. Pass -InstallService to create the Windows service." -ForegroundColor DarkGray
} else {
    $existingService = Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue
    if ($existingService) {
        Write-Host "    Stopping existing service..." -ForegroundColor Gray
        Stop-Service -Name $SERVICE_NAME -Force -ErrorAction SilentlyContinue
        Invoke-ScExe -Arguments @("delete", $SERVICE_NAME) | Out-Null
        Wait-ServiceDeleted -Name $SERVICE_NAME
    }

    Sync-ServiceProfileConfig
    $serviceBinaryPath = "`"$SERVICE_EXE`""
    if ($UseDotnetServiceHost) {
        if (-not (Test-Path -LiteralPath $DOTNET_SERVICE_DLL)) {
            throw "Framework-dependent service host missing: $DOTNET_SERVICE_DLL"
        }

        $dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
        if (-not $dotnetCommand) {
            throw "dotnet.exe not found. Install the .NET 8 runtime or run without -UseDotnetServiceHost."
        }

        $dotnetExe = $dotnetCommand.Source
        $dotnetSig = Get-AuthenticodeSignature -FilePath $dotnetExe
        if ($dotnetSig.Status -ne "Valid") {
            throw "dotnet.exe signature is not valid: $dotnetExe"
        }

        $serviceBinaryPath = "`"$dotnetExe`" `"$DOTNET_SERVICE_DLL`""
        Write-Host "    Using Microsoft-signed dotnet.exe service host" -ForegroundColor Gray
    }

    New-Service -Name $SERVICE_NAME -BinaryPathName $serviceBinaryPath -DisplayName "Majesty Guard Security Service" -StartupType Automatic | Out-Null
    $createdService = Get-Service -Name $SERVICE_NAME -ErrorAction Stop
    Invoke-ScExe -Arguments @("description", $SERVICE_NAME, "Biometric presence monitoring and face recognition for Majesty Guard.") | Out-Null
    Invoke-ScExe -Arguments @("failure", $SERVICE_NAME, "reset=", "86400", "actions=", "restart/5000/restart/10000/restart/30000") | Out-Null
    Write-Host "    Service installed (auto-start, LocalSystem)" -ForegroundColor Gray
}

Write-Host "[4/13] Setting up Python CV engine..." -ForegroundColor Yellow
$pythonExe = Resolve-CvPythonPath
if ($SkipPythonSetup) {
    Write-Host "    Skipped. Using CV Python: $pythonExe" -ForegroundColor DarkGray
} else {
    Write-Host "    Using Python: $pythonExe" -ForegroundColor Gray
    & $pythonExe -m pip install -r "$CV_DIR\requirements.txt" --quiet
    Write-Host "    Python dependencies installed" -ForegroundColor Gray
}

Write-Host "[5/13] Downloading models..." -ForegroundColor Yellow
if ($SkipModelDownload -or -not $pythonExe) {
    Write-Host "    Skipped." -ForegroundColor DarkGray
} else {
    $downloadScript = "$CV_DIR\download_models.py"
    if (Test-Path $downloadScript) {
        & $pythonExe $downloadScript "$INSTALL_DIR\models"
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Model download failed; re-run with internet access."
        } else {
            Write-Host "    Models ready" -ForegroundColor Gray
        }
    } else {
        Write-Warning "download_models.py not found; model download skipped."
    }
}

Write-Host "[6/13] Checking code signing setup..." -ForegroundColor Yellow
if (-not (Test-Path $INSTALL_DIR)) {
    Write-Host "    Skipped. Install directory not present." -ForegroundColor DarkGray
} else {
    $unsigned = @(Get-MajestyGuardBinaries | Where-Object {
        (Get-AuthenticodeSignature -FilePath $_.FullName).Status -ne "Valid"
    })
    if ($unsigned.Count -eq 0) {
        Write-Host "    MajestyGuard binaries are signed" -ForegroundColor Green
    } else {
        Write-Warning "Unsigned MajestyGuard binaries remain. Pass -EnableDevSigning to locally sign dev binaries."
        $unsigned | ForEach-Object { Write-Warning "Unsigned: $($_.Name)" }
    }
}

Write-Host "[7/13] Configuring firewall..." -ForegroundColor Yellow
if ($pythonExe) {
    $ruleName = "MajestyGuard - Block CVEngine Outbound"
    Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $ruleName -Direction Outbound -Program $pythonExe -Action Block -Profile Any | Out-Null
    Write-Host "    CVEngine blocked from outbound internet" -ForegroundColor Gray
} else {
    Write-Host "    Skipped (no Python found)" -ForegroundColor DarkGray
}

Write-Host "[8/13] Creating Task Scheduler backup task..." -ForegroundColor Yellow
if (-not $InstallService) {
    Write-Host "    Skipped. Task guard is only created with -InstallService." -ForegroundColor DarkGray
} else {
    $taskName = "MajestyGuard_ServiceGuard"
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute "sc.exe" -Argument "start $SERVICE_NAME"
    $triggerBoot = New-ScheduledTaskTrigger -AtStartup
    $triggerLogon = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggerBoot,$triggerLogon -Settings $settings -Principal $principal -Description "Ensures MajestyGuard service is running" | Out-Null
    Write-Host "    Task Scheduler guard registered" -ForegroundColor Gray
}

Write-Host "[9/13] Configuring lock screen integration..." -ForegroundColor Yellow
$cpKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$CP_CLSID"
if (-not $EnableCredentialProvider) {
    Write-Host "    Skipped. Pass -EnableCredentialProvider to activate lock screen integration." -ForegroundColor DarkGray
} elseif (Test-Path $cpKey) {
    Remove-ItemProperty -Path $cpKey -Name "Disabled" -ErrorAction SilentlyContinue
    Write-Host "    Credential Provider enabled for lock screen" -ForegroundColor Gray
} else {
    Write-Host "    Credential Provider key not present yet" -ForegroundColor DarkGray
}

Write-Host "[10/13] Setting overlay to auto-start..." -ForegroundColor Yellow
if (-not $AutoStartOverlay) {
    Write-Host "    Skipped. Pass -AutoStartOverlay to start overlay at every logon." -ForegroundColor DarkGray
} else {
    $overlayExe = "$INSTALL_DIR\MajestyGuard.Overlay.exe"
    $runKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    Set-ItemProperty -Path $runKey -Name "MajestyGuardOverlay" -Value "`"$overlayExe`"" -Force
    Write-Host "    Overlay registered in HKLM Run" -ForegroundColor Gray
}

Write-Host "[11/13] Hardening service permissions..." -ForegroundColor Yellow
if (-not $InstallService) {
    Write-Host "    Skipped. Service hardening is only applied with -InstallService." -ForegroundColor DarkGray
} else {
    $sddl = 'D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)'
    Invoke-ScExe -Arguments @("sdset", $SERVICE_NAME, $sddl) | Out-Null
    Invoke-ScExe -Arguments @("config", $SERVICE_NAME, "start=", "delayed-auto") | Out-Null
    Write-Host "    Service hardened" -ForegroundColor Gray
}

Write-Host "[12/13] Registry hardening and SafeBoot registration..." -ForegroundColor Yellow
if ($EnableRegistryHardening) {
    $cpRootKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers"
    try {
        $acl = Get-Acl $cpRootKey
        $acl.SetAccessRuleProtection($true, $false)
        $systemSid = [System.Security.Principal.SecurityIdentifier]"S-1-5-18"
        $adminSid  = [System.Security.Principal.SecurityIdentifier]"S-1-5-32-544"
        $userSid   = [System.Security.Principal.SecurityIdentifier]"S-1-5-32-545"
        $acl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($systemSid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow")))
        $acl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($adminSid, "ReadKey", "ContainerInherit,ObjectInherit", "None", "Allow")))
        $acl.AddAccessRule((New-Object System.Security.AccessControl.RegistryAccessRule($userSid, "ReadKey", "ContainerInherit,ObjectInherit", "None", "Allow")))
        Set-Acl -Path $cpRootKey -AclObject $acl
        Set-ItemProperty "HKCU:\Control Panel\Desktop" -Name "ScreenSaveActive" -Value "0" -ErrorAction SilentlyContinue
        Set-ItemProperty "HKCU:\Control Panel\Desktop" -Name "SCRNSAVE.EXE" -Value "" -ErrorAction SilentlyContinue
        Write-Host "    Registry hardening applied" -ForegroundColor Gray
    } catch {
        Write-Warning "Registry ACL hardening failed: $_"
    }
} else {
    Write-Host "    Registry hardening skipped." -ForegroundColor DarkGray
}

if ($EnableSafeBoot) {
    foreach ($mode in @("Minimal", "Network")) {
        $svcPath = "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\$mode\$SERVICE_NAME"
        $cpPath  = "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\$mode\$CP_CLSID"
        New-Item -Path $svcPath -Force | Out-Null
        Set-ItemProperty $svcPath -Name "(Default)" -Value "Service"
        New-Item -Path $cpPath -Force | Out-Null
        Set-ItemProperty $cpPath -Name "(Default)" -Value "Driver"
    }
    Write-Host "    SafeBoot entries registered" -ForegroundColor Gray
} else {
    Write-Host "    SafeBoot registration skipped." -ForegroundColor DarkGray
}

New-Item -Path "HKLM:\SOFTWARE\MajestyGuard" -Force | Out-Null

Write-Host "[13/13] Starting service..." -ForegroundColor Yellow
if (-not $StartServiceAfterInstall) {
    Write-Host "    Skipped. Pass -StartServiceAfterInstall to start it now." -ForegroundColor DarkGray
} elseif (-not $InstallService) {
    Write-Warning "Cannot start service because -InstallService was not passed."
} else {
    Start-Service -Name $SERVICE_NAME
    $svc = Get-Service -Name $SERVICE_NAME -ErrorAction Stop
    Write-Host "    Service status: $($svc.Status)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "MajestyGuard staged install complete." -ForegroundColor Green
Write-Host "Uninstall: $INSTALL_DIR\Uninstall.ps1" -ForegroundColor DarkGray
