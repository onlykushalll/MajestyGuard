import io
from pathlib import Path
from types import SimpleNamespace

import workflow_test


ROOT = Path(__file__).resolve().parents[2]


def test_dry_run_keeps_explicit_time_limit_and_warns_against_tab(capsys, monkeypatch):
    def unexpected_start(_env):
        raise AssertionError("dry run must not start the daemon")

    monkeypatch.setattr(workflow_test, "_start_daemon", unexpected_start)
    args = SimpleNamespace(idle_timeout=30, max_seconds=35, runs=3, dry_run=True)

    assert workflow_test.run(args) == 0
    output = capsys.readouterr().out
    assert '"MG_MAX_SECONDS": "35"' in output
    assert "Do not press Tab" in output


def test_workflow_reports_daemon_failure_and_drains_last_output(capsys, monkeypatch):
    child = SimpleNamespace(
        stdout=io.StringIO("last daemon line\n"),
        poll=lambda: 7,
        wait=lambda: 7,
    )
    monkeypatch.setattr(workflow_test, "_start_daemon", lambda _env: child)
    args = SimpleNamespace(idle_timeout=30, max_seconds=35, runs=1, dry_run=False)

    assert workflow_test.run(args) == 7
    assert "last daemon line" in capsys.readouterr().out


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
    assert "MG_OVERLAY_WATCHDOG" in text
    assert "_start_daemon" in text
    assert "str(UI)" not in text


def test_soft_lock_document_preserves_door_lock_principle():
    text = (ROOT / "docs" / "guides" / "LOCK_SCREEN_INTEGRATION.md").read_text(encoding="utf-8")

    assert "Music/audio: continues" in text
    assert "Downloads: continues" in text
    assert "Renders/builds: continues" in text
    assert "LockWorkStation" in text
    assert "HOSTILE_LOCK" in text
    assert "Credential Provider" in text
    assert "Smart App Control" in text
