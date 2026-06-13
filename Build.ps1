# MajestyGuard/Build.ps1
# Builds all projects and stages a clean local-dev package for Install.ps1.
# Run from the repo root. Requires Visual Studio Build Tools for the C++ provider.
#
# USAGE:
#   .\Build.ps1              # Debug build
#   .\Build.ps1 -Release     # Release build
#   .\Build.ps1 -Clean       # Clean + rebuild

param(
    [switch]$Release,
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath $PSScriptRoot).ProviderPath
$Config = if ($Release) { "Release" } else { "Debug" }
$BuildRoot = Join-Path $RepoRoot "build"
$OutDir = Join-Path $BuildRoot $Config
$StageDir = Join-Path $BuildRoot "staged"

function Find-MSBuild {
    $candidates = @(
        "${env:ProgramFiles}\Microsoft Visual Studio\18\Insiders\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\18\Preview\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }

    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $vswhere) {
        $vsPath = & $vswhere -latest -prerelease -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if (-not $vsPath) {
            $vsPath = & $vswhere -latest -prerelease -products * -property installationPath
        }

        if ($vsPath) {
            $msbuild = Join-Path $vsPath "MSBuild\Current\Bin\MSBuild.exe"
            if (Test-Path -LiteralPath $msbuild) {
                return $msbuild
            }
        }
    }

    return $null
}

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )

    $resolvedParent = (Resolve-Path -LiteralPath $Parent).ProviderPath.TrimEnd('\')
    $resolvedChild = if (Test-Path -LiteralPath $Child) {
        (Resolve-Path -LiteralPath $Child).ProviderPath
    } else {
        [System.IO.Path]::GetFullPath($Child)
    }

    if (-not $resolvedChild.StartsWith($resolvedParent + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside build root: $resolvedChild"
    }
}

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludeNames = @(),
        [string[]]$ExcludeFiles = @()
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Source directory not found: $Source"
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    $sourceRoot = (Resolve-Path -LiteralPath $Source).ProviderPath
    $items = Get-ChildItem -LiteralPath $sourceRoot -Force -Recurse
    foreach ($item in $items) {
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
            $targetParent = Split-Path -Parent $target
            New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
            Copy-Item -LiteralPath $item.FullName -Destination $target -Force
        }
    }
}

function Copy-RequiredItems {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePattern,
        [Parameter(Mandatory = $true)][string]$Destination,
        [switch]$Optional
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $matches = Get-ChildItem -Path $SourcePattern -Force -ErrorAction SilentlyContinue
    if (-not $matches) {
        if ($Optional) {
            Write-Warning "Optional artifact missing: $SourcePattern"
            return
        }

        throw "Required artifact missing: $SourcePattern"
    }

    foreach ($match in $matches) {
        Copy-Item -LiteralPath $match.FullName -Destination $Destination -Recurse -Force
    }
}

function Clear-EfsEncryption {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $encryptedFiles = @(
        Get-ChildItem -LiteralPath $Path -File -Force -Recurse -ErrorAction SilentlyContinue |
            Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::Encrypted) -ne 0 }
    )

    foreach ($file in $encryptedFiles) {
        [System.IO.File]::Decrypt($file.FullName)
        Write-Host "    Decrypted EFS staged file: $($file.FullName)" -ForegroundColor Gray
    }
}

function Test-StagePackage {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [bool]$ExpectCredentialProvider
    )

    $requiredFiles = @(
        "Install.ps1",
        "Uninstall.ps1",
        "MajestyGuard.Core.dll",
        "MajestyGuard.Service.exe",
        "MajestyGuard.Service.Host\MajestyGuard.Service.dll",
        "MajestyGuard.Overlay.exe",
        "MajestyGuard.DpapiHelper.exe",
        "CVEngine\cv_server.py",
        "CVEngine\face_engine.py",
        "CVEngine\liveness_detector.py",
        "CVEngine\requirements.txt"
    )

    if ($ExpectCredentialProvider) {
        $requiredFiles += "MajestyGuard.CredentialProvider.dll"
    }

    foreach ($file in $requiredFiles) {
        $fullPath = Join-Path $Path $file
        if (-not (Test-Path -LiteralPath $fullPath)) {
            throw "Staged package is missing required file: $file"
        }
    }

    $forbiddenPaths = @(
        "python",
        "test_overlay",
        "CVEngine\.venv",
        "CVEngine\.pytest_cache",
        "CVEngine\__pycache__",
        "CVEngine\models"
    )

    foreach ($item in $forbiddenPaths) {
        $fullPath = Join-Path $Path $item
        if (Test-Path -LiteralPath $fullPath) {
            throw "Staged package contains stale or dev-only content: $item"
        }
    }
}

