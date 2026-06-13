from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "run_phase3_admin.ps1"
DIAGNOSE = ROOT / "diagnose_localsystem_cv.ps1"


def test_phase3_runner_surfaces_code_integrity_service_blocks():
    runner_text = RUNNER.read_text(encoding="utf-8")

    assert "Get-CodeIntegrityServiceBlockEvents" in runner_text
    assert "Microsoft-Windows-CodeIntegrity/Operational" in runner_text
    assert "3033" in runner_text
    assert "3077" in runner_text
    assert "0x800711C7" in runner_text
    assert "SERVICE_BLOCKED_BY_CODE_INTEGRITY" in runner_text
    assert "CODE_INTEGRITY_POLICY_ID" in runner_text


def test_phase3_runner_allows_unsigned_service_exe_for_dotnet_host_probe():
    runner_text = RUNNER.read_text(encoding="utf-8")

    assert "DOTNET_HOST_UNSIGNED_SERVICE_EXE_ALLOWED" in runner_text
    assert "DOTNET_HOST_DEV_SIGNING_CONFLICT" in runner_text
    assert "$serviceSig.Status -ne \"Valid\" -and -not $EnableDevSigningIfNeeded -and -not $UseDotnetServiceHost" in runner_text
    assert "SIGNING_NEEDED: NotRequiredForDotnetHost" in runner_text


def test_localsystem_diagnostic_reports_service_code_integrity_context():
    diagnose_text = DIAGNOSE.read_text(encoding="utf-8")

    assert "MajestyGuard.Service.Host\\MajestyGuard.Service.dll" in diagnose_text
    assert "Microsoft-Windows-CodeIntegrity/Operational" in diagnose_text
    assert "CODE_INTEGRITY_SERVICE_BLOCK" in diagnose_text
    assert "Get-AuthenticodeSignature" in diagnose_text
    assert "3033" in diagnose_text
    assert "3077" in diagnose_text
