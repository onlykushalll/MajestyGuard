from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_install_guide_discloses_unsigned_build_and_user_data_behavior():
    text = (ROOT / "docs" / "guides" / "INSTALL_AND_UPDATE.md").read_text(encoding="utf-8")

    assert "unsigned" in text.lower()
    assert "preserves enrollment" in text.lower()
    assert "Smart App Control" in text


def test_install_guide_lists_manual_security_release_gates():
    text = (ROOT / "docs" / "guides" / "INSTALL_AND_UPDATE.md").read_text(encoding="utf-8")

    assert "stranger" in text.lower()
    assert "spoof" in text.lower()
    assert "video" in text.lower()


def test_readme_describes_the_active_user_space_architecture():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "current-user" in text.lower()
    assert "soft-lock" in text.lower()
    assert "Windows sign-in" in text
