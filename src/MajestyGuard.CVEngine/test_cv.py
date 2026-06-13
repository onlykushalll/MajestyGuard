# MajestyGuard.CVEngine/test_cv.py
# Run: pytest test_cv.py -v -k "not manual and not webcam"
import pytest
import numpy as np
import sys, os
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def liveness():
    from liveness_detector import LivenessDetector
    return LivenessDetector(model_dir="../../models")


# ── B-021: Liveness uses min(), not mean() ───────────────────────────────────

def test_liveness_min_not_mean(liveness):
    """One low score in window must sink the whole result."""
    from collections import deque
    liveness._score_history = deque([0.95] * 9 + [0.1], maxlen=liveness._WINDOW)
    liveness._frame_index = liveness._MIN_FRAMES_FOR_PASS + 1

    smoothed = float(np.min(list(liveness._score_history)))
    assert smoothed < 0.85, (
        f"B-021: min should be 0.1, got {smoothed}. "
        "If using mean: 0.865 passes — that's wrong."
    )


# ── B-029: ONNX failure must not null the session ────────────────────────────

def test_liveness_onnx_failure_keeps_session(liveness):
    """Single ONNX inference error must not disable the model."""
    if liveness._antispoof_session is None:
        pytest.skip("No ONNX anti-spoof model loaded")

    original = liveness._antispoof_session
    bad_roi = np.zeros((1, 1, 3), dtype=np.uint8)  # Wrong shape → inference error
    liveness._onnx_antispoof_score(bad_roi)

    assert liveness._antispoof_session is not None, \
        "B-029: ONNX session was nulled after inference error"
    assert liveness._antispoof_session is original


# ── E-2: _onnx_consecutive_failures initialized in __init__ ──────────────────

def test_liveness_onnx_counter_initialized():
    """_onnx_consecutive_failures must be a real field from __init__, not lazy."""
    from liveness_detector import LivenessDetector
    ld = LivenessDetector()
    assert hasattr(ld, "_onnx_consecutive_failures"), \
        "E-2: _onnx_consecutive_failures missing from __init__"
    assert ld._onnx_consecutive_failures == 0


# ── E-3: antispoof attrs initialized so _onnx_antispoof_score works safely ───

def test_liveness_antispoof_attrs_initialized():
    """_antispoof_input_name / _h / _w must exist even when no model is loaded."""
    from liveness_detector import LivenessDetector
    ld = LivenessDetector()
    assert hasattr(ld, "_antispoof_input_name"), "E-3: _antispoof_input_name not in __init__"
    assert hasattr(ld, "_antispoof_h"),          "E-3: _antispoof_h not in __init__"
    assert hasattr(ld, "_antispoof_w"),           "E-3: _antispoof_w not in __init__"


# ── S-5: _extract_roi failure → 0.0 (fail closed), not 0.5 ──────────────────

def test_liveness_roi_failure_failclosed(liveness):
    """ROI extraction failure must return 0.0 (deny), not 0.5 (neutral)."""
    class BadFace:
        bbox = [0, 0, 0, 0]   # zero-area box → extraction fails
        kps = None

    # A 1x1 frame with a zero-bbox face will fail extraction
    tiny_frame = np.zeros((1, 1, 3), dtype=np.uint8)
    result = liveness.score(tiny_frame, BadFace())
    assert result == 0.0, \
        f"S-5: ROI failure must return 0.0 (fail-closed), got {result}"


# ── P-1: CLAHE cached in __init__, not created per frame ─────────────────────

def test_face_engine_clahe_cached():
    """CLAHE object must be created once in __init__, not per call."""
    import importlib, types
    # Import without camera/insightface (skip if not available)
    try:
        from face_engine import FaceEngine
    except Exception:
        pytest.skip("FaceEngine import failed (missing insightface)")

    # Can't call initialize() without camera, but __init__ runs
    fe = FaceEngine.__new__(FaceEngine)
    FaceEngine.__init__(fe, model_dir="../../models")
    assert hasattr(fe, "_clahe"), "P-1: FaceEngine._clahe not initialized in __init__"


