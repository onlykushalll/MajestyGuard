import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "daemon"))

import audit_log  # noqa: E402

from app import activity  # noqa: E402


def _log(tmp_path, events):
    log = audit_log.AuditLog(tmp_path / "audit.log")
    for name, detail in events:
        assert log.record(name, **detail)
    return tmp_path / "audit.log"


def test_encrypted_gallery_chip_only_caches_the_summary(tmp_path, monkeypatch):
    from types import SimpleNamespace

    path = tmp_path / "embeddings_v2_meta.json"
    path.write_bytes(b"encrypted")
    reads = []

    def unprotect(data, *_args):
        reads.append(data)
        return "metadata", b'{"profile":"continuous-coverage","selected_count":32,"sensitive":"omit"}'

    monkeypatch.setitem(sys.modules, "win32crypt", SimpleNamespace(CryptUnprotectData=unprotect))
    assert activity.gallery_summary(path) == "32 templates | Coverage scan"
    assert activity.gallery_summary(path) == "32 templates | Coverage scan"
    assert reads == [b"encrypted"]
    path.write_bytes(b"new encrypted metadata")
    assert activity.gallery_summary(path) == "32 templates | Coverage scan"
    assert len(reads) == 2
    assert list(tmp_path.iterdir()) == [path]


def test_digest_matches_the_daemon_so_verification_agrees():
    body = {"seq": 1, "ts": 1.5, "event": "x", "detail": {"a": 1}}
    assert activity._digest("0" * 64, body) == audit_log._digest("0" * 64, body)


def test_profile_coverage_uses_only_kept_and_known_views(tmp_path, monkeypatch):
    path = tmp_path / "embeddings_v2_meta.json"
    path.touch()
    monkeypatch.setattr(activity, "load_enrollment_metadata", lambda _: {
        "profile": "continuous-coverage",
        "selected_count": 4,
        "bucket_counts": {"up": 12},
        "selected_samples": [
            {"angle": "center"}, {"angle": "right_wide"}, {"angle": "near"},
            {"angle": "not a recognized angle"}, {"angle": ["bad"]},
        ],
    })

    summary, coverage = activity.profile_overview(path)

    assert summary == "4 templates | Coverage scan"
    assert coverage == ("Head position: straight ahead, wide right", "Distance: near",
                        "Light: not recorded separately")


def test_intact_log_verifies_and_tampering_is_reported(tmp_path):
    path = _log(tmp_path, [("daemon_start", {"pid": 1}), ("soft_lock", {"reason": "idle"}), ("unlock_face", {})])
    ok, count, msg = activity.verify_integrity(path)
    assert (ok, count) == (True, 3) and "consistent" in msg
    lines = path.read_text().splitlines()
    lines[1] = lines[1].replace('"idle"', '"manual"')
    path.write_text("\n".join(lines) + "\n")
    ok, _, msg = activity.verify_integrity(path)
    assert ok is False and "altered" in msg


def test_missing_log_is_an_empty_intact_history(tmp_path):
    assert activity.verify_integrity(tmp_path / "nope.log")[:2] == (True, 0)
    assert activity.read_recent(tmp_path / "nope.log") == []


def test_nonfinite_or_invalid_audit_fields_do_not_crash_readers(tmp_path):
    log = tmp_path / "audit.log"
    log.write_text(json.dumps({"prev": [], "hash": "", "event": "unlock_face", "ts": float("nan")}))
    assert not activity.verify_integrity(log)[0]
    assert not audit_log.verify_chain(log)[0]
    assert activity.read_recent(log) == []
    log.write_text(json.dumps({"prev": "0"*64, "hash": "", "event": "verify_failed", "ts": time.time(),
                               "detail": {"cooldown_s": float("inf")}}))
    assert not activity.verify_integrity(log)[0]
    assert not audit_log.verify_chain(log)[0]
    assert activity.read_recent(log)[0].detail == ""


def test_corrupt_camera_snapshot_cannot_report_active_camera(tmp_path):
    path = tmp_path / "camera_health.json"
    for extra in ({"updated_at": float("inf")}, {"next_retry_in_s": float("nan")}, {"events": 42}):
        path.write_text(json.dumps({"status": "healthy", "capture_open": True, **extra}))
        assert activity.read_camera_health(path) is None


def test_recent_activity_is_newest_first_friendly_and_skips_internal_noise(tmp_path):
    path = _log(tmp_path, [
        ("daemon_start", {"pid": 4}),
        ("state", {"frm": "IDLE", "to": "SCANNING"}),
        ("soft_lock", {"reason": "idle"}),
        ("verify_failed", {"streak": 2, "cooldown_s": 5}),
        ("camera_fault", {"fault": "privacy_blocked"}),
    ])
    items = activity.read_recent(path)
    assert [i.title for i in items] == ["Camera problem", "Face not recognized", "Screen locked", "Protection started"]
    assert items[0].detail == "blocked by Windows privacy settings"
    assert items[1].detail == "2 in a row, retry in 5 s"
    assert items[2].detail == "no activity"
    assert all(i.title != "State" for i in items)


def test_repeated_failures_turn_red(tmp_path):
    path = _log(tmp_path, [("verify_failed", {"streak": 5, "cooldown_s": 30})])
    assert activity.read_recent(path)[0].tone == "bad"


def test_limit_and_garbage_lines_are_handled(tmp_path):
    path = _log(tmp_path, [("unlock_face", {})] * 6)
    with open(path, "a") as f:
        f.write("not json\n")
    assert len(activity.read_recent(path, limit=3)) == 3
    assert len(activity.read_recent(path, limit=50)) == 6


def test_ago_wording():
    now = 10_000.0
    assert activity.ago(now - 5, now) == "just now"
    assert activity.ago(now - 300, now) == "5 min ago"
    assert activity.ago(now - 7300, now) == "2 h ago"


def test_camera_health_snapshot_is_parsed_and_bad_files_ignored(tmp_path):
    p = tmp_path / "camera_health.json"
    assert activity.read_camera_health(p) is None
    p.write_text(json.dumps({"status": "degraded", "headline": "Camera view is dark", "hint": "Uncover the lens",
                             "last_backend": "dshow", "recoveries": 2, "open_failures": 0,
                             "next_retry_in_s": 4.5, "updated_at": time.time() - 3, "events": ["a", "b"]}))
    info = activity.read_camera_health(p)
    assert info.status == "degraded" and info.backend == "dshow" and info.recoveries == 2
    assert info.events == ("a", "b") and info.age_s < 10
    p.write_text("[1,2]")
    assert activity.read_camera_health(p) is None
    p.write_text("{broken")
    assert activity.read_camera_health(p) is None


def test_enrollment_events_have_plain_titles(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "daemon"))
    import audit_log

    log = audit_log.AuditLog(tmp_path / "audit.log")
    log.record("enroll_rejected", reason="different_person")
    log.record("gallery_restored", backup="x")
    items = activity.read_recent(tmp_path / "audit.log", 5)
    assert [i.title for i in items] == ["Face profile restored from backup", "Re-enrollment refused"]
    assert items[1].tone == "bad" and "did not match" in items[1].detail
