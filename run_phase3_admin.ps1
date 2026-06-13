# MajestyGuard Phase 2/3 admin runner.
# Run once from an Administrator PowerShell. It is intentionally verbose.

[CmdletBinding()]
param(
    [switch]$SkipLockScreenPrompts,
    [switch]$ServiceOnly,
    [switch]$UseDotnetServiceHost,
    [switch]$EnableDevSigningIfNeeded,
    [switch]$EnableTestSigningIfNeeded
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = "C:\tmp\MajestyGuard"
$Staged = Join-Path $Root "build\staged"
$InstallScript = Join-Path $Staged "Install.ps1"
$UninstallScript = Join-Path $Staged "Uninstall.ps1"
$ServiceExe = Join-Path $Staged "MajestyGuard.Service.exe"
$CpDll = Join-Path $Staged "MajestyGuard.CredentialProvider.dll"
$EmbeddingPath = Join-Path $env:LOCALAPPDATA "MajestyGuard\embeddings_v2.npy"
$ServiceName = "MajestyGuardService"
$CpGuid = "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"
$AdminLogDir = Join-Path $Root "admin-logs"
$TranscriptPath = Join-Path $AdminLogDir ("phase3-admin-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$RunStartedAt = Get-Date
$InstalledRoot = Join-Path $env:ProgramFiles "MajestyGuard"
$InstalledServiceExe = Join-Path $InstalledRoot "MajestyGuard.Service.exe"
$InstalledServiceDll = Join-Path $InstalledRoot "MajestyGuard.Service.Host\MajestyGuard.Service.dll"

New-Item -ItemType Directory -Force -Path $AdminLogDir | Out-Null
Start-Transcript -Path $TranscriptPath -Force | Out-Null

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host ("=== {0} ===" -f $Title) -ForegroundColor Cyan
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ServiceStatusLabel {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc) { return "NotInstalled" }
    return [string]$svc.Status
}

function Test-NamedPipeAccessible {
    param(
        [Parameter(Mandatory=$true)][string]$PipeName,
        [int]$TimeoutMs = 5000
    )
    try {
        $client = [System.IO.Pipes.NamedPipeClientStream]::new(
            ".",
            $PipeName,
            [System.IO.Pipes.PipeDirection]::InOut
        )
        $client.Connect($TimeoutMs)
        $client.Dispose()
        return "Accessible"
    } catch {
        return "Timeout"
    }
}

function Get-CpRegistration {
    $paths = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$CpGuid",
        "HKLM:\SOFTWARE\Classes\CLSID\$CpGuid"
    )
    $found = @()
    foreach ($path in $paths) {
        if (Test-Path $path) { $found += $path }
    }
    return $found
}

function Get-RecentMajestyEvents {
    try {
        Get-WinEvent -LogName Application -MaxEvents 40 |
            Where-Object { $_.ProviderName -like "*MajestyGuard*" } |
            Select-Object TimeCreated, LevelDisplayName, Message |
            Format-List
    } catch {
        Write-Host "EVENT_LOG_READ_FAILED: $($_.Exception.Message)"
    }
}

function Get-CodeIntegrityServiceBlockEvents {
    param([datetime]$Since = (Get-Date).AddMinutes(-15))

    $servicePaths = @(
        $InstalledServiceExe,
        $InstalledServiceDll,
        "MajestyGuard.Service.exe",
        "MajestyGuard.Service.dll",
        "MajestyGuard.Service.Host"
    )

    try {
        Get-WinEvent -FilterHashtable @{
            LogName = "Microsoft-Windows-CodeIntegrity/Operational"
            StartTime = $Since
        } -ErrorAction Stop |
            Where-Object {
                $message = [string]$_.Message
                $matchesService = $false
                foreach ($path in $servicePaths) {
                    if ($message -like "*$path*") {
                        $matchesService = $true
                        break
                    }
                }

                ($_.Id -in @(3033, 3077)) -and
                    ($matchesService -or $message -like "*0x800711C7*")
            }
    } catch {
        Write-Host "CODE_INTEGRITY_LOG_READ_FAILED: $($_.Exception.Message)"
        return @()
    }
}