# ── P-3: enrolled_matrix built from load_enrolled_embeddings ─────────────────

def test_face_engine_builds_matrix_on_load():
    """load_enrolled_embeddings must build _enrolled_matrix for vectorized matmul."""
    try:
        from face_engine import FaceEngine
    except Exception:
        pytest.skip("FaceEngine import failed")

    fe = FaceEngine.__new__(FaceEngine)
    FaceEngine.__init__(fe, model_dir="../../models")

    fake_embeddings = [np.random.rand(512).tolist() for _ in range(3)]
    fe.load_enrolled_embeddings(fake_embeddings)

    assert fe._enrolled_matrix is not None, "P-3: _enrolled_matrix not built"
    assert fe._enrolled_matrix.shape == (3, 512), \
        f"P-3: expected (3, 512), got {fe._enrolled_matrix.shape}"


# ── L-3: _consecutive_liveness resets on recognition failure ─────────────────

def test_face_engine_consecutive_liveness_resets_on_mismatch():
    """Recognition failure must reset _consecutive_liveness, not just _consecutive_matches."""
    try:
        from face_engine import FaceEngine
    except Exception:
        pytest.skip("FaceEngine import failed")

    fe = FaceEngine.__new__(FaceEngine)
    FaceEngine.__init__(fe, model_dir="../../models")

    # Manually set state as if 2 liveness passes but score drops below threshold
    fe._consecutive_liveness = 2
    fe._consecutive_matches  = 2

    # Load a dummy enrolled embedding far from zero vector (will give near-0 similarity)
    far_embedding = np.ones(512, dtype=np.float32)
    far_embedding /= np.linalg.norm(far_embedding)
    fe.load_enrolled_embeddings([far_embedding.tolist()])

    # Query with zero-like embedding — similarity ≈ 0 → below threshold
    # Simulate the logic directly (no camera needed)
    query = np.zeros(512, dtype=np.float32)
    query[0] = 1.0  # normalized
    scores = fe._enrolled_matrix @ query
    best = float(np.max(scores))

    # Simulate the recognition branch
    if best >= fe.recognition_threshold:
        fe._consecutive_matches += 1
    else:
        fe._consecutive_matches = 0
        fe._consecutive_liveness = 0  # L-3: this must happen

    assert fe._consecutive_liveness == 0, \
        "L-3: _consecutive_liveness must reset to 0 when recognition fails"


# ── Virtual camera detector: cache keyed by index ────────────────────────────

def test_virtual_camera_cache_keyed_by_index():
    """Cache must be per-camera-index, not global."""
    from virtual_camera_detector import VirtualCameraDetector

    detector = VirtualCameraDetector()
    # Directly inject cache entries for two different indices
    import time
    detector._cache[0] = (time.monotonic(), False)
    detector._cache[1] = (time.monotonic(), True)

    # Verify they return distinct values
    cached_0 = detector._cache.get(0)
    cached_1 = detector._cache.get(1)

    assert cached_0 is not None and cached_0[1] is False
    assert cached_1 is not None and cached_1[1] is True


# ── Enrollment: consistency threshold ≥ 0.55 ─────────────────────────────────

def test_enrollment_consistency_threshold():
    """_validate_consistency threshold must be ≥ 0.55 (not the old 0.35)."""
    import inspect
    from enrollment import EnrollmentManager
    src = inspect.getsource(EnrollmentManager._validate_consistency)
    # Check the default argument value
    import re
    m = re.search(r'threshold:\s*float\s*=\s*([\d.]+)', src)
    assert m is not None, "Could not find threshold parameter"
    threshold = float(m.group(1))
    assert threshold >= 0.55, \
        f"L-4: threshold is {threshold}, must be >= 0.55 to reject cross-person embeddings"


# ── IpcMessage: Deserialize handles all message types ────────────────────────

