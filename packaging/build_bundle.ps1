[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$PythonPath,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$ModelDir,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$InsightFaceModelDir,

    [string]$OutputDir,

    [switch]$CreateInstaller,
    [switch]$FinalizeExistingBundle,
    [string]$VCRedistDir,
    [ValidateRange(5, 600)]
    [int]$SmokeTimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    if ($FinalizeExistingBundle) { throw "Pass the explicit -OutputDir of the existing build." }
    $OutputDir = Join-Path $Root "dist"
}
$PythonPath = (Resolve-Path $PythonPath).Path
$ModelDir = (Resolve-Path $ModelDir).Path
$InsightFaceModelDir = (Resolve-Path $InsightFaceModelDir).Path
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
$BundleRoot = Join-Path $OutputDir "MajestyGuard"
$BuildRoot = Join-Path $Root "build\pyinstaller"
. (Join-Path $PSScriptRoot "bundle_crt.ps1")

function Require-File {
    param([string]$Path, [string]$Description)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing ${Description}: $Path"
    }
}

function Invoke-PyInstaller {
    param([string[]]$Arguments)
    $priorPythonPath = $env:PYTHONPATH
    $priorBuildCrt = $env:MG_BUILD_CRT_DIR
    try {
        $env:PYTHONPATH = Join-Path $PSScriptRoot "build_runtime"
        $env:MG_BUILD_CRT_DIR = $VCRedistDir
        & $PythonPath -m PyInstaller @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller failed with exit code $LASTEXITCODE."
        }
    } finally {
        $env:PYTHONPATH = $priorPythonPath
        $env:MG_BUILD_CRT_DIR = $priorBuildCrt
    }
}

if ($CreateInstaller) {
    $compiler = Get-Command ISCC.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $isccPath = if ($compiler) { $compiler.Path } else {
        @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
    if (-not $isccPath) { throw "Install Inno Setup 6 or put ISCC.exe on PATH before building the installer." }
}

Require-File (Join-Path $ModelDir "antispoof_minifasv2.onnx") "anti-spoof model"
Require-File (Join-Path $Root "assets\protection-reel.webp") "protection animation"
Require-File (Join-Path $ModelDir "face_detection_yunet_2023mar.onnx") "face detector model"
Require-File (Join-Path $ModelDir "midas_v21_small_256.onnx") "depth liveness model"
Require-File (Join-Path $InsightFaceModelDir "models\buffalo_l\det_10g.onnx") "InsightFace detector model"
Require-File (Join-Path $InsightFaceModelDir "models\buffalo_l\w600k_r50.onnx") "InsightFace recognition model"
Require-File (Join-Path $InsightFaceModelDir "models\buffalo_l\2d106det.onnx") "InsightFace landmark model"

& $PythonPath -c "import PyInstaller; print(PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is required. Install packaging\requirements-build.txt into the selected Python environment."
}

function New-FrozenBundle {
    if ((Test-Path -LiteralPath $BundleRoot) -and (Get-ChildItem -Force -LiteralPath $BundleRoot | Select-Object -First 1)) {
        throw "Bundle directory is not empty. Pass a fresh -OutputDir to preserve previous builds."
    }
    New-Item -ItemType Directory -Force -Path $BundleRoot, $BuildRoot | Out-Null
    $brandIcon = Join-Path $BuildRoot "majestyguard.ico"
    & $PythonPath (Join-Path $Root "tools\render_brand_icon.py") $brandIcon
    if ($LASTEXITCODE -ne 0) { throw "Could not render the MajestyGuard icon." }

    $commonArguments = @(
        "--noconfirm",
        "--clean",
        "--paths", $Root,
        "--workpath", $BuildRoot,
        "--specpath", $BuildRoot,
        "--distpath", $BundleRoot,
        "--icon", $brandIcon
    )

    # Keep every executable in an onedir bundle. This avoids one-file archive
    # extraction at launch and gives Qt's native DLLs a stable adjacent layout.
    Invoke-PyInstaller ($commonArguments + @(
        "--onedir",
        "--windowed",
        "--name", "MajestyGuard",
        "--hidden-import", "ui.tokens",
        "--add-data", "$Root\assets\protection-reel.webp;assets",
        (Join-Path $Root "app\main.py")
    ))

    Invoke-PyInstaller ($commonArguments + @(
        "--onedir",
        "--windowed",
        "--name", "MajestyGuardDaemon",
        "--paths", (Join-Path $Root "daemon"),
        "--paths", (Join-Path $Root "ui"),
        "--collect-data", "mediapipe",
        "--collect-binaries", "onnxruntime",
        "--exclude-module", "onnx.reference",
        "--exclude-module", "onnxruntime.quantization",
        "--exclude-module", "insightface.app.mask_renderer",
        "--hidden-import", "scipy._cyutility",
        "--hidden-import", "enroll_continuous",
        "--hidden-import", "coverage_enrollment_window",
        "--add-data", "$ModelDir;models",
        "--add-data", "$InsightFaceModelDir;models_insightface",
        "--add-data", "$Root\ui;ui",
        (Join-Path $Root "daemon\main.py")
    ))

    Invoke-PyInstaller ($commonArguments + @(
        "--onedir",
        "--windowed",
        "--name", "MajestyGuardUI",
        (Join-Path $Root "ui\main.py")
    ))
}

$VCRedistDir = @(Get-BundleCrtFiles -VCRedistDir $VCRedistDir)[0].DirectoryName
if (-not $FinalizeExistingBundle) { New-FrozenBundle }

$ControlCenter = Join-Path $BundleRoot "MajestyGuard\MajestyGuard.exe"
$Daemon = Join-Path $BundleRoot "MajestyGuardDaemon\MajestyGuardDaemon.exe"
$Island = Join-Path $BundleRoot "MajestyGuardUI\MajestyGuardUI.exe"
Require-File $ControlCenter "Control Center executable"
Require-File $Daemon "daemon executable"
Require-File $Island "overlay executable"
$resources = Join-Path (Split-Path -Parent $Daemon) "_internal"
& $PythonPath (Join-Path $PSScriptRoot "verify_model_bundle.py") $resources (Join-Path $Root "models\model_hashes.reference.json")
if ($LASTEXITCODE -ne 0) { throw "Packaged model verification failed." }
& (Join-Path $PSScriptRoot "install_bundle_crt.ps1") -DaemonDir (Split-Path -Parent $Daemon) -VCRedistDir $VCRedistDir

& $PythonPath (Join-Path $PSScriptRoot "verify_bundle_smoke.py") `
    --bundle-root $BundleRoot `
    --smoke-state (Join-Path $OutputDir "smoke-state") `
    --enrollment-timeout $SmokeTimeoutSeconds
if ($LASTEXITCODE -ne 0) {
    throw "Packaged self-tests failed. See the check output and isolated smoke-state logs."
}

if ($CreateInstaller) {
    $installerDir = Join-Path $OutputDir "installer"
    $brandIcon = Join-Path $BuildRoot "majestyguard.ico"
    if (-not (Test-Path -LiteralPath $brandIcon -PathType Leaf)) {
        New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
        & $PythonPath (Join-Path $Root "tools\render_brand_icon.py") $brandIcon
        if ($LASTEXITCODE -ne 0) { throw "Could not render the installer icon." }
    }
    & $isccPath "/O$installerDir" "/DBundleRoot=$BundleRoot" "/DBrandIcon=$brandIcon" (Join-Path $PSScriptRoot "MajestyGuard.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
    Require-File (Join-Path $installerDir "MajestyGuard-Setup.exe") "compiled installer"
}

Write-Host "Bundle ready: $BundleRoot"