function Show-CodeIntegrityServiceBlocks {
    param([datetime]$Since = $RunStartedAt)

    $events = @(Get-CodeIntegrityServiceBlockEvents -Since $Since)
    if ($events.Count -eq 0) {
        Write-Host "SERVICE_BLOCKED_BY_CODE_INTEGRITY: NoRecentEvent"
        return $false
    }

    Write-Host "SERVICE_BLOCKED_BY_CODE_INTEGRITY: Yes" -ForegroundColor Red
    Write-Host "CODE_INTEGRITY_EXPECTED_ERROR: 0x800711C7"
    foreach ($event in ($events | Select-Object -First 6)) {
        $message = [string]$event.Message
        $policyMatch = [regex]::Match($message, "Policy ID:\{[^}]+\}")
        $errorMatch = [regex]::Match($message, "0x[0-9A-Fa-f]{8}")

        Write-Host "CODE_INTEGRITY_EVENT_ID: $($event.Id)"
        Write-Host "CODE_INTEGRITY_TIME: $($event.TimeCreated.ToString('o'))"
        if ($policyMatch.Success) {
            Write-Host "CODE_INTEGRITY_POLICY_ID: $($policyMatch.Value)"
        }
        if ($errorMatch.Success) {
            Write-Host "CODE_INTEGRITY_ERROR_CODE: $($errorMatch.Value)"
        }
        Write-Host "CODE_INTEGRITY_MESSAGE: $($message -replace '\s+', ' ')"
    }

    return $true
}

function Assert-UserDataPresent {
    if (-not (Test-Path -LiteralPath $EmbeddingPath)) {
        throw "Required enrollment artifact missing: $EmbeddingPath"
    }
}

$serviceStatus = "Unknown"
$cvPipeStatus = "Unknown"
$credPipeStatus = "Unknown"
$cpRegistered = "NOT_REGISTERED"
$signingNeeded = "Unknown"
$lockScreenResult = "SKIPPED"
$rollbackResult = "SKIPPED"
$rollbackClean = "No"

