from types import SimpleNamespace
import time

import numpy as np

import enroll_v2


def test_enrollment_requires_exactly_one_detected_face():
    owner = SimpleNamespace(name="owner")
    stranger = SimpleNamespace(name="stranger")

    assert enroll_v2._single_enrollment_face([]) is None
    assert enroll_v2._single_enrollment_face([owner]) is owner
    assert enroll_v2._single_enrollment_face([owner, stranger]) is None


def test_studio_analysis_gets_a_copy_before_preview_privacy_zeroing():
    raw = np.full((8, 8, 3), 77, dtype=np.uint8)
    observed = {}

    class Studio:
        def update_preview_frame(self, frame):
            observed["preview"] = frame.copy()
            frame.fill(0)  # mirrors the privacy ownership contract of the UI

        def analyze_live_frame_async(self, frame, analyzer):
            observed["analysis"] = frame.copy()
            observed["analyzer"] = analyzer

    marker = object()
    enroll_v2._schedule_studio_analysis(Studio(), raw, marker)

    assert np.all(observed["preview"] == 77)
    assert np.all(observed["analysis"] == 77)
    assert not np.any(raw)
    assert observed["analyzer"] is marker


def test_enrollment_pose_alignment_uses_requested_view():
    frontal = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 72.0], [44.0, 92.0], [76.0, 92.0]],
        dtype=np.float32,
    )
    turned_left = frontal.copy()
    turned_left[2, 0] = 66.0

    assert enroll_v2.enrollment_pose_aligned(frontal, "Front - neutral expression")
    assert not enroll_v2.enrollment_pose_aligned(frontal, "Left 10 degrees - small turn")
    assert enroll_v2.enrollment_pose_aligned(turned_left, "Left 10 degrees - small turn")
    assert not enroll_v2.enrollment_pose_aligned(turned_left, "Right 10 degrees - small turn")


def test_enrollment_pose_accepts_practical_turns_but_rejects_frontal_reuse():
    frontal = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 72.0], [44.0, 92.0], [76.0, 92.0]],
        dtype=np.float32,
    )
    moderate_left = frontal.copy()
    moderate_left[2, 0] = 69.0

    assert not enroll_v2.enrollment_pose_aligned(frontal, "Left 25 degrees - wider turn")
    assert enroll_v2.enrollment_pose_aligned(moderate_left, "Left 25 degrees - wider turn")


def test_enrollment_pose_does_not_reject_a_clear_frontal_or_posture_view_on_pitch_noise():
    # Five-point landmarks are reliable for a left/right cue, but laptop camera
    # pitch estimates vary materially with camera height and lens perspective.
    # Baseline/posture views must remain hands-free rather than dead-ending.
    noisy_pitch = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 88.0], [44.0, 92.0], [76.0, 92.0]],
        dtype=np.float32,
    )

    assert enroll_v2.enrollment_pose_aligned(noisy_pitch, "Front - neutral expression")
    assert enroll_v2.enrollment_pose_aligned(
        noisy_pitch, "Natural working posture / glasses if you use them"
    )


def test_capture_burst_rejects_a_face_that_is_not_in_the_requested_pose(monkeypatch):
    frontal = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 72.0], [44.0, 92.0], [76.0, 92.0]],
        dtype=np.float32,
    )
    face = SimpleNamespace(kps=frontal)

    class Camera:
        def read(self):
            return True, np.full((120, 160, 3), 80, dtype=np.uint8)

    class Analyzer:
        def get(self, _frame):
            return [face]

    class Liveness:
        def reset_session(self):
            pass

        def score_fast(self, *_args):
            raise AssertionError("misaligned pose must be rejected before liveness scoring")

    monkeypatch.setattr(
        enroll_v2,
        "_face_readiness",
        lambda *_args, **_kwargs: (True, "READY", SimpleNamespace(score=0.9)),
    )
    samples = enroll_v2._capture_burst(
        Camera(),
        Analyzer(),
        "Left 10 degrees - small turn",
        1,
        samples_per_angle=1,
        min_quality=0.6,
        liveness=Liveness(),
        enrollment_deadline=time.monotonic() + 0.18,
    )

    assert samples == []


