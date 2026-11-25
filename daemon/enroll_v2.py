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
import io
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
import math

import cv2
import numpy as np
from insightface.app import FaceAnalysis

sys.path.insert(0, os.path.dirname(__file__))
from diagnostic_common import enhance_frame as _enhance_frame
from face_quality import FaceQuality, measure_face_quality
from liveness_detector import LivenessDetector
try:
    from .enrollment_pose import enrollment_pose_aligned, friendly_readiness_message
except ImportError:
    from enrollment_pose import enrollment_pose_aligned, friendly_readiness_message

logging.basicConfig(level=logging.WARNING)

from diagnostic_common import resolve_resource_dir
_resolve_resource_dir = resolve_resource_dir
MODELS_DIR = resolve_resource_dir("models_insightface")
LIVENESS_MODELS_DIR = resolve_resource_dir("models")
DEFAULT_ENROLL_LIVENESS_THRESHOLD = 0.70


def _enrollment_liveness_threshold(env: Mapping[str, str] | None = None) -> float:
    """Allow stricter enrollment liveness, never a weaker RGB floor."""
    source = os.environ if env is None else env
    raw = source.get("MG_ENROLL_LIVENESS_THRESHOLD", str(DEFAULT_ENROLL_LIVENESS_THRESHOLD))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_ENROLL_LIVENESS_THRESHOLD
    if not DEFAULT_ENROLL_LIVENESS_THRESHOLD <= value <= 1.0:
        return DEFAULT_ENROLL_LIVENESS_THRESHOLD
    return value


ENROLL_LIVENESS_THRESHOLD = _enrollment_liveness_threshold()
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


class EnrollmentBurst(list):
    def __init__(self, samples=(), status: str = "") -> None:
        super().__init__(samples)
        self.status = status



def l2_normalize(vec: np.ndarray) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(arr))
    if norm < 1e-8 or not np.isfinite(norm):
        return np.zeros_like(arr)
    return arr / norm


def spherical_slerp(u: np.ndarray, v: np.ndarray, t: float) -> np.ndarray:
    u_unit = l2_normalize(u)
    v_unit = l2_normalize(v)
    t_clamped = float(np.clip(t, 0.0, 1.0))
    dot = float(np.clip(np.dot(u_unit, v_unit), -1.0, 1.0))
    if abs(dot) > 0.9995:
        return l2_normalize((1.0 - t_clamped) * u_unit + t_clamped * v_unit)
    omega = math.acos(dot)
    sin_omega = math.sin(omega)
    if abs(sin_omega) < 1e-6:
        return u_unit
    w_u = math.sin((1.0 - t_clamped) * omega) / sin_omega
    w_v = math.sin(t_clamped * omega) / sin_omega
    return l2_normalize(w_u * u_unit + w_v * v_unit)


def weiszfeld_spherical_median(
    embeddings: list[np.ndarray],
    weights: list[float] | None = None,
    max_iter: int = 15,
    tol: float = 1e-5,
) -> np.ndarray:
    if not embeddings:
        raise ValueError("Cannot compute spherical median of empty embedding list")
    unit_vecs: list[np.ndarray] = []
    valid_weights: list[float] = []
    first_dim = np.asarray(embeddings[0]).size

    for i, emb in enumerate(embeddings):
        arr = np.asarray(emb, dtype=np.float32).ravel()
        if arr.size != first_dim or not np.all(np.isfinite(arr)):
            continue
        u = l2_normalize(arr)
        if float(np.linalg.norm(u)) > 0.5:
            unit_vecs.append(u)
            w = float(weights[i]) if (weights and i < len(weights)) else 1.0
            valid_weights.append(max(0.05, w if math.isfinite(w) else 1.0))

    if not unit_vecs:
        return np.ones(first_dim, dtype=np.float32) / math.sqrt(first_dim)
    if len(unit_vecs) == 1:
        return unit_vecs[0]

    mat = np.stack(unit_vecs, axis=0)
    w_arr = np.asarray(valid_weights, dtype=np.float32)
    w_arr /= np.sum(w_arr)

    est = l2_normalize(np.sum(mat * w_arr[:, None], axis=0))
    for _ in range(max_iter):
        diffs = mat - est[None, :]
        dists = np.linalg.norm(diffs, axis=1)
        inv_dists = w_arr / np.maximum(dists, 1e-4)
        inv_dists /= np.sum(inv_dists)
        next_est = l2_normalize(np.sum(mat * inv_dists[:, None], axis=0))
        if float(np.linalg.norm(next_est - est)) < tol:
            est = next_est
            break
        est = next_est
    return est


