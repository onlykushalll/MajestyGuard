from pathlib import Path


def test_signing_roadmap_documents_sac_gap_and_signpath_path():
    text = (Path(__file__).resolve().parents[1] / "docs" / "signing" / "CODE_SIGNING_AND_RELEASES.md").read_text(
        encoding="utf-8"
    )

    assert "Smart App Control" in text
    assert "SignPath" in text
    assert "Credential Provider" in text
    assert "WHCDF" in text
    assert "do not turn smart app control off" in text.lower()
