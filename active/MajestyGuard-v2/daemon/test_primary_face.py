import time
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import main as daemon_main
from companion_ipc import FaceState
from face_engine import FaceEngine, _OwnerBoxKalman
from main import MajestyGuardDaemon, State
from presence import PresenceDetector


class FakeFace:
    def __init__(self, bbox, det_score=0.75, embedding=None):
        self.bbox = bbox
        self.det_score = det_score
        if embedding is not None:
            emb = np.array(embedding, dtype=np.float32)
            self.normed_embedding = emb / (np.linalg.norm(emb) + 1e-8)


class FakeIpc:
    def __init__(self):
        self.states = []

    def broadcast_state(self, state, **kwargs):
        self.states.append((state, kwargs))


class FakeServiceIpc:
    def __init__(self):
        self.results = []

    def broadcast_detection_result(self, result):
        self.results.append(result)


class FakeMotion:
    def __init__(self):
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1


class FakeFaceEngine:
    def __init__(self, results):
        self.results = list(results)
        self.reset_liveness_calls = 0

    def process_frame(self, frame):
        return self.results.pop(0)

    def reset_liveness(self):
        self.reset_liveness_calls += 1


class FakeApp:
    def __init__(self, faces):
        self.faces = faces

    def get(self, _frame):
        return self.faces


class FakeLiveness:
    def __init__(self, score):
        self._score = score

    def score(self, _frame, _face):
        return self._score


def test_face_engine_selects_centered_primary_face():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    tiny_high_confidence = FakeFace([12, 20, 62, 70], det_score=0.99)
    centered_user = FakeFace([180, 100, 460, 420], det_score=0.82)

    engine = FaceEngine.__new__(FaceEngine)
    selected = FaceEngine._select_primary_face(engine, frame, [tiny_high_confidence, centered_user])

    assert selected is centered_user


def test_face_engine_rejects_invalid_primary_faces():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    invalid = FakeFace([10, 10, 10, 50], det_score=0.99)

    engine = FaceEngine.__new__(FaceEngine)
    selected = FaceEngine._select_primary_face(engine, frame, [invalid])

    assert selected is None


def test_presence_detector_selects_centered_primary_face():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    tiny_high_confidence = np.array([12, 20, 50, 50, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.99])
    centered_user = np.array([180, 100, 280, 320, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.82])

    selected = PresenceDetector._select_primary_face(frame, [tiny_high_confidence, centered_user])

    assert selected is centered_user


def test_presence_detector_rejects_invalid_primary_faces():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    invalid = np.array([10, 10, 0, 40, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.99])

    selected = PresenceDetector._select_primary_face(frame, [invalid])

    assert selected is None


def test_daemon_foreground_gate_accepts_large_centered_face():
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    result = SimpleNamespace(face_height_frac=0.32, face_center_offset=0.20)

    assert daemon._is_foreground_face(result)


def test_daemon_foreground_gate_rejects_background_faces():
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    small = SimpleNamespace(face_height_frac=0.12, face_center_offset=0.20)
    off_center = SimpleNamespace(face_height_frac=0.40, face_center_offset=0.70)

    assert not daemon._is_foreground_face(small)
    assert not daemon._is_foreground_face(off_center)


def _engine_for_selection_tests() -> FaceEngine:
    engine = FaceEngine.__new__(FaceEngine)
    engine._enrolled_matrix = np.array([[1.0, 0.0]], dtype=np.float32)
    engine._adaface_session = None
    engine._min_frame_quality = 0.0
    engine._owner_track_ttl_s = 3.0
    engine._owner_track_min_iou = 0.10
    engine._owner_track_predict_min_iou = 0.08
    engine._owner_track_min_score = 0.62
    engine._owner_track_score_margin = 0.04
    engine._last_owner_bbox = None
    engine._last_owner_seen_at = 0.0
    engine._owner_kalman = _OwnerBoxKalman(process_std=20.0, measurement_std=10.0)
    return engine


def _engine_for_smoothing_tests() -> FaceEngine:
    engine = FaceEngine.__new__(FaceEngine)
    engine._recognition_ewma = 0.0
    engine._recognition_ewma_ready = False
    engine._recognition_ewma_alpha = 0.35
    engine._active_liveness_jitter_floor = 0.55
    engine.recognition_threshold = 0.78
    engine._presence_confidence_max_boost = 0.25
    engine._presence_track_floor = 0.65
    engine._presence_min_quality = 0.55
    engine._presence_track_min_score = 0.35
    engine._owner_track_min_iou = 0.10
    return engine