def prune_outlier_embeddings(
    embeddings: list[np.ndarray],
    qualities: list[float] | None = None,
    min_consensus_cosine: float = 0.42,
) -> tuple[list[np.ndarray], list[float]]:
    if len(embeddings) <= 3:
        return list(embeddings), list(qualities or [0.85] * len(embeddings))
    quals = list(qualities) if (qualities and len(qualities) == len(embeddings)) else [0.85] * len(embeddings)
    median_unit = weiszfeld_spherical_median(embeddings, quals)
    sims = [float(np.dot(l2_normalize(e), median_unit)) for e in embeddings]
    kept_e: list[np.ndarray] = []
    kept_q: list[float] = []
    for e, q, s in zip(embeddings, quals, sims):
        if math.isfinite(s) and s >= min_consensus_cosine:
            kept_e.append(e)
            kept_q.append(q)
    if len(kept_e) < 3:
        order = sorted(range(len(sims)), key=lambda idx: sims[idx], reverse=True)[:3]
        kept_e = [embeddings[idx] for idx in order]
        kept_q = [quals[idx] for idx in order]
    return kept_e, kept_q


_LAST_POSE_TRACK: dict[str, float] = {"yaw": 0.0, "pitch": 0.0, "time": 0.0}


def _estimate_head_pose_and_vel(face: Any, frame_shape: tuple[int, ...]) -> tuple[float, float, float, float]:
    h = int(frame_shape[0]) if len(frame_shape) >= 2 else 480
    w = int(frame_shape[1]) if len(frame_shape) >= 2 else 640
    kps = getattr(face, "kps", None)
    if kps is None or len(kps) < 5:
        return 0.0, 0.0, 0.0, 0.0
    try:
        pts = [[float(p[0]), float(p[1])] for p in kps[:5]]
        left_eye, right_eye, nose, left_mouth, right_mouth = pts
        dy_eye = right_eye[1] - left_eye[1]
        dx_eye = max(1e-4, right_eye[0] - left_eye[0])
        roll = math.degrees(math.atan2(dy_eye, dx_eye))
        eye_mid_x = 0.5 * (left_eye[0] + right_eye[0])
        inter_ocular = math.hypot(dx_eye, dy_eye)
        yaw = ((nose[0] - eye_mid_x) / max(1.0, inter_ocular)) * 72.0
        eye_mid_y = 0.5 * (left_eye[1] + right_eye[1])
        mouth_mid_y = 0.5 * (left_mouth[1] + right_mouth[1])
        eye_to_mouth = max(1.0, mouth_mid_y - eye_mid_y)
        pitch = (0.56 - ((nose[1] - eye_mid_y) / eye_to_mouth)) * 140.0

        now = time.monotonic()
        prev_t = _LAST_POSE_TRACK["time"]
        dt = max(1e-3, now - prev_t)
        ang_vel = 0.0
        if 0.0 < (now - prev_t) < 0.6:
            ang_vel = math.hypot(yaw - _LAST_POSE_TRACK["yaw"], pitch - _LAST_POSE_TRACK["pitch"]) / dt
        _LAST_POSE_TRACK["yaw"] = yaw
        _LAST_POSE_TRACK["pitch"] = pitch
        _LAST_POSE_TRACK["time"] = now
        return yaw, pitch, roll, ang_vel
    except Exception:
        return 0.0, 0.0, 0.0, 0.0


def estimate_head_pose_3d(face: Any, frame_shape: tuple[int, ...]) -> tuple[float, float, float]:
    yaw, pitch, roll, _ = _estimate_head_pose_and_vel(face, frame_shape)
    return yaw, pitch, roll


def _single_enrollment_face(faces: list[Any]) -> Any | None:
    """Accept enrollment frames only when exactly one face is detected."""
    return faces[0] if len(faces) == 1 else None


