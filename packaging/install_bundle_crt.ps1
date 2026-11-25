[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DaemonDir,
    [string]$VCRedistDir
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath (Join-Path $DaemonDir "MajestyGuardDaemon.exe") -PathType Leaf)) {
    throw "Expected a frozen daemon directory: $DaemonDir"
}
if (-not $VCRedistDir) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $vswhere) {
        $installs = & $vswhere -all -prerelease -products "*" -property installationPath
        $candidates = foreach ($install in $installs) {
            Get-ChildItem -Path (Join-Path $install "VC\Redist\MSVC\*\x64\Microsoft.VC*.CRT") -Directory -ErrorAction SilentlyContinue
        }
        $best = $candidates | Sort-Object { [version]$_.Parent.Parent.Name } -Descending | Select-Object -First 1
        if ($best) { $VCRedistDir = $best.FullName }
    }
}
if (-not $VCRedistDir) {
    throw "Pass -VCRedistDir pointing to the x64 Microsoft.VC*.CRT redistributable folder from Visual Studio Build Tools."
}
# Put a coherent Microsoft runtime next to the daemon exe. Qt's wheel also
# ships older copies; selecting those first makes the MediaPipe DLL fail to initialize.
$runtime = Get-ChildItem -LiteralPath $VCRedistDir -File -Filter "*.dll" |
    Where-Object { $_.Name -match '^(concrt|msvcp|vccorlib|vcruntime)140(_[a-z0-9_]+)?\.dll$' }
foreach ($required in @("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")) {
    if ($required -notin $runtime.Name) { throw "Incomplete x64 CRT: missing $required" }
}
foreach ($dll in $runtime) {
    $sig = Get-AuthenticodeSignature -LiteralPath $dll.FullName
    if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(,|$)') {
        throw "Expected a signed Microsoft redistributable: $($dll.Name)"
    }
}
foreach ($dll in $runtime) {
    Copy-Item -LiteralPath $dll.FullName -Destination (Join-Path $DaemonDir $dll.Name) -Force
}
Write-Host "App-local daemon CRT: $VCRedistDir"