def test_capture_burst_backs_off_when_camera_read_fails(monkeypatch):
    clock = [0.0]
    sleeps = []

    def monotonic():
        clock[0] += 0.1
        return clock[0]

    class Camera:
        reads = 0

        def read(self):
            self.reads += 1
            return False, None

    class Liveness:
        def reset_session(self):
            pass

    camera = Camera()
    monkeypatch.setattr(
        enroll_v2,
        "time",
        SimpleNamespace(monotonic=monotonic, sleep=lambda seconds: sleeps.append(seconds)),
    )

    result = enroll_v2._capture_burst(
        camera,
        object(),
        "Front",
        1,
        samples_per_angle=1,
        min_quality=0.6,
        liveness=Liveness(),
    )

    assert result == []
    assert camera.reads > 0
    assert sleeps and all(seconds == 0.05 for seconds in sleeps)
    assert "camera" in result.status.lower()


def test_incomplete_capture_burst_zeroes_partial_embeddings(monkeypatch):
    clock = [0.0]
    sample_objects = []
    frontal = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 72.0], [44.0, 92.0], [76.0, 92.0]],
        dtype=np.float32,
    )
    face = SimpleNamespace(
        kps=frontal,
        normed_embedding=np.ones(512, dtype=np.float32),
        det_score=0.99,
    )

    def monotonic():
        clock[0] += 0.1
        return clock[0]

    class Camera:
        reads = 0

        def read(self):
            self.reads += 1
            if self.reads == 1:
                return True, np.full((120, 160, 3), 80, dtype=np.uint8)
            return False, None

    class Analyzer:
        def get(self, _frame):
            return [face]

    class Liveness:
        def reset_session(self):
            pass

        def score_fast(self, *_args):
            return 1.0

    def make_sample(**kwargs):
        sample = SimpleNamespace(**kwargs)
        sample_objects.append(sample)
        return sample

    quality = SimpleNamespace(
        score=0.9,
        height_frac=0.5,
        center_offset=0.0,
        sharpness=1.0,
        illumination_mean=128.0,
    )
    monkeypatch.setattr(enroll_v2, "time", SimpleNamespace(monotonic=monotonic, sleep=lambda _seconds: None))
    monkeypatch.setattr(enroll_v2, "_enhance_frame", lambda frame: frame)
    monkeypatch.setattr(
        enroll_v2,
        "_face_readiness",
        lambda *_args, **_kwargs: (True, "READY", quality),
    )
    monkeypatch.setattr(enroll_v2, "EnrollmentSample", make_sample)

    result = enroll_v2._capture_burst(
        Camera(),
        Analyzer(),
        "Front",
        1,
        samples_per_angle=2,
        min_quality=0.6,
        liveness=Liveness(),
    )

    assert result == []
    assert sample_objects
    assert not np.any(sample_objects[0].embedding)
    assert "1/2" in result.status


def test_face_readiness_accepts_a_clear_normal_laptop_frame():
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    # Add high-frequency checkerboard in face ROI so sharpness is high
    frame[140:300, 220:420:2] = 200
    face = SimpleNamespace(
        bbox=[240.0, 160.0, 400.0, 304.0],  # height_frac = 144 / 480 = 0.30
        det_score=0.92,
        normed_embedding=np.ones(512, dtype=np.float32),
    )
    ready_front, status_front, _ = enroll_v2._face_readiness(
        frame, face, min_quality=0.50, angle="Front - neutral expression"
    )
    assert ready_front is True
    assert status_front == "READY"

    ready_lean, status_lean, _ = enroll_v2._face_readiness(
        frame, face, min_quality=0.50, angle="Leaning back / away - relaxed real working posture"
    )
    assert ready_lean is True
    assert status_lean == "READY"
