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


def test_daemon_fails_closed_when_v2_embeddings_have_no_valid_templates(tmp_path, monkeypatch):
    local_app_data = tmp_path / "LocalAppData"
    enrollment_dir = local_app_data / "MajestyGuard"
    enrollment_dir.mkdir(parents=True)
    np.save(enrollment_dir / "embeddings_v2.npy", np.ones((2, 3), dtype=np.float32))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    daemon = _daemon_with_engine(FakeEngine(accepted_count=0))

    with pytest.raises(RuntimeError, match="No valid v2 enrolled embeddings"):
        daemon._load_enrolled_embeddings()


def test_daemon_fails_closed_when_v2_embeddings_file_is_corrupt(tmp_path, monkeypatch):
    local_app_data = tmp_path / "LocalAppData"
    enrollment_dir = local_app_data / "MajestyGuard"
    enrollment_dir.mkdir(parents=True)
    (enrollment_dir / "embeddings_v2.npy").write_bytes(b"not a numpy file")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    daemon = _daemon_with_engine(FakeEngine(accepted_count=1))

    with pytest.raises(RuntimeError, match="Failed to load v2 embeddings"):
        daemon._load_enrolled_embeddings()


def test_daemon_reports_count_accepted_by_face_engine(tmp_path, monkeypatch):
    local_app_data = tmp_path / "LocalAppData"
    enrollment_dir = local_app_data / "MajestyGuard"
    enrollment_dir.mkdir(parents=True)
    matrix = np.eye(2, 512, dtype=np.float32)
    np.save(enrollment_dir / "embeddings_v2.npy", matrix)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    engine = FakeEngine(accepted_count=2)
    daemon = _daemon_with_engine(engine)

    daemon._load_enrolled_embeddings()

    assert np.allclose(np.asarray(engine.loaded, dtype=np.float32), matrix)
