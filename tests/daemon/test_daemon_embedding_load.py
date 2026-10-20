import hashlib
import io

import numpy as np
import pytest

from main import MajestyGuardDaemon


class FakeEngine:
    def __init__(self, accepted_count: int):
        self.accepted_count = accepted_count
        self.loaded = None

    def load_enrolled_embeddings(self, embeddings):
        self.loaded = embeddings
        return self.accepted_count


def _daemon_with_engine(engine):
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.face_eng = engine
    return daemon


def _save_encrypted(enrollment_dir, matrix: np.ndarray) -> None:
    """Persist embeddings the way enroll_v2.py does: DPAPI-encrypted .npy bytes."""
    import win32crypt

    buf = io.BytesIO()
    np.save(buf, matrix)
    (enrollment_dir / "embeddings_v2.npy").write_bytes(
        win32crypt.CryptProtectData(
            buf.getvalue(), "MajestyGuard biometric embeddings", None, None, None, 0
        )
    )


def test_daemon_fails_closed_when_v2_embeddings_have_no_valid_templates(tmp_path, monkeypatch):
    local_app_data = tmp_path / "LocalAppData"
    enrollment_dir = local_app_data / "MajestyGuard"
    enrollment_dir.mkdir(parents=True)
    _save_encrypted(enrollment_dir, np.ones((2, 3), dtype=np.float32))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    daemon = _daemon_with_engine(FakeEngine(accepted_count=0))

    with pytest.raises(RuntimeError, match="No valid v2 enrolled embeddings"):
        daemon._load_enrolled_embeddings()


def test_daemon_fails_closed_when_v2_embeddings_file_is_corrupt(tmp_path, monkeypatch, caplog):
    """MG-A-03/C-03/D-03: unreadable plaintext garbage never reaches the
    engine and never crashes the daemon -- the unlock path degrades to
    "not enrolled" (fail-closed) instead of falling back to plaintext."""
    local_app_data = tmp_path / "LocalAppData"
    enrollment_dir = local_app_data / "MajestyGuard"
    enrollment_dir.mkdir(parents=True)
    (enrollment_dir / "embeddings_v2.npy").write_bytes(b"not a numpy file")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    daemon = _daemon_with_engine(FakeEngine(accepted_count=1))

    with caplog.at_level("ERROR"):
        daemon._load_enrolled_embeddings()  # must NOT raise

    assert daemon.face_eng.loaded is None
    assert any("refused (fail-closed)" in r.getMessage() for r in caplog.records)


def test_daemon_refuses_unpinned_plaintext_embeddings(tmp_path, monkeypatch, caplog):
    """The attacker variant of the corrupt case: a VALID plaintext .npy
    substituted into embeddings_v2.npy (gallery substitution). Anything
    that is not the pinned legacy store stays refused even though it
    parses fine."""
    local_app_data = tmp_path / "LocalAppData"
    enrollment_dir = local_app_data / "MajestyGuard"
    enrollment_dir.mkdir(parents=True)
    np.save(enrollment_dir / "embeddings_v2.npy", np.eye(2, 512, dtype=np.float32))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    daemon = _daemon_with_engine(FakeEngine(accepted_count=2))

    with caplog.at_level("ERROR"):
        daemon._load_enrolled_embeddings()  # must NOT raise

    assert daemon.face_eng.loaded is None
    assert any("Refusing to load plaintext embeddings" in r.getMessage() for r in caplog.records)


def test_daemon_migrates_pinned_legacy_plaintext_once(tmp_path, monkeypatch):
    """ALIGNMENT (2026-09-20): the one pinned legacy plaintext store
    auto-migrates to DPAPI-encrypted storage on load, preserving the
    owner's enrollment and re-encrypting the file on disk so the
    plaintext path can never load again."""
    local_app_data = tmp_path / "LocalAppData"
    enrollment_dir = local_app_data / "MajestyGuard"
    enrollment_dir.mkdir(parents=True)
    matrix = np.eye(2, 512, dtype=np.float32)
    buf = io.BytesIO()
    np.save(buf, matrix)
    raw = buf.getvalue()
    (enrollment_dir / "embeddings_v2.npy").write_bytes(raw)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(
        MajestyGuardDaemon,
        "_LEGACY_V2_PLAINTEXT_SHA256",
        hashlib.sha256(raw).hexdigest().upper(),
    )
    engine = FakeEngine(accepted_count=2)
    daemon = _daemon_with_engine(engine)

    daemon._load_enrolled_embeddings()

    assert np.allclose(np.asarray(engine.loaded, dtype=np.float32), matrix)
    # The on-disk store must no longer be the plaintext bytes.
    assert (enrollment_dir / "embeddings_v2.npy").read_bytes() != raw


def test_daemon_reports_count_accepted_by_face_engine(tmp_path, monkeypatch):
    local_app_data = tmp_path / "LocalAppData"
    enrollment_dir = local_app_data / "MajestyGuard"
    enrollment_dir.mkdir(parents=True)
    matrix = np.eye(2, 512, dtype=np.float32)
    _save_encrypted(enrollment_dir, matrix)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    engine = FakeEngine(accepted_count=2)
    daemon = _daemon_with_engine(engine)

    daemon._load_enrolled_embeddings()

    assert np.allclose(np.asarray(engine.loaded, dtype=np.float32), matrix)
