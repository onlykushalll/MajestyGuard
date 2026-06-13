"""
Owner-readiness preflight for MajestyGuard camera tests.

Samples the webcam briefly and checks whether the enrolled owner appears
centered enough to start longer recognition/liveness runs. No frames are saved,
no IPC is started, and no lock action is possible from this script.
"""
from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis

from face_quality import measure_face_quality
from mg_recog_diag import EMBEDDINGS_PATH, MODELS_DIR, _enhance_frame, _load_embeddings, _select_primary_face


@dataclass(frozen=True)
class PreflightDecision:
    status: str
    message: str
    exit_code: int


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _classify_preflight(
    *,
    max_score: float,
    median_height: float,
    median_offset: float,
    median_quality: float,
    dev_score: float,
    ready_score: float,
    min_face_height: float,
    max_center_offset: float,
    min_quality: float,
) -> PreflightDecision:
    """Classify owner readiness without mixing identity and liveness thresholds."""
    ready_score = max(float(ready_score), float(dev_score))

    if median_height < min_face_height:
        return PreflightDecision(
            "WAIT",
            "face is too small; move closer before running daemon tests",
            1,
        )
    if median_offset > max_center_offset:
        return PreflightDecision("WAIT", "face is too far from frame center", 1)
    if median_quality < min_quality:
        return PreflightDecision(
            "WAIT",
            "frame quality is too low; improve lighting, focus, or distance",
            1,
        )
    if max_score < dev_score:
        return PreflightDecision(
            "WAIT",
            "identity recognition score is below the daemon recognition floor; "
            "RGB liveness threshold is not an owner-identity gate",
            1,
        )
    if max_score < ready_score:
        return PreflightDecision(
            "CAUTION",
            "owner identity passes the daemon dev floor but misses the production margin; "
            "continue only with MG_ENABLE_LOCK=0",
            0,
        )
    return PreflightDecision(
        "READY",
        "enrolled owner appears visible, centered, and above the production margin",
        0,
    )


def _face_geometry(frame: np.ndarray, face) -> tuple[float, float]:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in face.bbox]
    height_frac = max(0.0, y2 - y1) / max(1.0, float(h))
    face_center_x = (x1 + x2) / 2.0
    face_center_y = (y1 + y2) / 2.0
    dist_x = abs(face_center_x - w / 2.0) / max(1.0, w / 2.0)
    dist_y = abs(face_center_y - h / 2.0) / max(1.0, h / 2.0)
    center_offset = float(np.clip((dist_x + dist_y) / 2.0, 0.0, 1.0))
    return float(height_frac), center_offset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--dev-score", type=float, default=_env_float("MG_RECOGNITION_THRESHOLD", 0.78))
    parser.add_argument("--ready-score", type=float, default=0.82)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--min-face-height", type=float, default=0.24)
    parser.add_argument("--max-center-offset", type=float, default=0.45)
    parser.add_argument("--min-quality", type=float, default=0.45)
    parser.add_argument("--min-samples", type=int, default=5)
    args = parser.parse_args()
    if args.min_score is not None:
        args.ready_score = args.min_score

    enrolled = _load_embeddings()
    print("MajestyGuard owner preflight")
    print(f"  embeddings: {Path(EMBEDDINGS_PATH)} shape={enrolled.shape}")
    print(f"  models: {MODELS_DIR}")
    print(
        "  identity thresholds: dev_floor=%.3f ready_margin=%.3f"
        % (args.dev_score, max(args.ready_score, args.dev_score))
    )

    app = FaceAnalysis(
        name="buffalo_l",
        root=str(MODELS_DIR),
        providers=["CPUExecutionProvider"],
        allowed_modules=["detection", "recognition"],
    )
    app.prepare(ctx_id=0, det_size=(320, 320))

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"WAIT: could not open camera {args.camera}")
        return 2

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    scores: list[float] = []
    qualities: list[float] = []
    heights: list[float] = []
    offsets: list[float] = []
    no_face = 0
    frames = 0
    end_at = time.monotonic() + args.seconds

    try:
        while time.monotonic() < end_at:
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            frames += 1
            frame = _enhance_frame(frame)
            face = _select_primary_face(frame, app.get(frame))
            if face is None or getattr(face, "normed_embedding", None) is None:
                no_face += 1
                continue

            emb = face.normed_embedding.astype(np.float32)
            emb = emb / (np.linalg.norm(emb) + 1e-8)
            scores.append(float(np.max(enrolled @ emb)))
            height, offset = _face_geometry(frame, face)
            heights.append(height)
            offsets.append(offset)
            qualities.append(float(measure_face_quality(frame, face).score))
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if len(scores) < args.min_samples:
        print(f"WAIT: not enough owner samples ({len(scores)}/{args.min_samples}); no_face_frames={no_face}")
        return 1

    score_arr = np.array(scores, dtype=np.float32)
    height_arr = np.array(heights, dtype=np.float32)
    offset_arr = np.array(offsets, dtype=np.float32)
    quality_arr = np.array(qualities, dtype=np.float32)
    max_score = float(np.max(score_arr))
    median_score = float(np.median(score_arr))
    median_height = float(np.median(height_arr))
    median_offset = float(np.median(offset_arr))
    median_quality = float(np.median(quality_arr))

    print(
        "  samples=%d frames=%d no_face=%d max_score=%.3f median_score=%.3f "
        "median_face_h=%.2f median_center=%.2f median_quality=%.2f"
        % (len(scores), frames, no_face, max_score, median_score, median_height, median_offset, median_quality)
    )

    decision = _classify_preflight(
        max_score=max_score,
        median_height=median_height,
        median_offset=median_offset,
        median_quality=median_quality,
        dev_score=args.dev_score,
        ready_score=args.ready_score,
        min_face_height=args.min_face_height,
        max_center_offset=args.max_center_offset,
        min_quality=args.min_quality,
    )

    print(f"{decision.status}: {decision.message}")
    return decision.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