def _engine_for_process_frame_tests(face, *, liveness_score=0.62) -> FaceEngine:
    engine = _engine_for_smoothing_tests()
    engine._app = FakeApp([face])
    engine._liveness = FakeLiveness(liveness_score)
    engine.liveness_threshold = 0.70
    engine._enrolled_matrix = np.array([[1.0, 0.0]], dtype=np.float32)
    engine._adaface_session = None
    engine._consecutive_matches = 0
    engine._consecutive_liveness = 0
    engine._consensus_threshold = 3
    engine._last_recognition_score = 0.0
    engine._min_frame_quality = 0.35
    engine._last_owner_bbox = None
    engine._last_owner_seen_at = 0.0
    engine._owner_track_ttl_s = 3.0
    engine._owner_track_min_iou = 0.10
    engine._owner_track_predict_min_iou = 0.08
    engine._owner_track_min_score = 0.62
    engine._owner_track_score_margin = 0.04
    engine._owner_kalman = _OwnerBoxKalman(process_std=20.0, measurement_std=10.0)
    engine._is_virtual_camera = lambda: False
    engine._is_obstructed = lambda _frame: False
    engine._enhance_frame = lambda frame: frame
    return engine


def _result(
    *,
    score=0.30,
    smooth=0.20,
    liveness=0.80,
    quality=0.80,
    height=0.35,
    center=0.15,
    selection_reason="geometry",
    sticky_iou=0.0,
    predicted_iou=0.0,
    presence=None,
):
    if presence is None:
        presence = score
    return SimpleNamespace(
        face_count=1,
        raw_face_count=1,
        primary_user_present=False,
        recognition_score=score,
        smoothed_recognition_score=smooth,
        liveness_score=liveness,
        liveness_passed=liveness >= 0.70,
        virtual_camera_detected=False,
        camera_obstructed=False,
        inference_ms=1.0,
        frame_quality=quality,
        face_height_frac=height,
        face_center_offset=center,
        selected_face_score=0.80,
        best_template_index=0,
        selection_reason=selection_reason,
        candidate_owner_score=score,
        sticky_iou=sticky_iou,
        predicted_iou=predicted_iou,
        presence_confidence=presence,
    )


def _no_face_result():
    result = _result(score=0.0, smooth=0.0, liveness=0.0, quality=0.0, height=0.0, center=0.0)
    result.face_count = 0
    result.raw_face_count = 0
    result.liveness_passed = False
    result.best_template_index = -1
    return result


def _daemon_for_tick_tests(state, face_engine):
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.state = state
    daemon.face_eng = face_engine
    daemon.ipc = FakeIpc()
    daemon.motion = FakeMotion()
    daemon._absent_frames = 0
    daemon._stranger_frames = 0
    daemon._active_reacquire_grace_frames = 0
    daemon._owner_continuity_grace_frames = 0
    daemon._scanning_owner_candidate_frames = 0
    daemon.service_ipc = None
    return daemon


def test_scanning_tick_broadcasts_detection_result_to_service_bridge():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = _result(score=0.30, liveness=0.80)
    bridge = FakeServiceIpc()
    daemon = _daemon_for_tick_tests(State.SCANNING, FakeFaceEngine([result]))
    daemon.service_ipc = bridge

    daemon._tick_scanning(frame, 1)

    assert bridge.results == [result]


def test_face_engine_sticky_owner_prefers_recent_owner_bbox():
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    centered_stranger = FakeFace([180, 100, 460, 420], det_score=0.95, embedding=[0.0, 1.0])
    previous_owner = FakeFace([44, 96, 244, 396], det_score=0.80, embedding=[1.0, 0.0])
    engine = _engine_for_selection_tests()
    engine._last_owner_bbox = (40.0, 100.0, 240.0, 400.0)
    engine._last_owner_seen_at = time.monotonic()

    selected, meta = engine._select_processing_face(frame, [centered_stranger, previous_owner])

    assert selected is previous_owner
    assert meta["reason"] in {"sticky_iou", "identity"}
    assert meta["sticky_iou"] > 0.90


