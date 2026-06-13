import pytest
import numpy as np
import cv2
from types import SimpleNamespace
from liveness_detector import LivenessDetector

# ── Fixture to initialize LivenessDetector ───────────────────────────────────

@pytest.fixture
def detector():
    # Do not load ONNX models (pass empty directory or none) to keep it fast
    # and avoid real inference except for tested logic
    return LivenessDetector(model_dir="")

# ── Layer 1: LBP Texture ─────────────────────────────────────────────────────

def test_lbp_texture_score_flat_image(detector):
    # Flat image (no texture)
    roi = np.zeros((128, 128, 3), dtype=np.uint8)
    score = detector._lbp_texture_score(roi)
    # LBP variance and entropy should be zero -> score should be 0.0
    assert score == pytest.approx(0.0)

def test_lbp_texture_score_noisy_image(detector):
    # Random noisy image (texture)
    np.random.seed(42)
    roi = np.random.randint(0, 256, (128, 128, 3), dtype=np.uint8)
    score = detector._lbp_texture_score(roi)
    # Texture exists, so score should be higher
    assert score > 0.4

# ── Layer 2: Specular Reflection ─────────────────────────────────────────────

def test_specular_score_normal_face(detector):
    # Normal face (no high glare)
    roi = np.zeros((128, 128, 3), dtype=np.uint8)
    # Glare mask: (value > 225) & (saturation < 40)
    # With all zeros, no glare.
    score = detector._specular_score(roi)
    assert score == 0.9

def test_specular_score_screen_glare(detector):
    # High glare, small spatial spread (typical screen replay)
    roi = np.zeros((100, 100, 3), dtype=np.uint8)
    # Convert BGR to HSV: H=0, S=0, V=255 for glare pixels (white spots)
    # Let's draw a compact white square in the center (glare spots close together)
    roi[40:60, 40:60] = [255, 255, 255]
    
    # Saturation is 0 (<40), Value is 255 (>225), so it is flagged as glare
    # Glare area is 20*20 = 400 out of 10000 pixels = 4% (which is < 5%)
    # Let's make it larger so glare_fraction > 15%
    roi[30:70, 30:70] = [255, 255, 255] # 40*40 = 1600 pixels = 16%
    
    score = detector._specular_score(roi)
    # High glare (16%) and small spatial spread (<30) should trigger screen detection
    assert score == 0.2

# ── Layer 3: Color Space Analysis ────────────────────────────────────────────

def test_color_space_score_no_skin(detector):
    # Solid blue image (no skin color)
    roi = np.zeros((128, 128, 3), dtype=np.uint8)
    roi[:, :, 0] = 255 # B = 255
    score = detector._color_space_score(roi)
    # No skin, low chromatic variance.
    # Clip keeps it in [0.55, 0.95] range as calibrated by the system.
    assert score == pytest.approx(0.55)

def test_color_space_score_skin_like(detector):
    # Skin-like color: Y=150, Cr=150, Cb=100 with realistic chromatic variance/correlation
    np.random.seed(42)
    ycrcb = np.zeros((128, 128, 3), dtype=np.uint8)
    ycrcb[:, :, 0] = 150  # Y
    # Generate Cr/Cb noise matching real skin distributions
    cr_noise = np.random.normal(150, 12, (128, 128))
    cb_noise = 100 + (cr_noise - 150) * 0.5 + np.random.normal(0, 5, (128, 128))
    
    ycrcb[:, :, 1] = np.clip(cr_noise, 0, 255).astype(np.uint8)
    ycrcb[:, :, 2] = np.clip(cb_noise, 0, 255).astype(np.uint8)
    roi = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)
    
    score = detector._color_space_score(roi)
    # High skin ratio and realistic variance should result in a higher score
    assert score > 0.55

# ── Layer 4: Moiré/Frequency ─────────────────────────────────────────────────

def test_moire_score_flat_image(detector):
    roi = np.ones((128, 128, 3), dtype=np.uint8) * 128
    score = detector._moire_score(roi)
    # Constant value has std_mag = 0, which triggers fallback score 0.7
    assert score == 0.7

def test_moire_score_periodic_pattern(detector):
    # Create periodic stripes (simulating moire frequency spikes)
    roi = np.zeros((128, 128, 3), dtype=np.uint8)
    for x in range(0, 128, 4):
        roi[:, x:x+2] = 255
    
    score = detector._moire_score(roi)
    # Periodic stripes should create high peak-to-mean frequency spikes
    # peak_ratio > 10 should return 0.15, or peak_ratio > 7 return 0.4
    assert score <= 0.4

# ── Layer 5: Temporal Blink & Micro Movement ────────────────────────────────

def test_temporal_blink_score_no_kps(detector):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face = SimpleNamespace(bbox=[100, 100, 200, 200]) # No kps attribute
    score = detector._temporal_blink_score(frame, face)
    assert score == 0.5

