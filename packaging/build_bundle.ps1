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

    [switch]$CreateInstaller
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $Root "dist"
}
$PythonPath = (Resolve-Path $PythonPath).Path
$ModelDir = (Resolve-Path $ModelDir).Path
$InsightFaceModelDir = (Resolve-Path $InsightFaceModelDir).Path
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
$BundleRoot = Join-Path $OutputDir "MajestyGuard"
$BuildRoot = Join-Path $Root "build\pyinstaller"

function Require-File {
    param([string]$Path, [string]$Description)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing ${Description}: $Path"
    }
}

function Invoke-PyInstaller {
    param([string[]]$Arguments)
    & $PythonPath -m PyInstaller @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}

Require-File (Join-Path $ModelDir "antispoof_minifasv2.onnx") "anti-spoof model"
Require-File (Join-Path $ModelDir "face_detection_yunet_2023mar.onnx") "face detector model"
Require-File (Join-Path $ModelDir "midas_v21_small_256.onnx") "depth liveness model"
Require-File (Join-Path $InsightFaceModelDir "models\buffalo_l\det_10g.onnx") "InsightFace detector model"
Require-File (Join-Path $InsightFaceModelDir "models\buffalo_l\w600k_r50.onnx") "InsightFace recognition model"

& $PythonPath -c "import PyInstaller; print(PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is required. Install packaging\requirements-build.txt into the selected Python environment."
}

New-Item -ItemType Directory -Force -Path $BundleRoot, $BuildRoot | Out-Null
foreach ($artifact in @(
    (Join-Path $BundleRoot "MajestyGuard.exe"),
    (Join-Path $BundleRoot "MajestyGuardUI.exe"),
    (Join-Path $BundleRoot "MajestyGuardDaemon")
)) {
    if (Test-Path -LiteralPath $artifact) {
        Remove-Item -LiteralPath $artifact -Recurse -Force
    }
}

$commonArguments = @(
    "--noconfirm",
    "--clean",
    "--paths", $Root,
    "--workpath", $BuildRoot,
    "--specpath", $BuildRoot,
    "--distpath", $BundleRoot
)

# The control app and visual client stay small. The daemon gets the models once
# in its onedir bundle, avoiding a copied virtual environment or model extraction
# each time protection is started.
Invoke-PyInstaller ($commonArguments + @(
    "--onefile",
    "--windowed",
    "--name", "MajestyGuard",
    "--collect-all", "PyQt6",
    (Join-Path $Root "app\main.py")
))

Invoke-PyInstaller ($commonArguments + @(
    "--onedir",
    "--windowed",
    "--name", "MajestyGuardDaemon",
    "--paths", (Join-Path $Root "daemon"),
    "--paths", (Join-Path $Root "ui"),
    "--collect-all", "mediapipe",
    "--collect-all", "insightface",
    "--collect-all", "onnxruntime",
    "--collect-all", "PyQt6",
    "--hidden-import", "enroll_v2",
    "--add-data", "$ModelDir;models",
    "--add-data", "$InsightFaceModelDir;models_insightface",
    "--add-data", "$Root\ui;ui",
    (Join-Path $Root "daemon\main.py")
))

Invoke-PyInstaller ($commonArguments + @(
    "--onefile",
    "--windowed",
    "--name", "MajestyGuardUI",
    "--collect-all", "PyQt6",
    (Join-Path $Root "ui\main.py")
))

$ControlCenter = Join-Path $BundleRoot "MajestyGuard.exe"
Require-File $ControlCenter "Control Center executable"
Require-File (Join-Path $BundleRoot "MajestyGuardDaemon\MajestyGuardDaemon.exe") "daemon executable"
Require-File (Join-Path $BundleRoot "MajestyGuardUI.exe") "overlay executable"

# This proves command-line initialization returns before QApplication, camera,
# daemon, or soft-lock code is started.
$previousLockSetting = $env:MG_ENABLE_LOCK
try {
    $env:MG_ENABLE_LOCK = "0"
    & $ControlCenter --help
    if ($LASTEXITCODE -ne 0) {
        throw "Control Center help smoke test failed with exit code $LASTEXITCODE."
    }
}
finally {
    $env:MG_ENABLE_LOCK = $previousLockSetting
}

if ($CreateInstaller) {
    $iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -eq $iscc) {
        $defaultIscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
        if (Test-Path -LiteralPath $defaultIscc -PathType Leaf) {
            $iscc = Get-Item -LiteralPath $defaultIscc
        }
    }
    if ($null -eq $iscc) {
        throw "Inno Setup 6 (ISCC.exe) is required for -CreateInstaller. Build the bundle succeeded; install Inno Setup and rerun with -CreateInstaller."
    }
    & $iscc.Source (Join-Path $PSScriptRoot "MajestyGuard.iss") "/DBundleRoot=$BundleRoot"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
}

Write-Host "Bundle ready: $BundleRoot"
