from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_installer_is_current_user_only_and_starts_control_center():
    text = (ROOT / "packaging" / "MajestyGuard.iss").read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in text
    assert "{localappdata}\\Programs\\MajestyGuard" in text
    assert "MajestyGuard.exe\"; Parameters: \"--background" in text


def test_installer_does_not_contain_legacy_privileged_integrations():
    text = (ROOT / "packaging" / "MajestyGuard.iss").read_text(encoding="utf-8").lower()

    assert "credential provider" not in text
    assert "safeboot" not in text
    assert "hosts file" not in text
    assert "new-service" not in text


def test_uninstall_preserves_enrollment_unless_explicitly_selected():
    text = (ROOT / "packaging" / "MajestyGuard.iss").read_text(encoding="utf-8")

    assert "Remove enrollment and diagnostic data" in text
    assert "embeddings_v2.npy" in text
    assert "{localappdata}\\MajestyGuard" in text