def _warm_camera(cap, *, now=time.monotonic, sleep=time.sleep) -> None:
    # A blocking capture already paces the exposure warm-up. Only wait the
    # remaining portion of each 15 fps interval on fast/buffered backends.
    for _ in range(30):
        started = now()
        _, frame = cap.read()
        if isinstance(frame, np.ndarray):
            frame.fill(0)
        remaining = (1.0 / 15.0) - (now() - started)
        if remaining > 0:
            sleep(remaining)


def _schedule_studio_analysis(studio: Any, raw_frame: np.ndarray, analyzer: Any) -> None:
    """Queue analysis from an intact copy before the preview zeroes its input.

    The UI copies preview pixels into a QImage and then zeroes the NumPy buffer.
    Analysis must therefore receive its own copy first, otherwise the detector
    silently sees an all-black image while the preview looks perfectly normal.
    """
    analysis_frame = raw_frame.copy()
    try:
        studio.update_preview_frame(raw_frame)
    finally:
        raw_frame.fill(0)
    studio.analyze_live_frame_async(analysis_frame, analyzer)


def _face_readiness(
    frame: np.ndarray,
    face,
    min_quality: float = 0.62,
    angle: str = "",
) -> tuple[bool, str, FaceQuality]:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in face.bbox]
    quality = measure_face_quality(frame, face)
    height_frac = quality.height_frac
    det_score = float(getattr(face, "det_score", 0.0))
    face_center_x = (x1 + x2) / 2.0
    face_center_y = (y1 + y2) / 2.0
    center_dx = abs(face_center_x - w / 2.0) / max(1.0, w / 2.0)
    center_dy = abs(face_center_y - h / 2.0) / max(1.0, h / 2.0)

    low_angle = (angle or "").lower()
    # Laptop cameras often frame the owner around 28-32% of image height.
    # This is sufficient for the detector, embedding, quality, and liveness gates
    # that follow, so requiring 36% only makes normal desk setup needlessly fail.
    min_h = 0.26 if ("leaning back" in low_angle or "away" in low_angle) else 0.28
    max_h = 0.76 if ("closer" in low_angle or "close" in low_angle) else 0.68
    max_dx = 0.34 if any(k in low_angle for k in ("left", "right", "lying")) else 0.26
    max_dy = 0.34 if any(k in low_angle for k in ("up", "down", "lying")) else 0.28

    if det_score < 0.70:
        return False, f"det too low ({det_score:.2f})", quality
    if height_frac < min_h:
        return False, "move closer", quality
    if height_frac > max_h:
        return False, "move back slightly", quality
    if center_dx > max_dx or center_dy > max_dy:
        return False, "center your face", quality
    if quality.score < min_quality:
        return False, f"quality low ({quality.score:.2f})", quality
    if getattr(face, "normed_embedding", None) is None:
        return False, "embedding not ready", quality

    _, _, _, ang_vel = _estimate_head_pose_and_vel(face, frame.shape)
    if ang_vel > 55.0:
        return False, "hold still (motion blur)", quality

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
    liveness: LivenessDetector,
    enrollment_deadline: float | None = None,
) -> list[EnrollmentSample]:
    liveness.reset_session()
    accepted: list[EnrollmentSample] = []
    rejected = {
        "camera": 0,
        "no_face": 0,
        "multiple_faces": 0,
        "quality": 0,
        "pose": 0,
        "liveness": 0,
    }
    last_readiness_status = ""
    deadline = time.monotonic() + 3.0

    while (
        len(accepted) < samples_per_angle
        and time.monotonic() < deadline
        and not _enrollment_deadline_expired(enrollment_deadline)
    ):
        try:
            ret, raw_frame = cap.read()
        except Exception as exc:
            rejected["camera"] += 1
            if rejected["camera"] == 1:
                logging.getLogger(__name__).warning("Enrollment camera read failed: %s", exc)
            time.sleep(0.05)
            continue
        if not ret or raw_frame is None:
            rejected["camera"] += 1
            if isinstance(raw_frame, np.ndarray):
                raw_frame.fill(0)
            time.sleep(0.05)
            continue
        frame = None
        faces = []
        try:
            frame = _enhance_frame(raw_frame)
            faces = app.get(frame)
            face = _single_enrollment_face(faces)
            if face is None:
                rejected["multiple_faces" if len(faces) > 1 else "no_face"] += 1
                time.sleep(0.05)
                continue

            ready, readiness_status, quality = _face_readiness(
                frame, face, min_quality=min_quality, angle=angle
            )
            if not ready:
                rejected["quality"] += 1
                last_readiness_status = str(readiness_status)
                time.sleep(0.05)
                continue

            if not enrollment_pose_aligned(getattr(face, "kps", None), angle):
                rejected["pose"] += 1
                time.sleep(0.05)
                continue

            live_score = liveness.score_fast(frame, face)
            if live_score < ENROLL_LIVENESS_THRESHOLD:
                rejected["liveness"] += 1
                logging.getLogger(__name__).warning(
                    "Enrollment sample rejected: liveness %.2f below %.2f (angle=%s)",
                    live_score, ENROLL_LIVENESS_THRESHOLD, angle,
                )
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
        finally:
            for detected_face in faces:
                embedding = getattr(detected_face, "normed_embedding", None)
                if isinstance(embedding, np.ndarray):
                    embedding.fill(0)
            if isinstance(frame, np.ndarray) and frame is not raw_frame:
                frame[:] = 0
            if isinstance(raw_frame, np.ndarray):
                raw_frame[:] = 0
        time.sleep(0.12)

    partial_count = len(accepted)
    if partial_count >= samples_per_angle:
        status = "View captured."
    else:
        for sample in accepted:
            if isinstance(sample.embedding, np.ndarray):
                sample.embedding.fill(0)
        accepted.clear()
        if rejected["camera"]:
            status = (
                f"Camera stopped before enough clear samples were captured ({partial_count}/"
                f"{samples_per_angle}). Close other camera apps and retry."
            )
        elif rejected["liveness"]:
            status = "Could not confirm a live face. Keep your face uncovered and try again in even light."
        elif rejected["pose"]:
            status = "The view did not match the guide. Turn as shown and hold still."
        elif rejected["quality"]:
            status = friendly_readiness_message(last_readiness_status or "quality low")
        elif rejected["multiple_faces"]:
            status = "Only one person can enroll at a time."
        elif rejected["no_face"]:
            status = "Move into the camera frame."
        elif partial_count:
            status = f"Only {partial_count} of {samples_per_angle} clear samples were captured. Hold still and retry."
        else:
            status = "Camera did not provide a clear frame. Check the camera and retry."
    return EnrollmentBurst(accepted, status)


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
    import win32crypt

    protected = win32crypt.CryptProtectData(
        json.dumps(payload, indent=2).encode("utf-8"),
        "MajestyGuard enrollment metadata",
        None,
        None,
        None,
        0,
    )
    tmp_meta = OUT_META_FILE.with_suffix(".json.tmp")
    tmp_meta.write_bytes(protected)
    os.replace(tmp_meta, OUT_META_FILE)


