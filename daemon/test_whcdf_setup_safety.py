from pathlib import Path


def test_whcdf_setup_does_not_publish_env_key_by_default():
    script = (Path(__file__).resolve().parents[1] / "setup" / "setup_whcdf.ps1").read_text(encoding="utf-8")

    assert "[switch]$AllowInsecureEnvKey" in script
    assert "MAJESTYGUARD_MUTUAL_AUTH_KEY" in script
    assert "paste into run_daemon.bat" not in script
    assert "Write-Host \"  $keyHex\"" not in script

    env_write = script.index("[System.Environment]::SetEnvironmentVariable")
    guard = script.rfind("if ($AllowInsecureEnvKey)", 0, env_write)
    assert guard != -1


def test_companion_registration_comment_does_not_claim_default_env_key_handoff():
    source = (
        Path(__file__).resolve().parents[1]
        / "companion"
        / "Services"
        / "CompanionRegistration.cs"
    ).read_text(encoding="utf-8")

    assert "set by setup_whcdf.ps1" not in source
