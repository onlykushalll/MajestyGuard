from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workflow_test_script_documents_supervised_timing_targets():
    text = (ROOT / "daemon" / "workflow_test.py").read_text(encoding="utf-8")

    assert "workflow_test.py" in text
    assert "MG_IDLE_TIMEOUT" in text
    assert "T1" in text
    assert "T2" in text
    assert "T3" in text
    assert "unlock latency" in text.lower()
    assert "--idle-timeout" in text
    assert "--runs" in text
    assert "MG_ENABLE_LOCK" in text


def test_soft_lock_document_preserves_door_lock_principle():
    text = (ROOT / "docs" / "SOFT_LOCK_VS_WINDOWS_LOCK.md").read_text(encoding="utf-8")

    assert "Music/audio: continues" in text
    assert "Downloads: continues" in text
    assert "Renders/builds: continues" in text
    assert "LockWorkStation" in text
    assert "HOSTILE_LOCK" in text
    assert "Credential Provider" in text
    assert "Smart App Control" in text