New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$msbuild = Find-MSBuild
if (-not $msbuild) {
    Write-Warning "MSBuild not found. The C++ Credential Provider cannot be rebuilt, but C# projects will still build."
}
Write-Host "MSBuild: $msbuild" -ForegroundColor DarkGray

Write-Host ""
Write-Host "[1/4] Building C# projects (Core, Service, Overlay, DpapiHelper)..." -ForegroundColor Cyan

$csharpProjects = @(
    "src\MajestyGuard.Core\MajestyGuard.Core.csproj",
    "src\MajestyGuard.DpapiHelper\MajestyGuard.DpapiHelper.csproj",
    "src\MajestyGuard.Service\MajestyGuard.Service.csproj",
    "src\MajestyGuard.Overlay\MajestyGuard.Overlay.csproj"
)

foreach ($project in $csharpProjects) {
    $projectPath = Join-Path $RepoRoot $project
    $projectName = [System.IO.Path]::GetFileNameWithoutExtension($project)
    $projectOutDir = Join-Path $OutDir $projectName
    $selfContained = if (@("MajestyGuard.Service", "MajestyGuard.Overlay") -contains $projectName) { "true" } else { "false" }

    Write-Host "  Building $projectName..." -ForegroundColor Gray
    $result = & dotnet publish $projectPath `
        --configuration $Config `
        --runtime win-x64 `
        --self-contained $selfContained `
        --output $projectOutDir `
        --nologo `
        --verbosity quiet 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Build failed for ${projectName}:`n${result}"
        exit 1
    }

    Write-Host "    [OK] $projectName" -ForegroundColor Green
}

$serviceProjectPath = Join-Path $RepoRoot "src\MajestyGuard.Service\MajestyGuard.Service.csproj"
$serviceHostOutDir = Join-Path $OutDir "MajestyGuard.Service.Host"
Write-Host "  Building MajestyGuard.Service.Host..." -ForegroundColor Gray
$serviceHostResult = & dotnet publish $serviceProjectPath `
    --configuration $Config `
    --runtime win-x64 `
    --self-contained false `
    --output $serviceHostOutDir `
    /p:UseAppHost=false `
    /p:PublishSingleFile=false `
    --nologo `
    --verbosity quiet 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed for MajestyGuard.Service.Host:`n${serviceHostResult}"
    exit 1
}

Write-Host "    [OK] MajestyGuard.Service.Host" -ForegroundColor Green

Write-Host ""
Write-Host "[2/4] Building C++ Credential Provider DLL..." -ForegroundColor Cyan

$vcxprojPath = Join-Path $RepoRoot "src\MajestyGuard.CredentialProvider\MajestyGuard.CredentialProvider.vcxproj"
$builtCredentialProvider = $false

if (Test-Path -LiteralPath $vcxprojPath) {
    if (-not $msbuild) {
        Write-Warning "Skipping C++ Credential Provider build (MSBuild not found)."
    } else {
        if ($Clean) {
            & $msbuild $vcxprojPath /t:Clean /p:Configuration=$Config /p:Platform=x64 /nologo /verbosity:quiet
        }

        $cpOutDir = Join-Path $OutDir "CredentialProvider"
        & $msbuild $vcxprojPath `
            /p:Configuration=$Config `
            /p:Platform=x64 `
            /p:OutDir="$cpOutDir\" `
            /nologo /verbosity:quiet

        if ($LASTEXITCODE -ne 0) {
            Write-Error "C++ build failed. Ensure 'Desktop development with C++' workload is installed."
            exit 1
        }

        $builtCredentialProvider = Test-Path -LiteralPath (Join-Path $cpOutDir "MajestyGuard.CredentialProvider.dll")
        Write-Host "    [OK] CredentialProvider.dll" -ForegroundColor Green
    }
} else {
    Write-Warning "CredentialProvider.vcxproj not found; skipping C++ build."
}

