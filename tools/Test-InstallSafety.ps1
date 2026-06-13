param(
    [string[]]$InstallerPaths = @(
        (Join-Path $PSScriptRoot "..\Install.ps1"),
        (Join-Path $PSScriptRoot "..\build\staged\Install.ps1")
    ),
    [string]$BuildScriptPath = (Join-Path $PSScriptRoot "..\Build.ps1")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$machineStateTokens = @(
    "Start-Process regsvr32",
    "sc.exe",
    "bcdedit",
    "New-SelfSignedCertificate",
    "Set-AuthenticodeSignature",
    "New-NetFirewallRule",
    "Register-ScheduledTask",
    "Set-ItemProperty",
    "Set-Acl",
    "Start-Service",
    "New-Item -Path `"HKLM:"
)

function Test-TopLevelTokenBeforeCopyOnly {
    param(
        [Parameter(Mandatory=$true)][AllowEmptyString()][string[]]$Lines,
        [Parameter(Mandatory=$true)][int]$CopyOnlyLineIndex,
        [Parameter(Mandatory=$true)][string]$Token
    )

    $functionDepth = 0
    for ($i = 0; $i -lt $CopyOnlyLineIndex; $i++) {
        $line = $Lines[$i]
        $trimmed = $line.TrimStart()

        if ($functionDepth -eq 0 -and $trimmed -match '^function\s+[A-Za-z0-9_-]+\s*\{') {
            $functionDepth = 1
            continue
        }

        if ($functionDepth -gt 0) {
            $functionDepth += ([regex]::Matches($line, '\{')).Count
            $functionDepth -= ([regex]::Matches($line, '\}')).Count
            if ($functionDepth -lt 0) { $functionDepth = 0 }
            continue
        }

        if ($line.Contains($Token)) {
            return $true
        }
    }

    return $false
}

foreach ($path in $InstallerPaths) {
    $resolved = (Resolve-Path -LiteralPath $path).ProviderPath
    $text = Get-Content -Raw -LiteralPath $resolved
    $lines = Get-Content -LiteralPath $resolved

    if ($text -notmatch '\[switch\]\$CopyOnly') {
        throw "$resolved is missing -CopyOnly."
    }
    if ($text -notmatch '\[string\]\$InstallDir') {
        throw "$resolved is missing -InstallDir."
    }
    if ($text -notmatch 'if \(\$CopyOnly\)') {
        throw "$resolved is missing the CopyOnly early-exit block."
    }

    $copyOnlyIndex = $text.IndexOf("if (`$CopyOnly)", [System.StringComparison]::Ordinal)
    if ($copyOnlyIndex -lt 0) {
        throw "$resolved CopyOnly block could not be located."
    }

    $copyOnlyLineIndex = 0
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Contains('if ($CopyOnly)')) {
            $copyOnlyLineIndex = $i
            break
        }
    }

    foreach ($token in $machineStateTokens) {
        if (Test-TopLevelTokenBeforeCopyOnly -Lines $lines -CopyOnlyLineIndex $copyOnlyLineIndex -Token $token) {
            throw "$resolved runs machine-state token before CopyOnly exit: $token"
        }
    }
}

$buildScript = (Resolve-Path -LiteralPath $BuildScriptPath).ProviderPath
$buildText = Get-Content -Raw -LiteralPath $buildScript
if ($buildText -notmatch '-CopyOnly') {
    throw "$buildScript must advertise the copy-only no-machine-state command."
}
if ($buildText -match 'Safe local-dev staging command' -and $buildText -notmatch 'No-machine-state copy check') {
    throw "$buildScript uses the old safe-staging wording without a copy-only warning."
}

Write-Host "Install safety checks passed for $($InstallerPaths.Count) installer script(s)."