try {
    Write-Section "Section 1: Pre-flight checks"
    if (-not (Test-IsAdministrator)) { throw "Must run as Administrator" }
    Write-Host "ADMIN: True"
    Write-Host "ROOT: $Root"
    Write-Host "STAGED: $Staged"
    Write-Host "INSTALL_SCRIPT: $InstallScript"
    Write-Host "UNINSTALL_SCRIPT: $UninstallScript"
    Write-Host "SERVICE_EXE: $ServiceExe"
    Write-Host "CP_DLL: $CpDll"
    Write-Host "EMBEDDINGS: $EmbeddingPath"
    Write-Host "TRANSCRIPT: $TranscriptPath"
    Write-Host ("DOTNET_SERVICE_HOST: {0} (-UseDotnetServiceHost)" -f $(if ($UseDotnetServiceHost) { "Enabled" } else { "Disabled" }))

    $existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existingService) {
        Write-Host "PREEXISTING_SERVICE: $($existingService.Status) / $($existingService.StartType)" -ForegroundColor Yellow
    } else {
        Write-Host "PREEXISTING_SERVICE: NotInstalled"
    }

    Assert-UserDataPresent
    foreach ($required in @($Staged, $InstallScript, $UninstallScript, $ServiceExe, $CpDll)) {
        if (-not (Test-Path -LiteralPath $required)) { throw "Missing required staged path: $required" }
    }
    powershell -NoProfile -ExecutionPolicy Bypass -File $UninstallScript -WhatIf | Out-Host
    Assert-UserDataPresent
    Write-Host "PREFLIGHT_USER_DATA_PRESENT: True"

    $serviceSig = Get-AuthenticodeSignature $ServiceExe
    Write-Host "SERVICE_EXE_SIGNATURE: $($serviceSig.Status)"
    if ($UseDotnetServiceHost -and $EnableDevSigningIfNeeded) {
        Write-Host "DOTNET_HOST_DEV_SIGNING_CONFLICT"
        Write-Host "Recent SAC diagnostics showed locally dev-signed managed DLLs are blocked when loaded by dotnet.exe." -ForegroundColor Yellow
        Write-Host "For this service-only SAC probe, re-run with -UseDotnetServiceHost and without -EnableDevSigningIfNeeded." -ForegroundColor Yellow
        Write-Host "=== PHASE 3 ADMIN RESULT ==="
        Write-Host "SERVICE_STATUS: $(Get-ServiceStatusLabel)"
        Write-Host "MajestyGuard_CV_PIPE: NotRun"
        Write-Host "MajestyGuard_CredProv_PIPE: NotRun"
        Write-Host "CP_REGISTERED: NOT_REGISTERED"
        Write-Host "SIGNING_NEEDED: AvoidDevSigningForDotnetHost"
        Write-Host "LOCK_SCREEN_TILE: DOTNET_HOST_DEV_SIGNING_CONFLICT"
        Write-Host "ROLLBACK_CLEAN: NotRun"
        Write-Host ("USER_DATA_PRESERVED: {0}" -f (Test-Path -LiteralPath $EmbeddingPath))
        Write-Host "TRANSCRIPT: $TranscriptPath"
        Write-Host "==========================="
        return
    }
    if ($UseDotnetServiceHost -and $serviceSig.Status -ne "Valid") {
        Write-Host "DOTNET_HOST_UNSIGNED_SERVICE_EXE_ALLOWED: True"
        Write-Host "Microsoft-signed dotnet.exe is the service binary in this mode; the staged apphost signature is not required for this probe." -ForegroundColor Yellow
        Write-Host "SIGNING_NEEDED: NotRequiredForDotnetHost"
    }
    if ($serviceSig.Status -ne "Valid" -and -not $EnableDevSigningIfNeeded -and -not $UseDotnetServiceHost) {
        Write-Host "SERVICE_SIGNING_REQUIRED"
        Write-Host "Windows Application Control blocked the previous unsigned service binary." -ForegroundColor Yellow
        Write-Host "Re-run with -EnableDevSigningIfNeeded to create/trust a local MajestyGuard dev publisher cert and sign MajestyGuard binaries." -ForegroundColor Yellow
        Write-Host "=== PHASE 3 ADMIN RESULT ==="
        Write-Host "SERVICE_STATUS: $(Get-ServiceStatusLabel)"
        Write-Host "MajestyGuard_CV_PIPE: NotRun"
        Write-Host "MajestyGuard_CredProv_PIPE: NotRun"
        Write-Host "CP_REGISTERED: NOT_REGISTERED"
        Write-Host "SIGNING_NEEDED: DevSigning"
        Write-Host "LOCK_SCREEN_TILE: SERVICE_SIGNING_REQUIRED"
        Write-Host "ROLLBACK_CLEAN: NotRun"
        Write-Host ("USER_DATA_PRESERVED: {0}" -f (Test-Path -LiteralPath $EmbeddingPath))
        Write-Host "TRANSCRIPT: $TranscriptPath"
        Write-Host "==========================="
        return
    }
    if ($EnableDevSigningIfNeeded) {
        $signingNeeded = "DevSigning"
    } elseif ($UseDotnetServiceHost) {
        $signingNeeded = "NotRequiredForDotnetHost"
    } else {
        $signingNeeded = "No"
    }

    Write-Section "Section 2: Service install"
    Push-Location $Staged
    try {
        $installArgs = @{
            AcknowledgeLoginRisk   = $true
            InstallService         = $true
            StartServiceAfterInstall = $true
            SkipPythonSetup        = $true
            SkipModelDownload      = $true
        }
        if ($EnableDevSigningIfNeeded) { $installArgs.EnableDevSigning = $true }
        if ($UseDotnetServiceHost) { $installArgs.UseDotnetServiceHost = $true }
        if ($ServiceOnly) { $installArgs.DisableServiceOverlayLaunch = $true }
        & $InstallScript @installArgs
    } finally {
        Pop-Location
    }
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name $ServiceName -ErrorAction Stop
    $serviceStatus = [string]$svc.Status
    $svc | Select-Object Status, StartType, ServiceName, DisplayName | Format-List

    Write-Section "Section 3: Service pipe validation"
    $cvPipeStatus = Test-NamedPipeAccessible -PipeName "MajestyGuard_CV"
    $credPipeStatus = Test-NamedPipeAccessible -PipeName "MajestyGuard_CredProv"
    Write-Host "MajestyGuard_CV_PIPE: $cvPipeStatus"
    Write-Host "MajestyGuard_CredProv_PIPE: $credPipeStatus"
    Get-RecentMajestyEvents

    if ($ServiceOnly) {
        $registration = @(Get-CpRegistration)
        $cpRegistered = if ($registration.Count -gt 0) { $CpGuid } else { "NOT_REGISTERED" }
        Write-Host "SERVICE_ONLY_RESULT: COMPLETE"
        Write-Host "=== PHASE 3 ADMIN RESULT ==="
        Write-Host "SERVICE_STATUS: $serviceStatus"
        Write-Host "MajestyGuard_CV_PIPE: $cvPipeStatus"
        Write-Host "MajestyGuard_CredProv_PIPE: $credPipeStatus"
        Write-Host "CP_REGISTERED: $cpRegistered"
        Write-Host "SIGNING_NEEDED: $signingNeeded"
        Write-Host "LOCK_SCREEN_TILE: SERVICE_ONLY_SKIPPED"
        Write-Host "ROLLBACK_CLEAN: NotRun"
        Write-Host ("USER_DATA_PRESERVED: {0}" -f (Test-Path -LiteralPath $EmbeddingPath))
        Write-Host "TRANSCRIPT: $TranscriptPath"
        Write-Host "==========================="
        return
    }

    Write-Section "Section 4: CP behavior analysis"
    $beforeCp = @(Get-CpRegistration)
    if ($beforeCp.Count -eq 0) {
        Write-Host "CP_PRE_REGISTRATION: NOT_REGISTERED"
    } else {
        Write-Host "CP_PRE_REGISTRATION: $CpGuid"
        $beforeCp | ForEach-Object { Write-Host "  $_" }
    }
    $sig = Get-AuthenticodeSignature $CpDll
    $sig | Select-Object Status, SignerCertificate | Format-List

    Write-Section "Section 5: Test signing decision"
    if ($sig.Status -eq "Valid") {
        $signingNeeded = "No"
        Write-Host "SIGNING_NOT_REQUIRED"
    } else {
        $signingNeeded = "Yes"
        Write-Host "SIGNING_REQUIRED"
        if ($EnableDevSigningIfNeeded) {
            $signingNeeded = "DevSigning"
            Write-Host "CP_DEV_SIGNING_WILL_BE_APPLIED"
        } elseif (-not $EnableTestSigningIfNeeded) {
            Write-Host "TEST_SIGNING_NOT_ENABLED"
            Write-Host "Re-run with -EnableTestSigningIfNeeded only after explicitly accepting test-signing risk." -ForegroundColor Yellow
            Write-Host "=== PHASE 3 ADMIN RESULT ==="
            Write-Host "SERVICE_STATUS: $serviceStatus"
            Write-Host "MajestyGuard_CV_PIPE: $cvPipeStatus"
            Write-Host "MajestyGuard_CredProv_PIPE: $credPipeStatus"
            Write-Host "CP_REGISTERED: NOT_REGISTERED"
            Write-Host "SIGNING_NEEDED: Yes"
            Write-Host "LOCK_SCREEN_TILE: TEST_SIGNING_NOT_ENABLED"
            Write-Host "ROLLBACK_CLEAN: NotRun"
            Write-Host ("USER_DATA_PRESERVED: {0}" -f (Test-Path -LiteralPath $EmbeddingPath))
            Write-Host "TRANSCRIPT: $TranscriptPath"
            Write-Host "==========================="
            return
        }
        if ($EnableTestSigningIfNeeded -and -not $EnableDevSigningIfNeeded) {
            bcdedit /set testsigning on
            Write-Host "REBOOT_REQUIRED_BEFORE_CP"
            Write-Host "=== PHASE 3 ADMIN RESULT ==="
            Write-Host "SERVICE_STATUS: $serviceStatus"
            Write-Host "MajestyGuard_CV_PIPE: $cvPipeStatus"
            Write-Host "MajestyGuard_CredProv_PIPE: $credPipeStatus"
            Write-Host "CP_REGISTERED: NOT_REGISTERED"
            Write-Host "SIGNING_NEEDED: Yes"
            Write-Host "LOCK_SCREEN_TILE: REBOOT_REQUIRED_BEFORE_CP"
            Write-Host "ROLLBACK_CLEAN: NotRun"
            Write-Host ("USER_DATA_PRESERVED: {0}" -f (Test-Path -LiteralPath $EmbeddingPath))
            Write-Host "TRANSCRIPT: $TranscriptPath"
            Write-Host "==========================="
            return
        }
    }

    Write-Section "Section 6: CP registration"
    Push-Location $Staged
    try {
        $cpInstallArgs = @{
            AcknowledgeLoginRisk  = $true
            InstallService        = $true
            EnableCredentialProvider = $true
            SkipPythonSetup       = $true
            SkipModelDownload     = $true
        }
        if ($EnableDevSigningIfNeeded) { $cpInstallArgs.EnableDevSigning = $true }
        if ($UseDotnetServiceHost) { $cpInstallArgs.UseDotnetServiceHost = $true }
        & $InstallScript @cpInstallArgs
    } finally {
        Pop-Location
    }
    $registration = @(Get-CpRegistration)
    if ($registration.Count -gt 0) {
        $cpRegistered = $CpGuid
        Write-Host "CP_REGISTERED: $CpGuid"
        $registration | ForEach-Object { Write-Host "  $_" }
    } else {
        $cpRegistered = "NOT_REGISTERED"
        Write-Host "CP_REGISTERED: NOT_REGISTERED"
    }

    Write-Section "Section 7: Lock screen tile verification prompt"
    if ($SkipLockScreenPrompts) {
        $lockScreenResult = "SKIPPED"
        Write-Host "LOCK_SCREEN_RESULT: SKIPPED"
    } else {
        Write-Host ""
        Write-Host "===================================================" -ForegroundColor Cyan
        Write-Host "ACTION REQUIRED: Lock screen test" -ForegroundColor Cyan
        Write-Host "===================================================" -ForegroundColor Cyan
        Write-Host "1. Press Win+L to lock the PC"
        Write-Host "2. Verify MajestyGuard tile appears alongside PIN/Password"
        Write-Host "3. Click the tile - confirm it does NOT crash LogonUI"
        Write-Host "4. Unlock using PIN or Password (NOT face unlock)"
        Write-Host "5. Return here and type TILE_OK or TILE_MISSING"
        Write-Host ""
        $lockScreenResult = Read-Host "Enter TILE_OK or TILE_MISSING"
        Write-Host "LOCK_SCREEN_RESULT: $lockScreenResult"
    }

    Write-Section "Section 8: CP rollback test"
    Push-Location $Staged
    try {
        & $UninstallScript
    } finally {
        Pop-Location
    }
    Start-Sleep -Seconds 3
    $svcAfterRollback = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svcAfterRollback) {
        Write-Host "POST_ROLLBACK_SERVICE: $($svcAfterRollback.Status)"
    } else {
        Write-Host "POST_ROLLBACK_SERVICE: NotInstalled"
    }
    $postRollbackCp = @(Get-CpRegistration)
    if ($postRollbackCp.Count -eq 0) {
        Write-Host "POST_ROLLBACK_CP: GONE"
    } else {
        Write-Host "POST_ROLLBACK_CP: STILL_PRESENT"
        $postRollbackCp | ForEach-Object { Write-Host "  $_" }
    }
    Assert-UserDataPresent
    if ($SkipLockScreenPrompts) {
        $rollbackResult = "SKIPPED"
        Write-Host "ROLLBACK_RESULT: SKIPPED"
    } else {
        Write-Host ""
        Write-Host "ACTION REQUIRED: Lock PC again and confirm MajestyGuard tile is GONE. Unlock with PIN/Password." -ForegroundColor Cyan
        $rollbackResult = Read-Host "Enter TILE_GONE or TILE_STILL_VISIBLE"
        Write-Host "ROLLBACK_RESULT: $rollbackResult"
    }
    $rollbackClean = if (($postRollbackCp.Count -eq 0) -and (-not $svcAfterRollback)) { "Yes" } else { "No" }

    Write-Section "Section 9: Service reinstall after rollback"
    Push-Location $Staged
    try {
        $finalInstallArgs = @{
            AcknowledgeLoginRisk   = $true
            InstallService         = $true
            StartServiceAfterInstall = $true
            EnableCredentialProvider = $true
            SkipPythonSetup        = $true
            SkipModelDownload      = $true
        }
        if ($EnableDevSigningIfNeeded) { $finalInstallArgs.EnableDevSigning = $true }
        if ($UseDotnetServiceHost) { $finalInstallArgs.UseDotnetServiceHost = $true }
        & $InstallScript @finalInstallArgs
    } finally {
        Pop-Location
    }
    Start-Sleep -Seconds 3
    $svcFinal = Get-Service -Name $ServiceName -ErrorAction Stop
    $serviceStatus = [string]$svcFinal.Status
    $svcFinal | Select-Object Status, StartType, ServiceName, DisplayName | Format-List
    $cvPipeStatus = Test-NamedPipeAccessible -PipeName "MajestyGuard_CV"
    $credPipeStatus = Test-NamedPipeAccessible -PipeName "MajestyGuard_CredProv"
    $finalCp = @(Get-CpRegistration)
    $cpRegistered = if ($finalCp.Count -gt 0) { $CpGuid } else { "NOT_REGISTERED" }

    Write-Section "Section 10: Final state report"
    Assert-UserDataPresent
    Write-Host "=== PHASE 3 ADMIN RESULT ==="
    Write-Host "SERVICE_STATUS: $serviceStatus"
    Write-Host "MajestyGuard_CV_PIPE: $cvPipeStatus"
    Write-Host "MajestyGuard_CredProv_PIPE: $credPipeStatus"
    Write-Host "CP_REGISTERED: $cpRegistered"
    Write-Host "SIGNING_NEEDED: $signingNeeded"
    Write-Host "LOCK_SCREEN_TILE: $lockScreenResult"
    Write-Host "ROLLBACK_CLEAN: $rollbackClean"
    Write-Host ("USER_DATA_PRESERVED: {0}" -f (Test-Path -LiteralPath $EmbeddingPath))
    Write-Host "LOCK_SCREEN_RESULT: $lockScreenResult"
    Write-Host "ROLLBACK_RESULT: $rollbackResult"
    Write-Host "TRANSCRIPT: $TranscriptPath"
    Write-Host "==========================="
}
catch {
    Write-Host ""
    Write-Host "=== PHASE 3 ADMIN ERROR ===" -ForegroundColor Red
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Show-CodeIntegrityServiceBlocks -Since $RunStartedAt | Out-Null
    Write-Host "SERVICE_STATUS: $(Get-ServiceStatusLabel)"
    Write-Host "MajestyGuard_CV_PIPE: $cvPipeStatus"
    Write-Host "MajestyGuard_CredProv_PIPE: $credPipeStatus"
    Write-Host "CP_REGISTERED: $cpRegistered"
    Write-Host "SIGNING_NEEDED: $signingNeeded"
    Write-Host "LOCK_SCREEN_TILE: $lockScreenResult"
    Write-Host "ROLLBACK_CLEAN: $rollbackClean"
    Write-Host ("USER_DATA_PRESERVED: {0}" -f (Test-Path -LiteralPath $EmbeddingPath))
    Write-Host "TRANSCRIPT: $TranscriptPath"
    Write-Host "==========================="
    throw
}
finally {
    Stop-Transcript | Out-Null
}