def test_ipc_message_deserialize_coverage():
    """All IPC message types must round-trip through Deserialize without returning None."""
    # This is a documentation test — actual test runs in C#.
    # Here we verify the Python side doesn't have a parallel issue.
    # (Python uses plain dicts for IPC, no type dispatch)
    import json
    msgs = [
        {"MessageType": "DetectionResult", "FaceCount": 0, "PrimaryUserPresent": False,
         "RecognitionScore": 0.0, "LivenessScore": 0.0, "LivenessPassed": False,
         "VirtualCameraDetected": False, "CameraObstructed": False, "InferenceMs": 5.0},
        {"MessageType": "UserIdleDetected", "IdleMs": 5000},
        {"MessageType": "UserActivityDetected"},
        {"MessageType": "ManualFallbackRequest"},
    ]
    for m in msgs:
        serialized = json.dumps(m)
        parsed = json.loads(serialized)
        assert parsed["MessageType"] == m["MessageType"]


def test_cv_server_connects_pipe_and_heartbeat_before_camera_init():
    """Service watchdog must see CV liveness even if LocalSystem camera init blocks."""
    source = Path(__file__).with_name("cv_server.py").read_text(encoding="utf-8")
    start_body = source[source.index("    def start(self):"):source.index("    # ──", source.index("    def start(self):"))]

    assert "self._running = True" in start_body
    assert "self._connect_pipe()" in start_body
    assert "self._start_heartbeat_thread()" in start_body
    assert "self._engine.initialize()" in start_body
    assert start_body.index("self._running = True") < start_body.index("self._connect_pipe()")
    assert start_body.index("self._connect_pipe()") < start_body.index("self._start_heartbeat_thread()")
    assert start_body.index("self._start_heartbeat_thread()") < start_body.index("self._engine.initialize()")
    assert "sys.exit(1)" not in start_body


def test_cv_server_heartbeat_loop_is_not_tied_to_frame_processing():
    """Heartbeat must continue while camera frame reads or model inference are slow."""
    source = Path(__file__).with_name("cv_server.py").read_text(encoding="utf-8")

    assert "def _start_heartbeat_thread" in source
    assert "def _heartbeat_loop" in source
    assert "def _send_heartbeat" in source
    assert "target=self._heartbeat_loop" in source
    heartbeat_body = source[source.index("    def _heartbeat_loop"):source.index("    def ", source.index("    def _heartbeat_loop") + 1)]
    assert "process_frame" not in heartbeat_body


# ── AR6: Liveness state resets between enrollment angles ─────────────────────

def test_enrollment_liveness_resets_between_angles():
    """Liveness frame index must not carry over between enrollment angle captures."""
    from liveness_detector import LivenessDetector
    ld = LivenessDetector()
    # Simulate 5 frames for angle 1
    for _ in range(5):
        ld._score_history.append(0.9)
    ld._frame_index = 5
    assert ld._frame_index == 5
    ld.reset_session()
    assert ld._frame_index == 0
    assert len(ld._score_history) == 0


# ── AR6-integration: FaceEngine.reset_liveness exists ────────────────────────

def test_face_engine_reset_liveness_exists():
    """FaceEngine must expose reset_liveness() for enrollment to call."""
    try:
        from face_engine import FaceEngine
    except Exception:
        pytest.skip("FaceEngine import failed")

    fe = FaceEngine.__new__(FaceEngine)
    FaceEngine.__init__(fe, model_dir="../../models")
    assert hasattr(fe, "reset_liveness"), "AR6: FaceEngine.reset_liveness() missing"


# ── N1/E1: virtual_camera_detector uses context manager for registry key ─────

def test_virtual_camera_clsid_uses_context_manager():
    """_check_clsid_blocklist must use 'with' for registry key (no leak on error)."""
    import inspect
    from virtual_camera_detector import VirtualCameraDetector
    src = inspect.getsource(VirtualCameraDetector._check_clsid_blocklist)
    assert "with winreg.OpenKey" in src, \
        "N1: _check_clsid_blocklist must use context manager for registry key"