def test_face_engine_identity_selection_prefers_owner_over_centered_stranger():
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    centered_stranger = FakeFace([180, 100, 460, 420], det_score=0.95, embedding=[0.0, 1.0])
    off_center_owner = FakeFace([30, 100, 230, 400], det_score=0.82, embedding=[1.0, 0.0])
    engine = _engine_for_selection_tests()

    selected, meta = engine._select_processing_face(frame, [centered_stranger, off_center_owner])

    assert selected is off_center_owner
    assert meta["reason"] == "identity"
    assert meta["candidate_owner_score"] > 0.99


def test_owner_box_kalman_predicts_constant_velocity_motion():
    tracker = _OwnerBoxKalman(process_std=10.0, measurement_std=4.0)

    tracker.update((20.0, 100.0, 220.0, 400.0), timestamp=10.0)
    tracker.update((70.0, 100.0, 270.0, 400.0), timestamp=11.0)
    predicted = tracker.predict(timestamp=12.0)

    assert predicted is not None
    x1, y1, x2, y2 = predicted
    assert x1 > 75.0
    assert y1 == pytest.approx(100.0, abs=8.0)
    assert x2 - x1 == pytest.approx(200.0, abs=12.0)


def test_face_engine_kalman_track_selects_predicted_owner_motion():
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    centered_stranger = FakeFace([180, 100, 460, 420], det_score=0.95)
    moving_owner = FakeFace([120, 100, 320, 400], det_score=0.82)
    engine = FaceEngine.__new__(FaceEngine)
    engine._enrolled_matrix = None
    engine._owner_track_ttl_s = 3.0
    engine._owner_track_min_iou = 0.80
    engine._owner_track_predict_min_iou = 0.20
    engine._owner_track_min_score = 0.62
    engine._owner_track_score_margin = 0.04
    engine._last_owner_bbox = (70.0, 100.0, 270.0, 400.0)
    engine._last_owner_seen_at = time.monotonic()
    engine._owner_kalman = _OwnerBoxKalman(process_std=10.0, measurement_std=4.0)
    engine._owner_kalman.update((20.0, 100.0, 220.0, 400.0), timestamp=engine._last_owner_seen_at - 1.0)
    engine._owner_kalman.update((70.0, 100.0, 270.0, 400.0), timestamp=engine._last_owner_seen_at - 0.5)

    selected, meta = engine._select_processing_face(frame, [centered_stranger, moving_owner])

    assert selected is moving_owner
    assert meta["reason"] == "kalman_iou"
    assert meta["sticky_iou"] < engine._owner_track_min_iou
    assert meta["predicted_iou"] >= engine._owner_track_predict_min_iou


def test_face_engine_recognition_smoother_dampens_single_bad_frame():
    engine = _engine_for_smoothing_tests()

    first = engine._update_recognition_smoother(score=0.88, quality=1.0, liveness_passed=True)
    dipped = engine._update_recognition_smoother(score=0.20, quality=0.25, liveness_passed=True)

    assert first == 0.88
    assert dipped > 0.58


def test_face_engine_recognition_smoother_resets_on_failed_liveness():
    engine = _engine_for_smoothing_tests()

    engine._update_recognition_smoother(score=0.88, quality=1.0, liveness_passed=True)
    reset = engine._update_recognition_smoother(score=0.90, quality=1.0, liveness_passed=False)

    assert reset == 0.0
    assert not engine._recognition_ewma_ready


def test_process_frame_reports_identity_score_when_liveness_jitters_below_threshold():
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    face = FakeFace([180, 100, 460, 420], det_score=0.95, embedding=[1.0, 0.0])
    engine = _engine_for_process_frame_tests(face, liveness_score=0.66)

    result = engine.process_frame(frame)

    assert not result.liveness_passed
    assert not result.primary_user_present
    assert result.recognition_score == pytest.approx(1.0)
    assert result.presence_confidence < engine.recognition_threshold


def test_face_engine_presence_confidence_holds_tracked_expression_dip_below_unlock_grade():
    engine = _engine_for_smoothing_tests()

    confidence = engine._presence_confidence(
        score=0.42,
        smoothed_score=0.74,
        quality=0.86,
        liveness_passed=True,
        selection_reason="sticky_iou",
        sticky_iou=0.82,
        predicted_iou=0.30,
    )

    assert confidence >= 0.65
    assert confidence < engine.recognition_threshold


