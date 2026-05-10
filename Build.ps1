# MajestyGuard/Build.ps1
# Builds all projects and stages them for Install.ps1.
# Run from the repo root. Requires Visual Studio 2022 Build Tools.
#
# USAGE:
#   .\Build.ps1              # Debug build
#   .\Build.ps1 -Release     # Release build (required for production)
#   .\Build.ps1 -Clean       # Clean + rebuild

param(
    [switch]$Release,
    [switch]$Clean
)

$Config  = if ($Release) { "Release" } else { "Debug" }
$OutDir  = "$PSScriptRoot\build\$Config"
$SLN     = "$PSScriptRoot\MajestyGuard.sln"

# ── Find MSBuild ──────────────────────────────────────────────────
function Find-MSBuild {
    $candidates = @(
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    # Try vswhere
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $vsPath = & $vswhere -latest -property installationPath
        $msbuild = "$vsPath\MSBuild\Current\Bin\MSBuild.exe"
        if (Test-Path $msbuild) { return $msbuild }
    }
    return $null
}

$msbuild = Find-MSBuild
if (-not $msbuild) {
    Write-Error "MSBuild not found. Install 'Desktop development with C++' and '.NET desktop development' workloads."
    exit 1
}
Write-Host "MSBuild: $msbuild" -ForegroundColor DarkGray

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# ── 1. Build C# projects via dotnet CLI ──────────────────────────
Write-Host ""
Write-Host "[1/3] Building C# projects (Core, Service, Overlay, DpapiHelper)..." -ForegroundColor Cyan

$csharpProjects = @(
    "src\MajestyGuard.Core\MajestyGuard.Core.csproj",
    "src\MajestyGuard.DpapiHelper\MajestyGuard.DpapiHelper.csproj",
    "src\MajestyGuard.Service\MajestyGuard.Service.csproj",
    "src\MajestyGuard.Overlay\MajestyGuard.Overlay.csproj"
)

foreach ($proj in $csharpProjects) {
    $projPath = "$PSScriptRoot\$proj"
    $projName = [System.IO.Path]::GetFileNameWithoutExtension($proj)
    Write-Host "  Building $projName..." -ForegroundColor Gray

    $cleanArg = if ($Clean) { "--no-incremental" } else { "" }
    $result = & dotnet publish $projPath `
        --configuration $Config `
        --runtime win-x64 `
        --self-contained false `
        --output "$OutDir\$projName" `
        --nologo `
        --verbosity quiet 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Build failed for $projName:`n$result"
        exit 1
    }
    Write-Host "    ✓ $projName" -ForegroundColor Green
}

# ── 2. Build C++ Credential Provider ─────────────────────────────
Write-Host ""
Write-Host "[2/3] Building C++ Credential Provider DLL..." -ForegroundColor Cyan

$vcxprojPath = "$PSScriptRoot\src\MajestyGuard.CredentialProvider\MajestyGuard.CredentialProvider.vcxproj"

if (Test-Path $vcxprojPath) {
    if ($Clean) {
        & $msbuild $vcxprojPath /t:Clean /p:Configuration=$Config /p:Platform=x64 /nologo /verbosity:quiet
    }

    & $msbuild $vcxprojPath `
        /p:Configuration=$Config `
        /p:Platform=x64 `
        /p:OutDir="$OutDir\CredentialProvider\" `
        /nologo /verbosity:quiet

    if ($LASTEXITCODE -ne 0) {
        Write-Error "C++ build failed. Ensure 'Desktop development with C++' workload is installed."
        exit 1
    }
    Write-Host "    ✓ CredentialProvider.dll" -ForegroundColor Green
} else {
    Write-Warning "CredentialProvider.vcxproj not found — skipping C++ build."
}

# ── 3. Stage output directory (mirror Install.ps1 expectations) ──
Write-Host ""
Write-Host "[3/3] Staging deployment package..." -ForegroundColor Cyan

$stageDir = "$PSScriptRoot\build\staged"
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null

# Copy each project output
$copies = @{
    "$OutDir\MajestyGuard.Service\*"            = $stageDir
    "$OutDir\MajestyGuard.Overlay\*"            = $stageDir
    "$OutDir\MajestyGuard.DpapiHelper\*"        = $stageDir
    "$OutDir\CredentialProvider\*.dll"          = $stageDir
    "$PSScriptRoot\src\MajestyGuard.CVEngine\*" = "$stageDir\CVEngine"
    "$PSScriptRoot\Install.ps1"                 = $stageDir
}

foreach ($src in $copies.Keys) {
    $dst = $copies[$src]
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item $src $dst -Recurse -Force -ErrorAction SilentlyContinue
}

# ── Summary ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Build complete: $stageDir" -ForegroundColor Green
Write-Host ""
Write-Host "  To install (as Administrator):" -ForegroundColor White
Write-Host "    cd '$stageDir'" -ForegroundColor DarkGray
Write-Host "    .\Install.ps1" -ForegroundColor DarkGray
Write-Host ""

# Show DLL signing status
$dll = "$stageDir\MajestyGuard.CredentialProvider.dll"
if (Test-Path $dll) {
    $sig = Get-AuthenticodeSignature $dll
    Write-Host "  CP DLL signing: $($sig.Status)" -ForegroundColor $(
        if ($sig.Status -eq "Valid") { "Green" } else { "Yellow" }
    )
    if ($sig.Status -ne "Valid") {
        Write-Host "  ⚠ Run Install.ps1 — it will self-sign the DLL and enable test signing." -ForegroundColor Yellow
    }
}
