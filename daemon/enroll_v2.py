"""
MajestyGuard v2 enrollment script.

Captures a multi-view owner gallery using the same InsightFace model family as
the daemon and saves embeddings to %LOCALAPPDATA%\\MajestyGuard\\embeddings_v2.npy.

Run with:
    python daemon/enroll_v2.py

Tips:
    - Use bright, even lighting.
    - Sit centered and close enough that the face box is roughly 40-60% of
      the preview height.
    - Press SPACE only when the preview says READY.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional

import cv2
import numpy as np
from insightface.app import FaceAnalysis

sys.path.insert(0, os.path.dirname(__file__))
from diagnostic_common import enhance_frame as _enhance_frame, select_primary_face as _select_primary_face
from face_quality import FaceQuality, measure_face_quality

logging.basicConfig(level=logging.WARNING)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models_insightface"
OUT_DIR = Path(os.environ.get("LOCALAPPDATA", os.environ.get("ProgramData", r"C:\ProgramData"))) / "MajestyGuard"
OUT_FILE = OUT_DIR / "embeddings_v2.npy"
OUT_META_FILE = OUT_DIR / "embeddings_v2_meta.json"
QUICK_ANGLES = [
    "Front - look straight at camera",
    "Slight left - turn head about 15 degrees left",
    "Slight right - turn head about 15 degrees right",
    "Slight up - raise chin slightly",
    "Slight down - lower chin slightly",
    "Natural variation - normal posture / glasses if you use them",
]
ROBUST_ANGLES = [
    "Front - neutral expression",
    "Front - slight smile / normal expression",
    "Left 10 degrees - small turn",
    "Right 10 degrees - small turn",
    "Left 25 degrees - stronger turn",
    "Right 25 degrees - stronger turn",
    "Chin slightly up",
    "Chin slightly down",
    "Left-up diagonal",
    "Right-up diagonal",
    "Closer normal posture - face fills preview but not cropped",
    "Natural working posture / glasses if you use them",
]
REAL_LIFE_ANGLES = ROBUST_ANGLES + [
    "Leaning back / away - relaxed real working posture",
    "Night lying down - relaxed face straight to webcam",
    "Night lying down - head tilted left on pillow",
    "Night lying down - head tilted right on pillow",
    "Night close relaxed face - fills preview but not cropped",
    "Night screen-lit face - normal study posture",
    "Night shadowed face - small natural movement",
]
PROFILE_ANGLES = {
    "quick": QUICK_ANGLES,
    "robust": ROBUST_ANGLES,
    "real-life": REAL_LIFE_ANGLES,
}
PROFILE_DEFAULT_MAX_TEMPLATES = {
    "quick": 18,
    "robust": 40,
    "real-life": 60,
}

OUT_DIR.mkdir(parents=True, exist_ok=True)
MIN_SELECTED_PER_ANGLE = 2
SELECTION_POLICY = "pose_coverage_then_quality_diversity"


def _env_max_seconds(env: Mapping[str, str] | None = None) -> float:
    env = os.environ if env is None else env
    raw = env.get("MG_MAX_SECONDS")
    if raw is None or raw.strip() == "":
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    return value if value > 0.0 else 0.0


def _deadline_from_max_seconds(max_seconds: float, now: float | None = None) -> float | None:
    if max_seconds <= 0.0:
        return None
    if now is None:
        now = time.monotonic()
    return now + max_seconds


def _enrollment_deadline_expired(deadline: float | None, now: float | None = None) -> bool:
    if deadline is None:
        return False
    if now is None:
        now = time.monotonic()
    return now >= deadline


def _angles_for_profile(profile: str) -> list[str]:
    try:
        return list(PROFILE_ANGLES[profile])
    except KeyError as exc:
        raise ValueError(f"Unknown enrollment profile: {profile}") from exc


def _default_max_templates_for_profile(profile: str, angle_count: int, samples_per_angle: int) -> int:
    default = PROFILE_DEFAULT_MAX_TEMPLATES.get(profile, 40)
    return max(1, min(default, max(1, angle_count) * max(1, samples_per_angle)))


@dataclass
class EnrollmentSample:
    embedding: np.ndarray
    angle: str
    angle_index: int
    sample_index: int
    det_score: float
    height_frac: float
    quality: float
    center_offset: float = 0.0
    sharpness: float = 0.0
    illumination: float = 0.0
    cohesion: float = 0.0
    selected: bool = False

    def metadata(self) -> dict:
        data = asdict(self)
        data.pop("embedding", None)
        return data


def _face_readiness(frame: np.ndarray, face, min_quality: float = 0.62) -> tuple[bool, str, FaceQuality]:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in face.bbox]
    box_w = max(0.0, x2 - x1)
    box_h = max(0.0, y2 - y1)
    quality = measure_face_quality(frame, face)
    height_frac = quality.height_frac
    det_score = float(getattr(face, "det_score", 0.0))
    face_center_x = (x1 + x2) / 2.0
    face_center_y = (y1 + y2) / 2.0
    center_dx = abs(face_center_x - w / 2.0) / max(1.0, w / 2.0)
    center_dy = abs(face_center_y - h / 2.0) / max(1.0, h / 2.0)

    if det_score < 0.70:
        return False, f"det too low ({det_score:.2f})", quality
    if height_frac < 0.38:
        return False, "move closer", quality
    if height_frac > 0.68:
        return False, "move back slightly", quality
    if center_dx > 0.25 or center_dy > 0.28:
        return False, "center your face", quality
    if quality.score < min_quality:
        return False, f"quality low ({quality.score:.2f})", quality
    if getattr(face, "normed_embedding", None) is None:
        return False, "embedding not ready", quality
    return True, "READY", quality


def _sample_quality(quality: FaceQuality) -> float:
    pose_size_score = float(np.clip(1.0 - abs(quality.height_frac - 0.50) / 0.30, 0.0, 1.0))
    return float(quality.score * 0.75 + pose_size_score * 0.25)


def _capture_burst(
    cap,
    app: FaceAnalysis,
    angle: str,
    angle_index: int,
    samples_per_angle: int,
    min_quality: float,
    enrollment_deadline: float | None = None,
) -> list[EnrollmentSample]:
    accepted: list[EnrollmentSample] = []
    deadline = time.monotonic() + 3.0

    while (
        len(accepted) < samples_per_angle
        and time.monotonic() < deadline
        and not _enrollment_deadline_expired(enrollment_deadline)
    ):
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        frame = _enhance_frame(frame)
        face = _select_primary_face(frame, app.get(frame))
        if face is None:
            time.sleep(0.05)
            continue

        ready, _, quality = _face_readiness(frame, face, min_quality=min_quality)
        if not ready:
            time.sleep(0.05)
            continue

        emb = face.normed_embedding.astype(np.float32)
        emb = emb / (np.linalg.norm(emb) + 1e-8)
        det_score = float(getattr(face, "det_score", 0.0))
        accepted.append(
            EnrollmentSample(
                embedding=emb.copy(),
                angle=angle,
                angle_index=angle_index,
                sample_index=len(accepted) + 1,
                det_score=det_score,
                height_frac=float(quality.height_frac),
                quality=_sample_quality(quality),
                center_offset=float(quality.center_offset),
                sharpness=float(quality.sharpness),
                illumination=float(quality.illumination_mean),
            )
        )
        time.sleep(0.12)

    return accepted


def _normalize_matrix(samples: list[EnrollmentSample]) -> np.ndarray:
    arr = np.stack([sample.embedding.astype(np.float32) for sample in samples], axis=0)
    norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
    arr = arr / norms
    for idx, sample in enumerate(samples):
        sample.embedding = arr[idx].copy()
    return arr


def _compute_cohesion(samples: list[EnrollmentSample], top_k: int = 3) -> np.ndarray:
    if len(samples) <= 1:
        cohesion = np.ones(len(samples), dtype=np.float32)
    else:
        arr = _normalize_matrix(samples)
        sims = arr @ arr.T
        np.fill_diagonal(sims, -1.0)
        k = min(top_k, max(1, len(samples) - 1))
        cohesion = np.mean(np.sort(sims, axis=1)[:, -k:], axis=1).astype(np.float32)

    for sample, value in zip(samples, cohesion.tolist()):
        sample.cohesion = float(value)
    return cohesion


def _select_gallery(
    samples: list[EnrollmentSample],
    max_templates: int,
    min_cohesion: float,
    duplicate_similarity: float,
) -> list[EnrollmentSample]:
    if not samples:
        return []

    _compute_cohesion(samples)
    if len(samples) >= 6:
        candidates = [sample for sample in samples if sample.cohesion >= min_cohesion]
        min_viable = min(4, len(samples))
        if len(candidates) < min_viable:
            candidates = sorted(samples, key=lambda sample: sample.cohesion, reverse=True)[:min_viable]
    else:
        candidates = list(samples)

    _normalize_matrix(candidates)
    limit = max(1, min(max_templates, len(candidates)))
    selected_indices: list[int] = []

    def pick_best(pool: list[int]) -> Optional[int]:
        best_rank = None
        best_idx = None
        for idx in pool:
            if idx in selected_indices:
                continue
            sims = [
                float(np.dot(candidates[idx].embedding, candidates[selected_idx].embedding))
                for selected_idx in selected_indices
            ]
            max_sim = max(sims) if sims else 0.0
            if sims and max_sim >= duplicate_similarity:
                continue
            diversity = 1.0 - max_sim
            rank = candidates[idx].quality * 0.65 + diversity * 0.35
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_idx = idx
        return best_idx

    angle_groups: dict[int, list[int]] = {}
    for idx, sample in enumerate(candidates):
        angle_groups.setdefault(sample.angle_index, []).append(idx)
    for group in angle_groups.values():
        group.sort(key=lambda idx: candidates[idx].quality, reverse=True)

    if len(angle_groups) > 1 and limit >= len(angle_groups):
        per_pose_floor = max(1, limit // len(angle_groups))
        for _ in range(per_pose_floor):
            for angle_index in sorted(angle_groups):
                if len(selected_indices) >= limit:
                    break
                chosen = next(
                    (idx for idx in angle_groups[angle_index] if idx not in selected_indices),
                    None,
                )
                if chosen is not None:
                    selected_indices.append(chosen)

    while len(selected_indices) < limit:
        remaining = [idx for idx in range(len(candidates)) if idx not in selected_indices]
        if not remaining:
            break
        chosen = pick_best(remaining)
        if chosen is None:
            break
        selected_indices.append(chosen)

    if len(selected_indices) < limit:
        for idx in sorted(range(len(candidates)), key=lambda i: candidates[i].quality, reverse=True):
            if idx not in selected_indices:
                selected_indices.append(idx)
            if len(selected_indices) >= limit:
                break

    selected = [candidates[idx] for idx in selected_indices[:limit]]
    for sample in samples:
        sample.selected = False
    for sample in selected:
        sample.selected = True
    return selected


def _min_selected_per_angle_for_budget(angle_count: int, max_templates: int, requested_floor: int = MIN_SELECTED_PER_ANGLE) -> int:
    if angle_count <= 0 or max_templates <= 0:
        return 1
    if max_templates < angle_count * requested_floor:
        return 1
    return max(1, requested_floor)


def _selected_pose_counts(selected: list[EnrollmentSample], angles: list[str]) -> dict[str, int]:
    counts = {angle: 0 for angle in angles}
    for sample in selected:
        if sample.angle in counts:
            counts[sample.angle] += 1
    return counts


def _undercovered_selected_angles(
    selected: list[EnrollmentSample],
    angles: list[str],
    min_selected_per_angle: int,
) -> dict[str, int]:
    counts = _selected_pose_counts(selected, angles)
    return {
        angle: count
        for angle, count in counts.items()
        if count < min_selected_per_angle
    }


def _write_metadata(
    *,
    profile: str,
    angles: list[str],
    samples_per_angle: int,
    all_samples: list[EnrollmentSample],
    selected: list[EnrollmentSample],
    max_templates: int,
    min_cohesion: float,
    duplicate_similarity: float,
    min_quality: float,
    min_selected_per_angle: int,
) -> None:
    payload = {
        "version": 5,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "profile": profile,
        "angles": angles,
        "samples_per_angle": samples_per_angle,
        "captured_count": len(all_samples),
        "selected_count": len(selected),
        "max_templates": max_templates,
        "min_angle_count": len(angles),
        "min_cohesion": min_cohesion,
        "duplicate_similarity": duplicate_similarity,
        "min_quality": min_quality,
        "min_selected_per_angle": min_selected_per_angle,
        "selection_policy": SELECTION_POLICY,
        "selected_samples": [sample.metadata() for sample in selected],
        "all_samples": [sample.metadata() for sample in all_samples],
    }
    OUT_META_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _backup_existing_artifacts() -> list[Path]:
    backups: list[Path] = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for path in [OUT_FILE, OUT_META_FILE]:
        if not path.exists():
            continue
        backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
        shutil.copy2(path, backup)
        backups.append(backup)
    return backups


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--max-seconds", type=float, default=_env_max_seconds())
    parser.add_argument("--profile", choices=sorted(PROFILE_ANGLES), default="robust")
    parser.add_argument("--samples-per-angle", type=int, default=4)
    parser.add_argument("--max-templates", type=int, default=None)
    parser.add_argument("--min-cohesion", type=float, default=0.45)
    parser.add_argument("--duplicate-similarity", type=float, default=0.985)
    parser.add_argument("--min-quality", type=float, default=0.62)
    args = parser.parse_args()
    samples_per_angle = max(1, min(args.samples_per_angle, 6))
    angles = _angles_for_profile(args.profile)
    requested_max_templates = (
        _default_max_templates_for_profile(args.profile, len(angles), samples_per_angle)
        if args.max_templates is None
        else args.max_templates
    )
    max_templates = max(1, min(requested_max_templates, len(angles) * samples_per_angle))
    min_cohesion = float(np.clip(args.min_cohesion, 0.0, 1.0))
    duplicate_similarity = float(np.clip(args.duplicate_similarity, 0.50, 1.0))
    min_quality = float(np.clip(args.min_quality, 0.0, 1.0))
    max_seconds = max(0.0, float(args.max_seconds))

    print("MajestyGuard v2 Enrollment")
    print("=" * 40)
    print(f"Models: {MODELS_DIR}")
    print(f"Output: {OUT_FILE}")
    print(f"Metadata: {OUT_META_FILE}")
    print(f"Profile: {args.profile} ({len(angles)} angles)")
    print(f"Samples per angle: {samples_per_angle}")
    print(f"Max templates after pruning: {max_templates}")
    print(f"Minimum frame quality: {min_quality:.2f}")
    if max_seconds > 0.0:
        print(f"Safety bound: auto-abort live enrollment after {max_seconds:.1f} seconds.")
    else:
        print("Safety bound: unbounded enrollment wait; set --max-seconds or MG_MAX_SECONDS for unattended runs.")
    print()
    print("This will overwrite embeddings_v2.npy only after all angle bursts complete.")
    if args.profile == "real-life":
        print("Use the same night lighting and real posture you want MajestyGuard to tolerate.")
    else:
        print("Use good lighting and keep your face centered.")
    print()

    print("Loading InsightFace...")
    app = FaceAnalysis(
        name="buffalo_l",
        root=str(MODELS_DIR),
        providers=["CPUExecutionProvider"],
        allowed_modules=["detection", "recognition"],
    )
    app.prepare(ctx_id=0, det_size=(320, 320))
    print("Model loaded.")
    print()

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Could not open camera {args.camera}.")
        return 1

    def abort_with_cleanup(message: str) -> int:
        print(message)
        cap.release()
        cv2.destroyAllWindows()
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    print("Warming camera for 2 seconds...")
    for _ in range(30):
        cap.read()
        time.sleep(0.05)
    print("Ready.")
    print()

    samples: list[EnrollmentSample] = []
    enrollment_deadline = _deadline_from_max_seconds(max_seconds)

    for i, angle in enumerate(angles, start=1):
        if _enrollment_deadline_expired(enrollment_deadline):
            return abort_with_cleanup("Enrollment timed out before all poses were captured; nothing was saved.")

        print(f"Angle {i}/{len(angles)}: {angle}")
        print(f"  Press SPACE when READY to capture {samples_per_angle} samples, or Q to quit.")
        print()

        captured = False
        while not captured:
            if _enrollment_deadline_expired(enrollment_deadline):
                return abort_with_cleanup("Enrollment timed out before all poses were captured; nothing was saved.")

            ret, raw_frame = cap.read()
            if not ret or raw_frame is None:
                continue
            frame = _enhance_frame(raw_frame)

            faces = app.get(frame)
            face = _select_primary_face(frame, faces)
            display = frame.copy()
            status = "no face"
            ready = False
            height_frac = 0.0

            if face is not None:
                x1, y1, x2, y2 = [int(v) for v in face.bbox]
                ready, status, quality = _face_readiness(frame, face, min_quality=min_quality)
                color = (0, 220, 0) if ready else (0, 165, 255)
                cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    display,
                    f"{status} q={quality.score:.2f} det={face.det_score:.2f} h={quality.height_frac:.2f}",
                    (max(0, x1), max(20, y1 - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    color,
                    1,
                )

            label = f"{i}/{len(angles)} {angle} | SPACE=burst Q=quit"
            cv2.putText(display, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)
            cv2.putText(display, status, (10, 455), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1)
            cv2.imshow("MajestyGuard Enrollment", display)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("Aborted.")
                cap.release()
                cv2.destroyAllWindows()
                return 1

            if key == ord(" "):
                if face is None:
                    print("  No usable face detected. Reposition and try again.")
                    continue

                ready, status, quality = _face_readiness(frame, face, min_quality=min_quality)
                if not ready:
                    print(f"  Not ready: {status}. Reposition and try again.")
                    continue

                burst = _capture_burst(
                    cap,
                    app,
                    angle,
                    i,
                    samples_per_angle,
                    min_quality,
                    enrollment_deadline,
                )
                if _enrollment_deadline_expired(enrollment_deadline):
                    return abort_with_cleanup("Enrollment timed out before all poses were captured; nothing was saved.")
                if len(burst) < samples_per_angle:
                    print(
                        f"  Only captured {len(burst)}/{samples_per_angle} good samples. "
                        "Reposition and try again."
                    )
                    continue

                samples.extend(burst)
                print(
                    f"  CAPTURED {len(burst)} samples "
                    f"det_score={face.det_score:.3f} height={quality.height_frac:.2f} "
                    f"quality={quality.score:.2f}"
                )
                print()
                captured = True
                time.sleep(0.3)

    cap.release()
    cv2.destroyAllWindows()

    expected = len(angles) * samples_per_angle
    if len(samples) != expected:
        print(f"Only {len(samples)} embeddings captured; expected {expected}.")
        return 1

    selected = _select_gallery(samples, max_templates, min_cohesion, duplicate_similarity)
    if not selected:
        print("No valid enrollment samples remained after pruning.")
        return 1

    min_selected_per_angle = _min_selected_per_angle_for_budget(len(angles), max_templates)
    undercovered = _undercovered_selected_angles(selected, angles, min_selected_per_angle)
    if undercovered:
        print("Selected gallery is undercovered after pruning; enrollment was not saved.")
        print(f"Minimum selected templates per captured angle: {min_selected_per_angle}")
        for angle, count in undercovered.items():
            print(f"  - {angle}: {count}")
        print("Try better lighting/framing or increase --max-templates.")
        return 1

    arr = np.stack([sample.embedding.astype(np.float32) for sample in selected], axis=0)
    arr = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8)

    backups = _backup_existing_artifacts()
    for backup in backups:
        print(f"Backed up previous artifact to {backup}")
    np.save(str(OUT_FILE), arr)
    _write_metadata(
        profile=args.profile,
        angles=angles,
        samples_per_angle=samples_per_angle,
        all_samples=samples,
        selected=selected,
        max_templates=max_templates,
        min_cohesion=min_cohesion,
        duplicate_similarity=duplicate_similarity,
        min_quality=min_quality,
        min_selected_per_angle=min_selected_per_angle,
    )
    print("Enrollment complete.")
    print(f"Captured {len(samples)} embeddings; selected {len(arr)} gallery templates.")
    print(f"Saved gallery to {OUT_FILE}")
    print(f"Saved metadata to {OUT_META_FILE}")
    print("Next: python daemon/mg_recog_diag.py --seconds 30")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