def test_face_engine_presence_confidence_does_not_boost_bad_or_untracked_frames():
    engine = _engine_for_smoothing_tests()

    assert engine._presence_confidence(
        score=0.42,
        smoothed_score=0.74,
        quality=0.20,
        liveness_passed=True,
        selection_reason="sticky_iou",
        sticky_iou=0.82,
        predicted_iou=0.30,
    ) == pytest.approx(0.42)
    assert engine._presence_confidence(
        score=0.42,
        smoothed_score=0.74,
        quality=0.86,
        liveness_passed=False,
        selection_reason="sticky_iou",
        sticky_iou=0.82,
        predicted_iou=0.30,
    ) == 0.0
    assert engine._presence_confidence(
        score=0.25,
        smoothed_score=0.74,
        quality=0.86,
        liveness_passed=True,
        selection_reason="geometry",
        sticky_iou=0.0,
        predicted_iou=0.0,
    ) == pytest.approx(0.25)


def test_scanning_carries_brief_face_loss_before_idle_reset():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face_engine = FakeFaceEngine([_no_face_result() for _ in range(5)])
    daemon = _daemon_for_tick_tests(State.SCANNING, face_engine)

    for frame_no in range(1, 5):
        daemon._tick_scanning(frame, frame_no)
        assert daemon.state == State.SCANNING
        assert face_engine.reset_liveness_calls == 0

    daemon._tick_scanning(frame, 5)

    assert daemon.state == State.IDLE
    assert face_engine.reset_liveness_calls == 1
    assert daemon.motion.reset_calls == 1


def test_active_resets_liveness_only_after_sustained_face_loss():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face_engine = FakeFaceEngine([_no_face_result() for _ in range(5)])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    for frame_no in range(1, 5):
        daemon._tick_active(frame, frame_no)
        assert daemon.state == State.ACTIVE
        assert daemon._absent_frames == frame_no
        assert face_engine.reset_liveness_calls == 0

    daemon._tick_active(frame, 5)

    assert daemon.state == State.ACTIVE
    assert daemon._absent_frames == 5
    assert face_engine.reset_liveness_calls == 1


def test_active_face_state_expires_after_sustained_face_loss():
    FaceState.clear()
    FaceState.set_recognized(liveness_score=0.92)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face_engine = FakeFaceEngine([_no_face_result() for _ in range(5)])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    for frame_no in range(1, 6):
        daemon._tick_active(frame, frame_no)

    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason == "face-not-recognized"
    FaceState.clear()


def test_active_definite_stranger_invalidates_whcdf_face_state_immediately():
    FaceState.clear()
    FaceState.set_recognized(liveness_score=0.92)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = _result(score=0.22, smooth=0.20, liveness=0.84, quality=0.85, height=0.40, center=0.10)
    face_engine = FakeFaceEngine([result])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    daemon._tick_active(frame, 1)

    assert daemon.state == State.ACTIVE
    assert daemon._stranger_frames == 1
    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason == "face-not-recognized"
    FaceState.clear()


def test_active_liveness_failure_invalidates_whcdf_face_state_immediately():
    FaceState.clear()
    FaceState.set_recognized(liveness_score=0.92)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = _result(score=0.88, smooth=0.86, liveness=0.40, quality=0.85, height=0.40, center=0.10)
    face_engine = FakeFaceEngine([result])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    daemon._tick_active(frame, 1)

    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason == "face-not-recognized"
    FaceState.clear()


def test_active_borderline_liveness_owner_dip_holds_ui_active_without_whcdf_auth():
    FaceState.clear()
    FaceState.set_recognized(liveness_score=0.92)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = _result(
        score=0.72,
        smooth=0.74,
        liveness=0.66,
        quality=0.86,
        height=0.40,
        center=0.10,
        selection_reason="sticky_iou",
        sticky_iou=0.82,
        presence=0.74,
    )
    face_engine = FakeFaceEngine([result])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    daemon._tick_active(frame, 1)

    assert daemon.state == State.ACTIVE
    assert daemon._stranger_frames == 0
    assert daemon.ipc.states[-1] == ("active", {"confidence": pytest.approx(0.74), "liveness": pytest.approx(0.66)})
    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason == "face-not-recognized"
    FaceState.clear()


