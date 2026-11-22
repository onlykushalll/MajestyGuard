import json

import audit_log
from audit_log import GENESIS, AuditLog, verify_chain


def _lines(path):
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_records_chain_and_verify(tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    for i in range(5):
        assert log.record("state", frm="ACTIVE", to="SOFT_LOCK", n=i)
    ok, count, msg = verify_chain(tmp_path / "audit.log", expected_first_prev=GENESIS)
    assert ok and count == 5, msg


def test_editing_a_record_is_detected(tmp_path):
    path = tmp_path / "audit.log"
    log = AuditLog(path)
    for i in range(4):
        log.record("unlock", n=i)
    lines = _lines(path)
    rec = json.loads(lines[1])
    rec["event"] = "nothing_happened"
    lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, _, msg = verify_chain(path)
    assert not ok and "line 2" in msg


def test_deleting_or_reordering_records_is_detected(tmp_path):
    path = tmp_path / "audit.log"
    log = AuditLog(path)
    for i in range(5):
        log.record("lock", n=i)
    lines = _lines(path)
    path.write_text("\n".join(lines[:2] + lines[3:]) + "\n", encoding="utf-8")
    ok, _, msg = verify_chain(path)
    assert not ok and "line 3" in msg

    path.write_text("\n".join([lines[0], lines[2], lines[1], *lines[3:]]) + "\n", encoding="utf-8")
    assert not verify_chain(path)[0]


def test_chain_resumes_after_restart(tmp_path):
    path = tmp_path / "audit.log"
    AuditLog(path).record("start")
    AuditLog(path).record("lock")
    ok, count, _ = verify_chain(path, expected_first_prev=GENESIS)
    assert ok and count == 2


def test_rotation_keeps_the_chain_linked(tmp_path):
    path = tmp_path / "audit.log"
    log = AuditLog(path, max_bytes=600)
    for i in range(20):
        log.record("event", n=i, pad="x" * 30)
    backup = tmp_path / "audit.log.1"
    assert backup.exists()
    ok_old, _, msg = verify_chain(backup, expected_first_prev=None)
    ok_new, _, msg2 = verify_chain(path)
    assert ok_old, msg
    assert ok_new, msg2
    last_old = json.loads(_lines(backup)[-1])["hash"]
    assert json.loads(_lines(path)[0])["prev"] == last_old


def test_details_are_bounded_and_text_only(tmp_path):
    path = tmp_path / "audit.log"
    AuditLog(path).record("x" * 200, reason="r" * 500, score=0.5)
    rec = json.loads(_lines(path)[0])
    assert len(rec["event"]) <= 48 and len(rec["detail"]["reason"]) <= 120


def test_disabled_log_writes_nothing_and_never_raises(tmp_path):
    log = AuditLog(tmp_path / "audit.log", enabled=False)
    assert log.record("lock") is False
    assert not (tmp_path / "audit.log").exists()
    bad = AuditLog(tmp_path / "nope" / ".." / "\0bad")
    assert bad.record("lock") in (True, False)  # must not raise


def test_module_api_respects_the_env_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("MG_AUDIT_LOG", "0")
    monkeypatch.setattr(audit_log, "_default", None)
    assert audit_log.record("lock") is False
    monkeypatch.setattr(audit_log, "_default", None)
