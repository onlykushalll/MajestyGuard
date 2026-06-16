"""
Recognition diagnostic for MajestyGuard v2.

Loads %LOCALAPPDATA%\\MajestyGuard\\embeddings_v2.npy, opens the webcam, and
reports cosine similarity against the enrolled matrix. No locking, no IPC, and
no frames are written to disk.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Mapping

import cv2
import numpy as np
from insightface.app import FaceAnalysis

from diagnostic_common import (
    enhance_frame as _enhance_frame,
    posture_label as _posture_label,
    select_primary_face as _select_primary_face,
)
from face_quality import measure_face_quality

MODELS_DIR = Path(__file__).resolve().parent.parent / "models_insightface"
EMBEDDINGS_PATH = Path(os.environ.get("LOCALAPPDATA", os.environ.get("ProgramData", r"C:\ProgramData"))) / "MajestyGuard" / "embeddings_v2.npy"
EMBEDDINGS_META_PATH = EMBEDDINGS_PATH.with_name("embeddings_v2_meta.json")


def _env_float(env: Mapping[str, str], name: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


def _default_target(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    return _env_float(env, "MG_RECOGNITION_THRESHOLD", 0.78)


def _load_embeddings() -> np.ndarray:
    if not EMBEDDINGS_PATH.exists():
        raise FileNotFoundError(f"Embeddings not found: {EMBEDDINGS_PATH}")
    arr = np.load(str(EMBEDDINGS_PATH)).astype(np.float32)
    if arr.ndim != 2 or arr.shape[1] != 512:
        raise ValueError(f"Expected embeddings shape (N, 512), got {arr.shape}")
    norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
    return arr / norms


def _load_metadata() -> dict:
    if not EMBEDDINGS_META_PATH.exists():
        return {}
    try:
        return json.loads(EMBEDDINGS_META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _template_descriptor(index: int, metadata: dict) -> dict:
    selected = list(metadata.get("selected_samples") or [])
    sample = selected[index] if 0 <= index < len(selected) else {}
    return {
        "index": index,
        "angle": sample.get("angle", "unknown"),
        "angle_index": sample.get("angle_index"),
        "sample_index": sample.get("sample_index"),
        "quality": sample.get("quality"),
        "cohesion": sample.get("cohesion"),
    }


def _template_pose_report(
    winner_counts: np.ndarray,
    template_scores: list[list[float]],
    metadata: dict,
    *,
    limit: int = 8,
) -> list[dict]:
    rows: list[dict] = []
    for idx, values in enumerate(template_scores):
        wins = int(winner_counts[idx]) if idx < len(winner_counts) else 0
        if wins <= 0 and not values:
            continue
        descriptor = _template_descriptor(idx, metadata)
        vals = np.array(values, dtype=np.float32)
        if vals.size:
            descriptor.update(
                {
                    "wins": wins,
                    "median": float(np.median(vals)),
                    "p90": float(np.percentile(vals, 90)),
                    "max": float(np.max(vals)),
                }
            )
        else:
            descriptor.update({"wins": wins, "median": None, "p90": None, "max": None})
        rows.append(descriptor)
    rows.sort(key=lambda row: (row["wins"], row["max"] or 0.0), reverse=True)
    return rows[:limit]


def _format_template_row(row: dict) -> str:
    quality = row.get("quality")
    cohesion = row.get("cohesion")
    quality_text = "?" if quality is None else f"{float(quality):.2f}"
    cohesion_text = "?" if cohesion is None else f"{float(cohesion):.2f}"
    return (
        f"  template {row['index']}: wins={row['wins']} median={row['median']:.3f} "
        f"p90={row['p90']:.3f} max={row['max']:.3f} "
        f"pose={row['angle']!r} sample={row.get('sample_index')} "
        f"quality={quality_text} cohesion={cohesion_text}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--target", type=float, default=_default_target())
    parser.add_argument("--top-templates", type=int, default=8)
    parser.add_argument("--posture", help="Human label for this diagnostic posture, e.g. upright or tilted")
    parser.add_argument("--raw", action="store_true", help="Disable daemon-style low-light enhancement")
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    posture = _posture_label(args.posture)

    enrolled = _load_embeddings()
    metadata = _load_metadata()
    print(f"Loaded embeddings: {EMBEDDINGS_PATH} shape={enrolled.shape}")
    if metadata:
        print(
            "Metadata: profile=%s captured=%s selected=%s created=%s"
            % (
                metadata.get("profile", "unknown"),
                metadata.get("captured_count", "?"),
                metadata.get("selected_count", "?"),
                metadata.get("created_at", "?"),
            )
        )
    print(f"Models: {MODELS_DIR}")
    print(f"Posture: {posture}")
    print(f"Target recognition threshold: {args.target:.3f}")

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
        print(f"Could not open camera {args.camera}")
        return 2

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    scores: list[float] = []
    qualities: list[float] = []
    heights: list[float] = []
    offsets: list[float] = []
    winner_counts = np.zeros(enrolled.shape[0], dtype=np.int32)
    template_scores: list[list[float]] = [[] for _ in range(enrolled.shape[0])]
    no_face = 0
    no_embedding = 0
    end_at = time.monotonic() + args.seconds
    frame_no = 0

    while time.monotonic() < end_at:
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        frame_no += 1
        if not args.raw:
            frame = _enhance_frame(frame)

        face = _select_primary_face(frame, app.get(frame))
        if face is None:
            no_face += 1
            if args.preview:
                cv2.imshow("MajestyGuard recognition diagnostic", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            continue

        emb = getattr(face, "normed_embedding", None)
        if emb is None:
            no_embedding += 1
            continue

        emb = emb.astype(np.float32)
        emb = emb / (np.linalg.norm(emb) + 1e-8)
        all_scores = enrolled @ emb
        winner = int(np.argmax(all_scores))
        winner_counts[winner] += 1
        for idx, value in enumerate(all_scores):
            template_scores[idx].append(float(value))
        score = float(all_scores[winner])
        scores.append(score)
        quality = measure_face_quality(frame, face)
        qualities.append(float(quality.score))
        heights.append(float(quality.height_frac))
        offsets.append(float(quality.center_offset))

        if frame_no % 15 == 0:
            print(
                f"frame={frame_no:04d} score={score:.3f} quality={quality.score:.2f} "
                f"h={quality.height_frac:.2f} center={quality.center_offset:.2f} "
                f"samples={len(scores)}"
            )

        if args.preview:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 0), 2)
            cv2.putText(frame, f"score={score:.3f} q={quality.score:.2f}", (x1, max(20, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 0), 2)
            cv2.imshow("MajestyGuard recognition diagnostic", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()

    if not scores:
        print(f"No recognition samples. no_face={no_face} no_embedding={no_embedding}")
        return 2

    arr = np.array(scores, dtype=np.float32)
    quality_arr = np.array(qualities, dtype=np.float32)
    height_arr = np.array(heights, dtype=np.float32)
    offset_arr = np.array(offsets, dtype=np.float32)
    print()
    print("Recognition diagnostic summary")
    print(f"  posture: {posture}")
    print(f"  samples: {len(arr)}")
    print(f"  no_face_frames: {no_face}")
    print(f"  no_embedding_frames: {no_embedding}")
    print(f"  max: {float(np.max(arr)):.3f}")
    print(f"  p90: {float(np.percentile(arr, 90)):.3f}")
    print(f"  median: {float(np.median(arr)):.3f}")
    print(f"  p10: {float(np.percentile(arr, 10)):.3f}")
    print(f"  target max: {args.target:.3f}")
    print(
        "  quality: median=%.2f p10=%.2f face_h_median=%.2f center_median=%.2f"
        % (
            float(np.median(quality_arr)),
            float(np.percentile(quality_arr, 10)),
            float(np.median(height_arr)),
            float(np.median(offset_arr)),
        )
    )
    print("  template wins: " + ", ".join(
        f"{idx}:{count}" for idx, count in enumerate(winner_counts.tolist()) if count
    ))
    print("  top matched enrolled poses:")
    for row in _template_pose_report(
        winner_counts,
        template_scores,
        metadata,
        limit=max(1, int(args.top_templates)),
    ):
        print(_format_template_row(row))

    if float(np.max(arr)) < args.target:
        print("FAIL: max cosine similarity below target. Re-enroll with better lighting/framing.")
        return 1

    print("PASS: max cosine similarity reached target.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