def _backup_existing_artifacts() -> dict[Path, Path]:
    backups: dict[Path, Path] = {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for path in [OUT_FILE, OUT_META_FILE]:
        if not path.exists():
            continue
        backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
        data = path.read_bytes()
        import win32crypt

        try:
            win32crypt.CryptUnprotectData(data, None, None, None, 0)
        except Exception:
            description = (
                "MajestyGuard biometric gallery backup"
                if path == OUT_FILE
                else "MajestyGuard enrollment metadata backup"
            )
            data = win32crypt.CryptProtectData(data, description, None, None, None, 0)
        backup.write_bytes(data)
        shutil.copystat(path, backup)
        backups[path] = backup
    return backups


def _rollback_enrollment_artifacts(backups: dict[Path, Path]) -> list[str]:
    errors: list[str] = []
    for target in (OUT_FILE, OUT_META_FILE):
        restore_tmp = target.with_name(f"{target.name}.rollback-{os.getpid()}.tmp")
        try:
            backup = backups.get(target)
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                shutil.copy2(backup, restore_tmp)
                os.replace(restore_tmp, target)
        except OSError as exc:
            errors.append(f"{target}: {exc}")
        finally:
            restore_tmp.unlink(missing_ok=True)
    return errors


def _audit_event(event: str, **detail: Any) -> None:
    """Best effort: enrollment runs with the daemon stopped, so the chain has a single writer."""
    try:
        import audit_log

        audit_log.record(event, **detail)
    except Exception:
        pass


def _confirm_start_over_up_front() -> bool:
    """Start over replaces the owner, so ask Windows BEFORE the scan, not after minutes of it."""
    import enrollment_gate as gate

    if gate.prepare_enrollment(OUT_FILE):
        return True
    _, message = gate.refusal("reset_not_authorized")
    print(message)
    _audit_event("enroll_rejected", reason="reset_not_authorized")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", action="store_true", help="Force legacy OpenCV CLI window instead of Apple Face ID Studio")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--max-seconds", type=float, default=_env_max_seconds())
    parser.add_argument("--profile", choices=sorted(PROFILE_ANGLES), default="robust")
    parser.add_argument("--samples-per-angle", type=int, default=4)
    parser.add_argument("--max-templates", type=int, default=None)
    parser.add_argument("--min-cohesion", type=float, default=0.45)
    parser.add_argument("--duplicate-similarity", type=float, default=0.985)
    parser.add_argument("--min-quality", type=float, default=0.62)
    args = parser.parse_args()
    if not _confirm_start_over_up_front():
        return 1
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

    print("Loading liveness detector...")
    liveness = LivenessDetector(model_dir=str(LIVENESS_MODELS_DIR))
    print("Liveness detector loaded.")
    print()

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        cap.release()
        liveness.close()
        print(f"Could not open camera {args.camera}.")
        return 1

    def abort_with_cleanup(message: str) -> int:
        print(message)
        cap.release()
        cv2.destroyAllWindows()
        try:
            liveness.close()
        except Exception:
            pass
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    print("Warming camera for 2 seconds...")
    _warm_camera(cap)
    print("Ready.")
    print()

    samples: list[EnrollmentSample] = []
    enrollment_deadline = _deadline_from_max_seconds(max_seconds)

    use_studio_window = False
    if not getattr(args, "cli", False):
        try:
            ui_dir = _resolve_resource_dir("ui")
            if str(ui_dir) not in sys.path:
                sys.path.insert(0, str(ui_dir))
            import importlib
            _qwidgets = importlib.import_module("PyQt6.QtWidgets")
            _qcore = importlib.import_module("PyQt6.QtCore")
            QApplication = getattr(_qwidgets, "QApplication")
            QTimer = getattr(_qcore, "QTimer")
            from enrollment_window import EnrollmentStudioWindow
            use_studio_window = True
        except Exception as e:
            print(f"EnrollmentStudioWindow unavailable ({e}), using OpenCV fallback")
            use_studio_window = False

    if use_studio_window:
        app_qt = QApplication.instance() or QApplication(sys.argv)

        def _do_burst(angle_name: str, angle_idx: int) -> list[EnrollmentSample]:
            return _capture_burst(
                cap,
                app,
                angle_name,
                angle_idx,
                samples_per_angle,
                min_quality,
                liveness,
                enrollment_deadline,
            )

        def _on_switch_profile(new_profile: str) -> list[str]:
            nonlocal angles, max_templates
            updated = _angles_for_profile(new_profile)
            args.profile = new_profile
            angles = updated
            req_max = (
                _default_max_templates_for_profile(new_profile, len(angles), samples_per_angle)
                if args.max_templates is None
                else args.max_templates
            )
            max_templates = max(1, min(req_max, len(angles) * samples_per_angle))
            return updated

        studio = EnrollmentStudioWindow(
            angles=angles,
            profile=args.profile,
            samples_per_angle=samples_per_angle,
            on_capture_burst=_do_burst,
            on_switch_profile=_on_switch_profile,
        )

        poll_timer = QTimer()

        def _on_studio_tick():
            if studio.completed or studio.aborted:
                poll_timer.stop()
                app_qt.quit()
                return

            if _enrollment_deadline_expired(enrollment_deadline):
                studio.abort("deadline")
                if not studio.capture_in_progress and not studio.analysis_in_progress:
                    poll_timer.stop()
                    app_qt.quit()
                return

            if studio.capture_in_progress:
                return

            if studio.analysis_in_progress:
                ret, discarded_frame = cap.read()
                if ret and discarded_frame is not None:
                    studio.update_preview_frame(discarded_frame)
                    discarded_frame[:] = 0
                return

            ret, raw_frame = cap.read()
            if not ret or raw_frame is None:
                return
            angle = studio.current_angle()
            try:
                def _analyze_live_frame(analysis_frame):
                    processed = _enhance_frame(analysis_frame)
                    try:
                        faces = app.get(processed)
                        face = _single_enrollment_face(faces)
                        if face is not None:
                            ready, status, quality = _face_readiness(
                                processed,
                                face,
                                min_quality=min_quality,
                                angle=angle,
                            )
                            return face, ready, status, quality.score
                        status = "one face only" if len(faces) > 1 else "no face"
                        return None, False, status, 0.0
                    finally:
                        if processed is not analysis_frame:
                            processed[:] = 0

                _schedule_studio_analysis(studio, raw_frame, _analyze_live_frame)
            except Exception:
                raw_frame.fill(0)
                raise

            if studio.completed or studio.aborted:
                poll_timer.stop()
                app_qt.quit()

        poll_timer.timeout.connect(_on_studio_tick)
        poll_timer.start(30)
        studio.show()
        app_qt.exec()

        if not studio.completed or studio.aborted:
            reason = studio.abort_reason
            messages = {
                "cancel_button": "Enrollment canceled by the user; nothing was saved.",
                "escape": "Enrollment canceled with Escape; nothing was saved.",
                "window_closed": "Enrollment window closed before completion; nothing was saved.",
                "deadline": "Enrollment timed out before completion; nothing was saved.",
            }
            message = messages.get(reason, "Enrollment stopped before completion; nothing was saved.")
            return abort_with_cleanup(message)
        samples = studio.captured_samples
        cap.release()
        cv2.destroyAllWindows()
    else:
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
                face = _single_enrollment_face(faces)
                display = frame.copy()
                status = "one face only" if len(faces) > 1 else "no face"
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
                        liveness,
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
    try:
        liveness.close()
    except Exception:
        pass

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

    import enrollment_gate as _gate

    verdict = _gate.check_continuity(arr, OUT_FILE)
    if not verdict.allowed:
        outcome, message = _gate.refusal(verdict.reason)
        print(message)
        _gate.write_result(OUT_DIR, outcome, message)
        _audit_event("enroll_rejected", reason=verdict.reason)
        return 1
    if verdict.reason == "continuity_waived":
        print("Starting over: the previous profile will be replaced (identity check waived after Windows re-authentication).")

    backups = _backup_existing_artifacts()
    for original, backup in backups.items():
        print(f"Backed up {original.name} to {backup}")
    buf = io.BytesIO()
    np.save(buf, arr, allow_pickle=False)
    tmp_out = OUT_FILE.with_suffix(".npy.tmp")
    try:
        import win32crypt
        encrypted = win32crypt.CryptProtectData(
            buf.getvalue(), "MajestyGuard biometric embeddings", None, None, None, 0
        )
        tmp_out.write_bytes(encrypted)
        os.replace(tmp_out, OUT_FILE)
        print(f"Wrote DPAPI-encrypted embeddings to {OUT_FILE}")
    except Exception as e:
        if os.environ.get("MG_ALLOW_PLAINTEXT_EMBEDDINGS", "0") == "1":
            print(f"WARNING: DPAPI encryption failed ({e}); writing plaintext (MG_ALLOW_PLAINTEXT_EMBEDDINGS=1)")
            tmp_out.write_bytes(buf.getvalue())
            os.replace(tmp_out, OUT_FILE)
        else:
            print(f"ERROR: DPAPI encryption failed ({e}) — refusing to write plaintext biometric gallery.")
            return 1
    try:
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
    except Exception as exc:
        print(f"ERROR: Could not write enrollment metadata ({exc}); rolling artifacts back.")
        rollback_errors = _rollback_enrollment_artifacts(backups)
        if rollback_errors:
            print("ERROR: Enrollment rollback was incomplete:")
            for error in rollback_errors:
                print(f"  - {error}")
        else:
            print("Enrollment artifacts restored to their prior state.")
        return 1
    _gate.prune_backups([OUT_FILE, OUT_META_FILE])
    _gate.write_result(OUT_DIR, "ok", f"{len(arr)} templates")
    _audit_event("enroll_completed", templates=len(arr), identity=verdict.reason)
    print("Enrollment complete.")
    print(f"Captured {len(samples)} embeddings; selected {len(arr)} gallery templates.")
    print(f"Saved gallery to {OUT_FILE}")
    print(f"Saved metadata to {OUT_META_FILE}")
    print("Next: python daemon/mg_recog_diag.py --seconds 30")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