def test_active_maintenance_score_below_initial_threshold_clears_whcdf_state():
    FaceState.clear()
    FaceState.set_recognized(liveness_score=0.92)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = _result(score=0.70, smooth=0.72, liveness=0.84, quality=0.85, height=0.40, center=0.10)
    face_engine = FakeFaceEngine([result])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    daemon._tick_active(frame, 1)

    assert daemon.state == State.ACTIVE
    assert daemon.ipc.states[-1][0] == "active"
    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason == "face-not-recognized"
    FaceState.clear()


def test_active_owner_track_expression_dip_holds_ui_active_without_whcdf_auth():
    FaceState.clear()
    FaceState.set_recognized(liveness_score=0.92)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = _result(
        score=0.42,
        smooth=0.35,
        liveness=0.84,
        quality=0.85,
        height=0.40,
        center=0.10,
        selection_reason="sticky_iou",
        sticky_iou=0.82,
        presence=0.66,
    )
    face_engine = FakeFaceEngine([result])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    daemon._tick_active(frame, 1)

    assert daemon.state == State.ACTIVE
    assert daemon._stranger_frames == 0
    assert daemon.ipc.states[-1] == ("active", {"confidence": pytest.approx(0.66), "liveness": pytest.approx(0.84)})
    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason == "face-not-recognized"
    FaceState.clear()


def test_active_recent_owner_smooth_dip_keeps_ui_active():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = _result(score=0.42, smooth=0.72, presence=0.67, liveness=0.82, quality=0.80, height=0.36, center=0.12)
    face_engine = FakeFaceEngine([result])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    daemon._tick_active(frame, 1)

    assert daemon.state == State.ACTIVE
    assert daemon._stranger_frames == 0
    assert daemon.ipc.states[-1] == ("active", {"confidence": pytest.approx(0.67), "liveness": pytest.approx(0.82)})


def test_active_background_low_score_dip_does_not_keep_ui_active():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = _result(score=0.42, smooth=0.72, liveness=0.82, quality=0.80, height=0.12, center=0.12)
    face_engine = FakeFaceEngine([result])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    daemon._tick_active(frame, 1)

    assert daemon.state == State.ACTIVE
    assert daemon._stranger_frames == 0
    assert daemon.ipc.states[-1] == ("scanning", {})


def test_active_recent_owner_dip_grace_suppresses_next_stranger_burst():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    continuity = _result(score=0.42, smooth=0.72, liveness=0.82, quality=0.80, height=0.36, center=0.12)
    wild_motion = _result(score=0.20, smooth=0.35, liveness=0.82, quality=0.80, height=0.36, center=0.12)
    face_engine = FakeFaceEngine([continuity, wild_motion])
    daemon = _daemon_for_tick_tests(State.ACTIVE, face_engine)

    daemon._tick_active(frame, 1)
    assert daemon._owner_continuity_grace_frames > 0

    daemon._tick_active(frame, 2)

    assert daemon.state == State.ACTIVE
    assert daemon._stranger_frames == 0
    assert daemon.ipc.states[-1] == ("scanning", {})


def test_scanning_multiface_plausible_owner_score_delays_social_lock():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    low_score = _result(score=0.30, smooth=0.30, liveness=0.80, quality=0.85, height=0.36, center=0.12)
    ambiguous = _result(score=0.52, smooth=0.52, liveness=0.80, quality=0.88, height=0.60, center=0.30, presence=0.65)
    ambiguous.raw_face_count = 2
    followup_low = _result(score=0.28, smooth=0.35, liveness=0.80, quality=0.86, height=0.42, center=0.20)
    face_engine = FakeFaceEngine([low_score, ambiguous, followup_low])
    daemon = _daemon_for_tick_tests(State.SCANNING, face_engine)

    daemon._tick_scanning(frame, 1)
    daemon._tick_scanning(frame, 2)
    daemon._tick_scanning(frame, 3)

    assert daemon.state == State.SCANNING
    assert daemon._stranger_frames == 0
    assert ("stranger", {}) not in daemon.ipc.states


def test_scanning_owner_like_uncertain_score_suppresses_followup_stranger_burst():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    owner_like = _result(score=0.64, smooth=0.64, liveness=0.80, quality=0.90, height=0.64, center=0.22, presence=0.65)
    burst = [
        _result(score=0.26, smooth=0.41, liveness=0.80, quality=0.86, height=0.42, center=0.22),
        _result(score=0.23, smooth=0.35, liveness=0.80, quality=0.86, height=0.43, center=0.23),
        _result(score=0.28, smooth=0.34, liveness=0.80, quality=0.86, height=0.42, center=0.24),
    ]
    face_engine = FakeFaceEngine([owner_like, *burst])
    daemon = _daemon_for_tick_tests(State.SCANNING, face_engine)

    for frame_no in range(1, 5):
        daemon._tick_scanning(frame, frame_no)

    assert daemon.state == State.SCANNING
    assert daemon._stranger_frames == 0
    assert ("stranger", {}) not in daemon.ipc.states


