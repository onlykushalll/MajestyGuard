"""
Per-layer liveness diagnostic for MajestyGuard v2.

Runs the same 12-layer liveness components on webcam frames and prints per-layer
statistics. This is diagnostic-only: no locking, no IPC, no frame writes.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from insightface.app import FaceAnalysis

sys.path.insert(0, os.path.dirname(__file__))

from liveness_detector import LivenessDetector
from face_quality import measure_face_quality

def _default_v2_root() -> Path:
    env_root = os.environ.get("MG_V2_ROOT")
    candidates = []
    if env_root:
        candidates.append(Path(env_root))
    here = Path(__file__).resolve()
    candidates.extend([
        here.parents[2] / "active" / "MajestyGuard-v2",
        Path("C:/tmp/MajestyGuard-v2"),
    ])
    for candidate in candidates:
        if (candidate / "models").exists() and (candidate / "models_insightface").exists():
            return candidate
    return candidates[0]


_V2_ROOT = _default_v2_root()
MODELS_DIR = _V2_ROOT / "models"
INSIGHTFACE_DIR = _V2_ROOT / "models_insightface"


_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def _enhance_frame(frame: np.ndarray) -> np.ndarray:
    """Mirror FaceEngine._enhance_frame for diagnostic parity."""
    try:
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        mean_l = float(np.mean(l))
        if mean_l > 100:
            return frame
        l_enhanced = _CLAHE.apply(l)
        if mean_l < 50:
            gamma = 0.6
            l_enhanced = (np.power(l_enhanced / 255.0, gamma) * 255.0).astype(np.uint8)
        enhanced = cv2.merge([l_enhanced, a, b])
        return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
    except Exception:
        return frame


def _select_primary_face(frame: np.ndarray, faces: list) -> Optional[object]:
    if not faces:
        return None

    h, w = frame.shape[:2]
    frame_area = max(1, h * w)
    frame_center_x = w / 2.0
    frame_center_y = h / 2.0

    def score(face) -> float:
        try:
            x1, y1, x2, y2 = [float(v) for v in face.bbox]
        except Exception:
            return -1.0
        box_w = max(0.0, x2 - x1)
        box_h = max(0.0, y2 - y1)
        if box_w <= 0.0 or box_h <= 0.0:
            return -1.0
        area_score = min(1.0, ((box_w * box_h) / frame_area) / 0.35)
        face_center_x = (x1 + x2) / 2.0
        face_center_y = (y1 + y2) / 2.0
        dist_x = abs(face_center_x - frame_center_x) / max(1.0, frame_center_x)
        dist_y = abs(face_center_y - frame_center_y) / max(1.0, frame_center_y)
        center_score = max(0.0, 1.0 - ((dist_x + dist_y) / 2.0))
        contains_center = x1 <= frame_center_x <= x2 and y1 <= frame_center_y <= y2
        center_bonus = 0.15 if contains_center else 0.0
        det_score = float(getattr(face, "det_score", 0.75))
        return det_score * 0.45 + area_score * 0.30 + center_score * 0.20 + center_bonus

    selected = max(faces, key=score)
    return selected if score(selected) >= 0.0 else None


def _diagnose_one(detector: LivenessDetector, frame: np.ndarray, face) -> Optional[dict[str, float]]:
    roi = detector._extract_roi(frame, face)
    if roi is None:
        return None

    detector._frame_index += 1
    replay = detector._replay_detection(roi)
    if replay < 0.3:
        return {
            "lbp": 0.0,
            "specular": 0.0,
            "color": 0.0,
            "moire": 0.0,
            "temporal": 0.0,
            "boundary": 0.0,
            "onnx": 0.0,
            "geometry": 0.0,
            "histogram": 0.0,
            "midas": 0.0,
            "rppg": 0.0,
            "attention": 0.0,
            "replay": replay,
            "combined": 0.1,
            "smoothed": 0.1,
        }

    lbp = detector._lbp_texture_score(roi)
    specular = detector._specular_score(roi)
    color = detector._color_space_score(roi)
    moire = detector._moire_score(roi)
    temporal = detector._temporal_blink_score(frame, face)
    boundary = detector._boundary_score(frame, face)
    onnx = detector._onnx_antispoof_score(roi)
    onnx_idx0 = detector._last_onnx_idx0
    onnx_idx1 = detector._last_onnx_idx1
    geometry = detector._depth_geometry_score(face)
    histogram = detector._histogram_consistency_score(roi)
    midas = (
        detector._depth_liveness.score(frame, face)
        if detector._depth_liveness is not None else 0.5
    )
    rppg = detector._rppg.update(frame, face)
    attention = detector._attention.score(frame)

    if onnx is not None:
        combined = (
            onnx * 0.10 +
            lbp * 0.13 +
            specular * 0.08 +
            color * 0.09 +
            moire * 0.10 +
            temporal * 0.10 +
            boundary * 0.09 +
            geometry * 0.09 +
            histogram * 0.08 +
            replay * 0.14
        )
    else:
        combined = (
            lbp * 0.18 +
            specular * 0.10 +
            color * 0.14 +
            moire * 0.10 +
            temporal * 0.14 +
            boundary * 0.08 +
            geometry * 0.12 +
            histogram * 0.07 +
            replay * 0.07
        )

    if detector._depth_liveness is not None and detector._depth_liveness.available:
        if midas < 0.38:
            combined = combined * 0.85 + midas * 0.15
        elif midas > 0.72:
            combined = combined * 0.88 + midas * 0.12

    if detector._rppg.has_signal:
        if rppg >= 0.60 or attention >= 0.75:
            combined = min(
                0.98,
                combined
                + max(0.0, rppg - 0.50) * 0.06
                + max(0.0, attention - 0.50) * 0.04,
            )
        else:
            combined = combined * 0.94 + rppg * 0.04 + attention * 0.02

    detector._score_history.append(combined)
    if detector._frame_index < detector._MIN_FRAMES_FOR_PASS:
        smoothed = min(float(np.mean(detector._score_history)), 0.75)
    else:
        smoothed = float(np.percentile(list(detector._score_history)[-30:], 10))

    return {
        "lbp": float(lbp),
        "specular": float(specular),
        "color": float(color),
        "moire": float(moire),
        "temporal": float(temporal),
        "boundary": float(boundary),
        "onnx": float(onnx) if onnx is not None else np.nan,
        "onnx_idx0": float(onnx_idx0),
        "onnx_idx1": float(onnx_idx1),
        "geometry": float(geometry),
        "histogram": float(histogram),
        "midas": float(midas),
        "rppg": float(rppg),
        "attention": float(attention),
        "replay": float(replay),
        "combined": float(combined),
        "smoothed": float(smoothed),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--target", type=float, default=0.75)
    parser.add_argument("--warn-below", type=float, default=0.65)
    parser.add_argument("--warmup-samples", type=int, default=30)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()

    print(f"InsightFace models: {INSIGHTFACE_DIR}")
    print(f"Liveness models: {MODELS_DIR}")

    app = FaceAnalysis(
        name="buffalo_l",
        root=str(INSIGHTFACE_DIR),
        providers=["CPUExecutionProvider"],
        allowed_modules=["detection", "recognition"],
    )
    app.prepare(ctx_id=0, det_size=(320, 320))
    detector = LivenessDetector(model_dir=str(MODELS_DIR))

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Could not open camera {args.camera}")
        return 2
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    samples: dict[str, list[float]] = defaultdict(list)
    no_face = 0
    skipped_quality = 0
    end_at = time.monotonic() + args.seconds
    frame_no = 0

    while time.monotonic() < end_at:
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        frame_no += 1
        frame = _enhance_frame(frame)
        face = _select_primary_face(frame, app.get(frame))
        if face is None:
            no_face += 1
            continue
        quality = measure_face_quality(frame, face)
        if quality.score < LivenessDetector._MIN_USABLE_FACE_QUALITY:
            skipped_quality += 1
            if args.preview:
                x1, y1, x2, y2 = [int(v) for v in face.bbox]
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 165, 255), 2)
                cv2.putText(frame, f"skip q={quality.score:.2f}", (x1, max(20, y1 - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 165, 255), 2)
                cv2.imshow("MajestyGuard liveness layers diagnostic", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            continue

        result = _diagnose_one(detector, frame, face)
        if result is None:
            continue

        for key, value in result.items():
            if not np.isnan(value):
                samples[key].append(float(value))

        if frame_no % 15 == 0:
            print(
                f"frame={frame_no:04d} smoothed={result['smoothed']:.3f} "
                f"onnx={result['onnx']:.3f} "
                f"idx0={result['onnx_idx0']:.3f} idx1={result['onnx_idx1']:.3f} "
                f"combined={result['combined']:.3f}"
            )

        if args.preview:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 0), 2)
            cv2.putText(frame, f"live={result['smoothed']:.3f}", (x1, max(20, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 0), 2)
            cv2.imshow("MajestyGuard liveness layers diagnostic", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()
    detector.close()

    if not samples:
        print(f"No liveness samples. no_face={no_face}")
        return 2

    print()
    print("Layer diagnostic summary")
    print(f"  no_face_frames: {no_face}")
    print(f"  skipped_quality_frames: {skipped_quality}")
    weak: list[str] = []
    for key in [
        "lbp", "specular", "color", "moire", "temporal", "boundary",
        "onnx", "onnx_idx0", "onnx_idx1", "geometry", "histogram", "midas", "rppg", "attention",
        "replay", "combined", "smoothed",
    ]:
        values = np.array(samples.get(key, []), dtype=np.float32)
        if values.size == 0:
            continue
        median = float(np.median(values))
        p10 = float(np.percentile(values, 10))
        p90 = float(np.percentile(values, 90))
        print(f"  {key:10s} median={median:.3f} p10={p10:.3f} p90={p90:.3f}")
        if key not in {"midas", "rppg", "attention"} and median < args.warn_below:
            weak.append(key)

    final_smoothed = np.array(samples["smoothed"], dtype=np.float32)
    all_sustained = float(np.percentile(final_smoothed, 10))
    warmup = max(0, min(args.warmup_samples, max(0, final_smoothed.size - 1)))
    steady_smoothed = final_smoothed[warmup:] if final_smoothed.size > warmup else final_smoothed
    sustained = float(np.percentile(steady_smoothed, 10))
    print(f"  sustained_p10_all: {all_sustained:.3f}")
    print(f"  sustained_p10_steady: {sustained:.3f} (after {warmup} warmup samples)")
    if weak:
        print("Weak layers below warning threshold: " + ", ".join(weak))
    if sustained < args.target:
        print("FAIL: sustained liveness below target.")
        return 1

    print("PASS: sustained liveness reached target.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
