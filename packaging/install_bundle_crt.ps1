[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DaemonDir,
    [string]$VCRedistDir
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath (Join-Path $DaemonDir "MajestyGuardDaemon.exe") -PathType Leaf)) {
    throw "Expected a frozen daemon directory: $DaemonDir"
}
. (Join-Path $PSScriptRoot "bundle_crt.ps1")
$runtime = @(Get-BundleCrtFiles -VCRedistDir $VCRedistDir)
$VCRedistDir = $runtime[0].DirectoryName
# Keep Qt's private copies coherent too: enrollment loads both Qt and SciPy
# in the same process, unlike the standalone UI.
$qtBin = Join-Path $DaemonDir "_internal\PyQt6\Qt6\bin"
foreach ($dll in $runtime) {
    Copy-Item -LiteralPath $dll.FullName -Destination (Join-Path $DaemonDir $dll.Name) -Force
    foreach ($directory in @((Join-Path $DaemonDir "_internal"), $qtBin)) {
        $existing = Join-Path $directory $dll.Name
        if (Test-Path -LiteralPath $existing -PathType Leaf) {
            Copy-Item -LiteralPath $dll.FullName -Destination $existing -Force
        }
    }
}
Write-Host "App-local daemon CRT: $VCRedistDir"