def test_temporal_blink_score_and_micro_movement(detector):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    
    # 1. Test Static Face (no displacement -> micro_movement score = 0.2)
    # We feed 10 frames at same position
    for i in range(10):
        face = SimpleNamespace(
            bbox=[100, 100, 200, 200],
            kps=[[130, 150], [170, 150]] # left/right eye centers
        )
        score = detector._temporal_blink_score(frame, face)
    # No blink detected (blink_score = 0.5).
    # Static face (movement_score = 0.2).
    # Combined = 0.5 * 0.6 + 0.2 * 0.4 = 0.3 + 0.08 = 0.38
    assert score == pytest.approx(0.38)

    # 2. Test Normal Sway (displacement 0.5-5px -> high micro_movement score)
    detector._face_center_history.clear()
    for i in range(10):
        # Shift bbox center by 1px alternatingly
        offset = 1.0 if (i % 2 == 0) else 0.0
        face = SimpleNamespace(
            bbox=[100 + offset, 100, 200 + offset, 200],
            kps=[[130 + offset, 150], [170 + offset, 150]]
        )
        score = detector._temporal_blink_score(frame, face)
    # Movement score should be higher (clip(0.5 + std * 0.3, 0.5, 0.95))
    # std of [1, 0, 1, 0...] diffs is 0.5
    # Combined score should be higher than 0.38
    assert score > 0.40

# ── Layer 6: Face Boundary ───────────────────────────────────────────────────

def test_boundary_score_no_edges(detector):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    face = SimpleNamespace(bbox=[100, 100, 200, 200])
    score = detector._boundary_score(frame, face)
    # No lines detected -> returns 0.9
    assert score == 0.9

def test_boundary_score_rectangular_border(detector):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # Let's draw a rectangular border around the expanded bounding box region
    # face is at [100, 100, 200, 200], expand = (200-100)*0.4 = 40.
    # outer_roi is [60, 60, 240, 240] (size 180x180)
    # Draw white rectangle at coordinates 65, 65, 235, 235
    cv2.rectangle(frame, (65, 65), (235, 235), (255, 255, 255), 2)
    
    face = SimpleNamespace(bbox=[100, 100, 200, 200])
    score = detector._boundary_score(frame, face)
    # Rectangular lines detected on multiple sides -> low score (0.2 or 0.45)
    assert score <= 0.45

# ── Layer 7: ONNX Anti-Spoof ─────────────────────────────────────────────────

def test_onnx_antispoof_resilience_to_exception(detector):
    # ONNX session is not loaded -> returns None
    roi = np.zeros((128, 128, 3), dtype=np.uint8)
    assert detector._onnx_antispoof_score(roi) is None

    # Mock an ONNX session that fails
    class FailingSession:
        def run(self, *args, **kwargs):
            raise RuntimeError("Inference failed")
    
    detector._antispoof_session = FailingSession()
    detector._antispoof_h = 128
    detector._antispoof_w = 128
    detector._antispoof_input_name = "input"

    # Single failure: increments counter but does not crash, returns None
    assert detector._onnx_antispoof_score(roi) is None
    assert detector._onnx_consecutive_failures == 1

# ── Layer 8: Depth Geometry ──────────────────────────────────────────────────

def test_depth_geometry_score(detector):
    # No kps -> returns 0.5
    face = SimpleNamespace(bbox=[100, 100, 200, 200])
    assert detector._depth_geometry_score(face) == 0.5

    # Static landmarks (photo -> variance = 0 -> score = 0.2)
    face_static = SimpleNamespace(
        bbox=[100, 100, 200, 200],
        kps=[[130, 140], [170, 140], [150, 160], [140, 180], [160, 180]]
    )
    for _ in range(5):
        score = detector._depth_geometry_score(face_static)
    assert score == 0.2

    # Moving landmarks (real person -> variance > 0.008 -> score > 0.5)
    detector._landmark_ratios.clear()
    for i in range(5):
        offset = float(i) * 5.0
        face_moving = SimpleNamespace(
            bbox=[100 + offset, 100, 200 + offset, 200],
            kps=[
                [130 + offset, 140],
                [170 + offset, 140],
                [150 + offset * 1.5, 160], # vary vertical/horizontal proportions
                [140 + offset, 180],
                [160 + offset, 180]
            ]
        )
        score = detector._depth_geometry_score(face_moving)
    assert score > 0.5

# ── Layer 9: Histogram Consistency ───────────────────────────────────────────

def test_histogram_consistency_score(detector):
    roi = np.zeros((128, 128, 3), dtype=np.uint8)
    
    # 5 identical frames (perfect stability, zero variance) -> returns 0.45
    for _ in range(5):
        score = detector._histogram_consistency_score(roi)
    assert score == 0.45

# ── Anti-Replay ──────────────────────────────────────────────────────────────

def test_replay_detection(detector):
    roi = np.zeros((128, 128, 3), dtype=np.uint8)
    
    # Unique frame -> returns 0.95
    score1 = detector._replay_detection(roi)
    assert score1 == 0.95

    # Send duplicates
    detector._replay_detection(roi) # 2nd
    score3 = detector._replay_detection(roi) # 3rd
    assert score3 == 0.8 # up to 2 duplicates allowed -> 0.8

    # Many duplicates -> drops to 0.1
    for _ in range(5):
        score_last = detector._replay_detection(roi)
    assert score_last == 0.1
