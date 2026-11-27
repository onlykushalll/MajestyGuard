function Get-BundleCrtFiles {
    param([string]$VCRedistDir)
    if (-not $VCRedistDir) {
        $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
        if (Test-Path -LiteralPath $vswhere) {
            $candidates = foreach ($install in (& $vswhere -all -prerelease -products "*" -property installationPath)) {
                Get-ChildItem -Path (Join-Path $install "VC\Redist\MSVC\*\x64\Microsoft.VC*.CRT") -Directory -ErrorAction SilentlyContinue
            }
            $best = $candidates |
                Where-Object { $_.Parent.Parent.Name -match '^\d+(\.\d+){1,3}$' } |
                Sort-Object { [version]$_.Parent.Parent.Name } -Descending | Select-Object -First 1
            if ($best) { $VCRedistDir = $best.FullName }
        }
    }
    if (-not $VCRedistDir) {
        throw "Pass -VCRedistDir pointing to the x64 Microsoft.VC*.CRT redistributable folder."
    }
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
    return $runtime
}
