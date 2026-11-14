"""Commit / rollback behaviour of the coverage-enrollment gallery writer."""
from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "daemon"))

import enrollment_commit as ecm  # noqa: E402
import enrollment_coverage as ec  # noqa: E402

PREFIX = b"ENC:"


def fake_protect(data, description):
    return PREFIX + data


def fake_is_protected(data):
    return data.startswith(PREFIX)


def decode_gallery(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    assert raw.startswith(PREFIX)
    return np.load(io.BytesIO(raw[len(PREFIX):]), allow_pickle=False)


def make_result(n=52, ok=True, seed=3) -> ec.CoverageResult:
    rng = np.random.default_rng(seed)
    buckets = list(ec.ALL_BUCKETS[:9])
    samples = []
    for i in range(n):
        e = ec._unit(rng.standard_normal(512)).astype(np.float32)
        samples.append(ec.CoverageSample(
            embedding=e, bucket=buckets[i % len(buckets)], rel_yaw=0.0, rel_pitch=0.0,
            rel_roll=0.0, quality=0.8, det_score=0.9, height_frac=0.3, illumination=120.0,
            live_score=0.9, t=float(i), selected=True))
    counts: dict[str, int] = {}
    for s in samples:
        counts[s.bucket] = counts.get(s.bucket, 0) + 1
    return ec.CoverageResult(ok, "" if ok else "coverage_incomplete", selected=samples,
                             captured_count=n + 3, pruned_count=3, bucket_counts=counts)


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "embeddings_v2.npy", tmp_path / "embeddings_v2_meta.json"


def commit(result, paths, **kw):
    return ecm.commit_result(result, gallery_path=paths[0], meta_path=paths[1],
                             protect=fake_protect, is_protected=fake_is_protected, **kw)


def test_successful_commit_writes_protected_gallery_and_metadata(paths):
    result = make_result()
    out = commit(result, paths)
    assert out.ok and out.backups == {}
    gallery = decode_gallery(paths[0])
    assert gallery.shape == (52, 512) and gallery.dtype == np.float32
    np.testing.assert_allclose(gallery, result.embeddings(), atol=1e-6)
    meta = json.loads(paths[1].read_bytes()[len(PREFIX):])
    assert meta["version"] == ecm.METADATA_VERSION
    assert meta["selection_policy"] == ecm.SELECTION_POLICY
    assert meta["selected_count"] == 52 and len(meta["selected_samples"]) == 52
    assert all("embedding" not in row and "angle" in row for row in meta["selected_samples"])
    assert not list(paths[0].parent.glob("*.tmp"))


def test_incomplete_result_writes_nothing(paths):
    out = commit(make_result(ok=False), paths)
    assert not out.ok and out.error.startswith("not_committed")
    assert not paths[0].exists() and not paths[1].exists()


def test_empty_result_writes_nothing(paths):
    out = commit(ec.CoverageResult(True, "", selected=[]), paths)
    assert not out.ok and not paths[0].exists()


@pytest.mark.parametrize(
    "mutate,problem",
    [
        (lambda a: a.__setitem__((0, 0), np.nan), "non_finite"),
        (lambda a: a.__imul__(2.0), "not_normalized"),
    ],
)
def test_invalid_embeddings_are_refused(paths, mutate, problem):
    arr = make_result().embeddings()
    mutate(arr)
    out = ecm.commit_gallery(arr, {}, gallery_path=paths[0], meta_path=paths[1],
                             protect=fake_protect, is_protected=fake_is_protected)
    assert not out.ok and problem in out.error
    assert not paths[0].exists() and not paths[1].exists()


def test_bad_shape_refused(paths):
    out = ecm.commit_gallery(np.zeros(5, dtype=np.float32), {}, gallery_path=paths[0],
                             meta_path=paths[1], protect=fake_protect, is_protected=fake_is_protected)
    assert not out.ok and "bad_shape" in out.error


def test_input_array_is_not_mutated(paths):
    arr = make_result().embeddings()
    before = arr.copy()
    out = ecm.commit_gallery(arr, {"k": 1}, gallery_path=paths[0], meta_path=paths[1],
                             protect=fake_protect, is_protected=fake_is_protected)
    assert out.ok
    np.testing.assert_array_equal(arr, before)


def test_existing_files_are_backed_up_protected(paths):
    paths[0].write_bytes(PREFIX + b"old-gallery")
    paths[1].write_bytes(b"old-meta-unprotected")
    out = commit(make_result(), paths)
    assert out.ok and set(out.backups) == set(paths)
    assert out.backups[paths[0]].read_bytes() == PREFIX + b"old-gallery"
    # plaintext legacy file is protected before being kept as a backup
    assert out.backups[paths[1]].read_bytes() == PREFIX + b"old-meta-unprotected"
    assert decode_gallery(paths[0]).shape[0] == 52


def test_metadata_write_failure_rolls_back_both_files(paths):
    paths[0].write_bytes(PREFIX + b"old-gallery")
    paths[1].write_bytes(PREFIX + b"old-meta")

    def flaky(data, description):
        if description == ecm.META_DESCRIPTION:
            raise RuntimeError("dpapi meta boom")
        return PREFIX + data

    out = ecm.commit_result(make_result(), gallery_path=paths[0], meta_path=paths[1],
                            protect=flaky, is_protected=fake_is_protected)
    assert not out.ok and out.rolled_back and not out.rollback_errors
    assert "meta boom" in out.error
    assert paths[0].read_bytes() == PREFIX + b"old-gallery"
    assert paths[1].read_bytes() == PREFIX + b"old-meta"
    assert not list(paths[0].parent.glob("*.tmp"))


def test_first_enrollment_failure_leaves_no_partial_files(paths):
    def flaky(data, description):
        if description == ecm.META_DESCRIPTION:
            raise RuntimeError("boom")
        return PREFIX + data

    out = ecm.commit_result(make_result(), gallery_path=paths[0], meta_path=paths[1],
                            protect=flaky, is_protected=fake_is_protected)
    assert not out.ok and out.rolled_back
    assert not paths[0].exists() and not paths[1].exists()


def test_replace_failure_on_metadata_rolls_back(paths, monkeypatch):
    paths[0].write_bytes(PREFIX + b"old-gallery")
    paths[1].write_bytes(PREFIX + b"old-meta")
    real_replace = ecm.os.replace

    def failing_replace(src, dst):
        if Path(dst) == paths[1] and ".rollback-" not in str(src):
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(ecm.os, "replace", failing_replace)
    out = commit(make_result(), paths)
    assert not out.ok and out.rolled_back and "disk full" in out.error
    assert paths[0].read_bytes() == PREFIX + b"old-gallery"
    assert paths[1].read_bytes() == PREFIX + b"old-meta"


def test_incomplete_rollback_is_reported(paths, monkeypatch):
    paths[0].write_bytes(PREFIX + b"old-gallery")
    paths[1].write_bytes(PREFIX + b"old-meta")

    def flaky(data, description):
        if description == ecm.META_DESCRIPTION:
            raise RuntimeError("boom")
        return PREFIX + data

    def broken_copy2(*a, **k):
        raise OSError("copy blocked")

    monkeypatch.setattr(ecm.shutil, "copy2", broken_copy2)
    out = ecm.commit_result(make_result(), gallery_path=paths[0], meta_path=paths[1],
                            protect=flaky, is_protected=fake_is_protected)
    assert not out.ok and not out.rolled_back and out.rollback_errors


def test_encryption_failure_refuses_plaintext_and_keeps_old_gallery(paths):
    paths[0].write_bytes(PREFIX + b"old-gallery")

    def broken(data, description):
        if description == ecm.GALLERY_DESCRIPTION:
            raise RuntimeError("no dpapi")
        return PREFIX + data

    out = ecm.commit_result(make_result(), gallery_path=paths[0], meta_path=paths[1],
                            protect=broken, is_protected=fake_is_protected)
    assert not out.ok and out.error.startswith("encryption_failed")
    assert paths[0].read_bytes() == PREFIX + b"old-gallery"
    assert not paths[1].exists()


def test_plaintext_only_when_explicitly_allowed(paths):
    def broken(data, description):
        if description == ecm.GALLERY_DESCRIPTION:
            raise RuntimeError("no dpapi")
        return PREFIX + data

    out = ecm.commit_result(make_result(), gallery_path=paths[0], meta_path=paths[1],
                            protect=broken, is_protected=fake_is_protected, allow_plaintext=True)
    assert out.ok
    assert np.load(io.BytesIO(paths[0].read_bytes()), allow_pickle=False).shape == (52, 512)


def test_build_metadata_has_no_embeddings_and_records_coverage():
    meta = ecm.build_metadata(make_result())
    assert "embedding" not in json.dumps(meta).lower().replace("embeddings", "")
    assert meta["pruned_count"] == 3 and meta["captured_count"] == 55
    assert meta["thresholds"]["liveness"] >= 0.70
    assert meta["min_angle_count"] == len(meta["angles"]) > 0