Write-Host ""
Write-Host "[3/4] Staging clean deployment package..." -ForegroundColor Cyan

Assert-ChildPath -Parent $BuildRoot -Child $StageDir
if (Test-Path -LiteralPath $StageDir) {
    $resolvedStageDir = (Resolve-Path -LiteralPath $StageDir).ProviderPath
    Assert-ChildPath -Parent $BuildRoot -Child $resolvedStageDir
    Remove-Item -LiteralPath $resolvedStageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

Copy-RequiredItems -SourcePattern (Join-Path $OutDir "MajestyGuard.Service\*") -Destination $StageDir
Copy-RequiredItems -SourcePattern (Join-Path $OutDir "MajestyGuard.Service.Host\*") -Destination (Join-Path $StageDir "MajestyGuard.Service.Host")
Copy-RequiredItems -SourcePattern (Join-Path $OutDir "MajestyGuard.Overlay\*") -Destination $StageDir
Copy-RequiredItems -SourcePattern (Join-Path $OutDir "MajestyGuard.DpapiHelper\*") -Destination $StageDir
Copy-RequiredItems -SourcePattern (Join-Path $OutDir "MajestyGuard.Core\*") -Destination $StageDir
Copy-RequiredItems -SourcePattern (Join-Path $OutDir "CredentialProvider\*.dll") -Destination $StageDir -Optional:(!$builtCredentialProvider)
Copy-RequiredItems -SourcePattern (Join-Path $RepoRoot "Install.ps1") -Destination $StageDir
Copy-RequiredItems -SourcePattern (Join-Path $RepoRoot "Uninstall.ps1") -Destination $StageDir

$cvSource = Join-Path $RepoRoot "src\MajestyGuard.CVEngine"
$cvDestination = Join-Path $StageDir "CVEngine"
Copy-DirectoryContents `
    -Source $cvSource `
    -Destination $cvDestination `
    -ExcludeNames @(".venv", ".pytest_cache", "__pycache__", "models") `
    -ExcludeFiles @("*.pyc", "*.pyo", ".coverage", "test_*.py")
Clear-EfsEncryption -Path $cvDestination

Write-Host ""
Write-Host "[4/4] Verifying staged package..." -ForegroundColor Cyan
Test-StagePackage -Path $StageDir -ExpectCredentialProvider:$builtCredentialProvider

Write-Host ""
Write-Host "  Build complete: $StageDir" -ForegroundColor Green
Write-Host ""
Write-Host "  No-machine-state copy check:" -ForegroundColor White
Write-Host "    cd '$StageDir'" -ForegroundColor DarkGray
Write-Host "    .\Install.ps1 -AcknowledgeLoginRisk -CopyOnly -InstallDir `"`$env:TEMP\MajestyGuard-copycheck`"" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Machine-level install command (as Administrator, after copy-only check):" -ForegroundColor White
Write-Host "    cd '$StageDir'" -ForegroundColor DarkGray
Write-Host "    .\Install.ps1 -AcknowledgeLoginRisk -SkipPythonSetup -SkipModelDownload" -ForegroundColor DarkGray
Write-Host "    Add -InstallService only after rollback and dev-mode testing are verified." -ForegroundColor DarkGray
Write-Host "    Add -EnableCredentialProvider only as a final login-screen step." -ForegroundColor DarkGray
Write-Host ""

$dll = Join-Path $StageDir "MajestyGuard.CredentialProvider.dll"
if (Test-Path -LiteralPath $dll) {
    $signature = Get-AuthenticodeSignature $dll
    $signatureColor = if ($signature.Status -eq "Valid") { "Green" } else { "Yellow" }
    Write-Host "  CP DLL signing: $($signature.Status)" -ForegroundColor $signatureColor
    if ($signature.Status -ne "Valid") {
        Write-Host "  NOTE: test signing is opt-in; use -EnableTestSigning only for planned CP secure-desktop testing." -ForegroundColor Yellow
    }
}