def test_scanning_fast_owner_consensus_enters_active_below_strict_peak():
    FaceState.clear()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    first_owner_like = _result(
        score=0.73,
        smooth=0.72,
        liveness=0.76,
        quality=0.86,
        height=0.48,
        center=0.12,
        presence=0.73,
    )
    second_owner_like = _result(
        score=0.74,
        smooth=0.73,
        liveness=0.77,
        quality=0.87,
        height=0.50,
        center=0.13,
        presence=0.74,
    )
    face_engine = FakeFaceEngine([first_owner_like, second_owner_like])
    daemon = _daemon_for_tick_tests(State.SCANNING, face_engine)

    daemon._tick_scanning(frame, 1)
    daemon._tick_scanning(frame, 2)

    assert daemon.state == State.ACTIVE
    assert daemon._stranger_frames == 0
    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason.startswith("liveness-below-threshold")
    FaceState.clear()


def test_scanning_quick_owner_consensus_uses_stronger_liveness_quality_and_track():
    FaceState.clear()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    first_owner_like = _result(
        score=0.707,
        smooth=0.716,
        liveness=0.750,
        quality=0.80,
        height=0.37,
        center=0.24,
        presence=0.716,
        selection_reason="sticky_iou",
        sticky_iou=0.97,
    )
    second_owner_like = _result(
        score=0.700,
        smooth=0.706,
        liveness=0.774,
        quality=0.80,
        height=0.37,
        center=0.24,
        presence=0.706,
        selection_reason="kalman_iou",
        sticky_iou=0.92,
        predicted_iou=0.93,
    )
    face_engine = FakeFaceEngine([first_owner_like, second_owner_like])
    daemon = _daemon_for_tick_tests(State.SCANNING, face_engine)

    daemon._tick_scanning(frame, 1)
    daemon._tick_scanning(frame, 2)

    assert daemon.state == State.ACTIVE
    assert daemon._stranger_frames == 0
    FaceState.clear()


def test_scanning_quick_owner_consensus_rejects_untracked_low_score_face():
    FaceState.clear()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    first = _result(score=0.707, smooth=0.716, liveness=0.750, quality=0.80, height=0.37, center=0.24, presence=0.716)
    second = _result(score=0.700, smooth=0.706, liveness=0.774, quality=0.80, height=0.37, center=0.24, presence=0.706)
    face_engine = FakeFaceEngine([first, second])
    daemon = _daemon_for_tick_tests(State.SCANNING, face_engine)

    daemon._tick_scanning(frame, 1)
    daemon._tick_scanning(frame, 2)

    assert daemon.state == State.SCANNING
    assert FaceState.is_authorized()[0] is False
    FaceState.clear()


@pytest.mark.parametrize(
    "first,second",
    [
        (
            {"score": 0.74, "smooth": 0.73, "liveness": 0.69, "quality": 0.88, "height": 0.50, "center": 0.12},
            {"score": 0.75, "smooth": 0.74, "liveness": 0.69, "quality": 0.89, "height": 0.50, "center": 0.12},
        ),
        (
            {"score": 0.74, "smooth": 0.73, "liveness": 0.78, "quality": 0.70, "height": 0.50, "center": 0.12},
            {"score": 0.75, "smooth": 0.74, "liveness": 0.78, "quality": 0.70, "height": 0.50, "center": 0.12},
        ),
        (
            {"score": 0.74, "smooth": 0.73, "liveness": 0.78, "quality": 0.88, "height": 0.50, "center": 0.36},
            {"score": 0.75, "smooth": 0.74, "liveness": 0.78, "quality": 0.89, "height": 0.50, "center": 0.36},
        ),
    ],
)
def test_scanning_fast_owner_consensus_keeps_liveness_and_quality_gates(first, second):
    FaceState.clear()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face_engine = FakeFaceEngine([_result(**first), _result(**second)])
    daemon = _daemon_for_tick_tests(State.SCANNING, face_engine)

    daemon._tick_scanning(frame, 1)
    daemon._tick_scanning(frame, 2)

    assert daemon.state == State.SCANNING
    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason == "face-not-recognized"
    FaceState.clear()


