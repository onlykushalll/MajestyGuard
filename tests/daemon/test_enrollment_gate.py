import io
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "daemon"))
import enrollment_gate as gate  # noqa: E402

ident = lambda b: b  # noqa: E731  (stand-in for DPAPI)


def _unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _person(seed, n=8, spread=0.35):
    rng = np.random.default_rng(seed)
    base = rng.normal(size=512)
    return _unit(base + spread * rng.normal(size=(n, 512)) * np.linalg.norm(base) / 22.6)


def _save(path, arr, garbage=False):
    buf = io.BytesIO()
    np.save(buf, arr.astype(np.float32), allow_pickle=False)
    path.write_bytes(b"\x00not a gallery" if garbage else buf.getvalue())


def test_first_enrollment_allowed(tmp_path):
    v = gate.check_continuity(_person(1), tmp_path / "embeddings_v2.npy", unprotect=ident)
    assert v.allowed and v.reason == "first_enrollment"


def test_same_person_replaces_gallery(tmp_path):
    p = tmp_path / "embeddings_v2.npy"
    base = _person(1, spread=0.3)
    _save(p, base)
    fresh = _unit(base + 0.25 * np.random.default_rng(9).normal(size=base.shape) / 22.6)   # same face, new session
    v = gate.check_continuity(fresh, p, unprotect=ident, required=True)
    assert v.allowed and v.reason == "continuity_ok"
    assert v.median_best >= gate.MEDIAN_BEST_MIN


def test_different_person_is_refused(tmp_path):
    p = tmp_path / "embeddings_v2.npy"
    _save(p, _person(1))
    v = gate.check_continuity(_person(2), p, unprotect=ident, required=True)
    assert not v.allowed and v.reason == "different_person"
    assert v.median_best < gate.MEDIAN_BEST_MIN


def test_reset_waiver_allows_a_different_face(tmp_path):
    p = tmp_path / "embeddings_v2.npy"
    _save(p, _person(1))
    v = gate.check_continuity(_person(2), p, unprotect=ident, required=False)
    assert v.allowed and v.reason == "continuity_waived"


def test_mixed_gallery_with_one_matching_template_is_not_enough(tmp_path):
    p = tmp_path / "embeddings_v2.npy"
    owner = _person(1)
    _save(p, owner)
    sneaky = np.vstack([owner[:1], _person(3, n=7)])        # 1 of 8 templates is the owner
    assert not gate.check_continuity(sneaky, p, unprotect=ident, required=True).allowed


def test_unreadable_existing_gallery_does_not_block_recovery(tmp_path):
    p = tmp_path / "embeddings_v2.npy"
    _save(p, _person(1), garbage=True)
    v = gate.check_continuity(_person(2), p, unprotect=ident, required=True)
    assert v.allowed and v.reason == "existing_unreadable"


def test_invalid_new_gallery_refused(tmp_path):
    bad = np.full((3, 512), np.nan)
    assert gate.check_continuity(bad, tmp_path / "x.npy", unprotect=ident).reason == "invalid_new_gallery"
    assert not gate.check_continuity(np.zeros((2, 512)), tmp_path / "x.npy", unprotect=ident).allowed


def test_continuity_env_switch():
    assert gate.continuity_required({})
    assert gate.continuity_required({"MG_ENROLL_CONTINUITY": "1"})
    assert not gate.continuity_required({"MG_ENROLL_CONTINUITY": "0"})


def _make_backups(tmp_path, count):
    gallery = tmp_path / "embeddings_v2.npy"
    names = []
    for i in range(count):
        b = tmp_path / f"embeddings_v2.backup-20261124-0000{i:02d}-000000.npy"
        _save(b, _person(10 + i))
        names.append(b)
    return gallery, names


def test_prune_keeps_newest_only(tmp_path):
    gallery, names = _make_backups(tmp_path, 5)
    removed = gate.prune_backups([gallery], keep=2)
    assert sorted(p.name for p in removed) == sorted(n.name for n in names[:3])
    assert [p.name for p in gate.list_backups(gallery)] == [names[3].name, names[4].name]


def test_restore_brings_back_newest_valid_backup(tmp_path):
    gallery, names = _make_backups(tmp_path, 3)
    _save(gallery, _person(99), garbage=True)                # live gallery is corrupt
    _save(names[2], _person(5), garbage=True)                # newest backup is corrupt too
    used = gate.restore_latest_valid_backup(gallery, unprotect=ident)
    assert used == names[1]
    assert gate.read_gallery(gallery, ident)[0] == "ok"


def test_restore_leaves_a_healthy_gallery_alone(tmp_path):
    gallery, _ = _make_backups(tmp_path, 2)
    _save(gallery, _person(1))
    before = gallery.read_bytes()
    assert gate.restore_latest_valid_backup(gallery, unprotect=ident) is None
    assert gallery.read_bytes() == before


def test_restore_with_no_good_backup_returns_none(tmp_path):
    gallery = tmp_path / "embeddings_v2.npy"
    _save(gallery, _person(1), garbage=True)
    assert gate.restore_latest_valid_backup(gallery, unprotect=ident) is None


def test_restore_also_restores_matching_metadata(tmp_path):
    gallery, names = _make_backups(tmp_path, 1)
    meta = tmp_path / "embeddings_v2_meta.json"
    meta.write_text("broken")
    (tmp_path / "embeddings_v2_meta.backup-20261124-000000-000000.json").write_text('{"ok": 1}')
    _save(gallery, _person(1), garbage=True)
    assert gate.restore_latest_valid_backup(gallery, meta, unprotect=ident) == names[0]
    assert meta.read_text() == '{"ok": 1}'


def test_result_file_roundtrip_and_staleness(tmp_path):
    assert gate.read_result(tmp_path) is None
    assert gate.write_result(tmp_path, "rejected_different_person", "x" * 500)
    r = gate.read_result(tmp_path)
    assert r["outcome"] == "rejected_different_person" and len(r["detail"]) == 160
    assert gate.read_result(tmp_path, max_age_s=-1) is None
    (tmp_path / gate.RESULT_FILE).write_text("{oops")
    assert gate.read_result(tmp_path) is None