def test_scanning_fast_owner_consensus_rejects_multiface_scene():
    FaceState.clear()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    first = _result(score=0.74, smooth=0.73, liveness=0.78, quality=0.88, height=0.50, center=0.12, presence=0.74)
    second = _result(score=0.75, smooth=0.74, liveness=0.78, quality=0.89, height=0.50, center=0.12, presence=0.75)
    first.raw_face_count = 2
    second.raw_face_count = 2
    face_engine = FakeFaceEngine([first, second])
    daemon = _daemon_for_tick_tests(State.SCANNING, face_engine)

    daemon._tick_scanning(frame, 1)
    daemon._tick_scanning(frame, 2)

    assert daemon.state == State.SCANNING
    authorized, reason = FaceState.is_authorized()
    assert not authorized
    assert reason == "face-not-recognized"
    FaceState.clear()


def test_daemon_wall_clock_limit_stops_bounded_camera_runs(monkeypatch):
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon._stop = threading.Event()
    monkeypatch.setattr(daemon_main, "MAX_SECONDS", 3.0)

    assert not daemon._stop_after_time_limit(time.monotonic())
    assert daemon._stop_after_time_limit(time.monotonic() - 3.5)
    assert daemon._stop.is_set()


def test_daemon_stranger_evidence_accepts_trusted_live_foreground_low_score():
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    result = _result(score=0.30, smooth=0.25, quality=0.80, height=0.36, center=0.12)

    assert daemon._stranger_evidence_reason(result, liveness_ok=True) == "definite_stranger"
    assert daemon._is_stranger_evidence(result, liveness_ok=True)


def test_daemon_stranger_evidence_rejects_recent_owner_smooth_score():
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    result = _result(score=0.30, smooth=0.72, quality=0.80, height=0.36, center=0.12)

    assert daemon._stranger_evidence_reason(result, liveness_ok=True) == "recent_owner_smooth"
    assert not daemon._is_stranger_evidence(result, liveness_ok=True)


def test_daemon_stranger_evidence_rejects_recent_owner_track_association():
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.state = State.ACTIVE
    result = _result(
        score=0.30,
        smooth=0.25,
        quality=0.80,
        height=0.36,
        center=0.12,
        selection_reason="sticky_iou",
        sticky_iou=0.91,
    )

    assert daemon._stranger_evidence_reason(result, liveness_ok=True) == "owner_track_uncertain"
    assert not daemon._is_stranger_evidence(result, liveness_ok=True)


def test_daemon_stranger_evidence_rejects_kalman_owner_track_association():
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.state = State.ACTIVE
    result = _result(
        score=0.30,
        smooth=0.25,
        quality=0.80,
        height=0.36,
        center=0.12,
        selection_reason="kalman_iou",
        predicted_iou=0.48,
    )

    assert daemon._stranger_evidence_reason(result, liveness_ok=True) == "owner_track_uncertain"
    assert not daemon._is_stranger_evidence(result, liveness_ok=True)


def test_daemon_stranger_evidence_rejects_active_reacquire_grace():
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    daemon.state = State.ACTIVE
    daemon._active_reacquire_grace_frames = 3
    result = _result(score=0.30, smooth=0.25, quality=0.80, height=0.36, center=0.12)

    assert daemon._stranger_evidence_reason(result, liveness_ok=True) == "active_reacquiring"
    assert not daemon._is_stranger_evidence(result, liveness_ok=True)


def test_daemon_stranger_evidence_requires_quality_and_foreground_geometry():
    daemon = MajestyGuardDaemon.__new__(MajestyGuardDaemon)
    low_quality = _result(score=0.30, smooth=0.20, quality=0.20, height=0.36, center=0.12)
    background = _result(score=0.30, smooth=0.20, quality=0.80, height=0.12, center=0.12)

    assert daemon._stranger_evidence_reason(low_quality, liveness_ok=True) == "low_quality"
    assert daemon._stranger_evidence_reason(background, liveness_ok=True) == "background_geometry"
    assert not daemon._is_stranger_evidence(low_quality, liveness_ok=True)
    assert not daemon._is_stranger_evidence(background, liveness_ok=True)
