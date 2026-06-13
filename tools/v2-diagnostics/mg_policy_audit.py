"""
Offline MajestyGuard policy sanity check.

This script reads environment-style settings only. It does not import the
daemon, open the camera, start IPC, call lock APIs, or touch machine state.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True)
class PolicyCheck:
    name: str
    ok: bool
    message: str


@dataclass(frozen=True)
class PolicyAudit:
    lock_enabled: bool
    whcdf_ipc_enabled: bool
    adaface_flip_fusion_enabled: bool
    max_frames: int
    max_seconds: float
    thresholds: dict[str, float]
    checks: list[PolicyCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def _env_bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _env_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


def _check(name: str, ok: bool, message: str) -> PolicyCheck:
    return PolicyCheck(name=name, ok=ok, message=message)


def audit_policy(
    env: Mapping[str, str] | None = None,
    *,
    allow_lock_enabled: bool = False,
    allow_whcdf_ipc: bool = False,
    require_bound: bool = False,
) -> PolicyAudit:
    env = os.environ if env is None else env
    lock_enabled = _env_bool(env, "MG_ENABLE_LOCK")
    whcdf_ipc_enabled = _env_bool(env, "MG_ENABLE_WHCDF_IPC")
    adaface_flip_fusion_enabled = _env_bool(env, "MG_ADAFACE_FLIP_FUSION", True)
    max_frames = _env_int(env, "MG_MAX_FRAMES", 0, 0)
    max_seconds = _env_float(env, "MG_MAX_SECONDS", 0.0, 0.0, 86400.0)
    thresholds = {
        "recognition": _env_float(env, "MG_RECOGNITION_THRESHOLD", 0.78),
        "active_recognition": _env_float(env, "MG_ACTIVE_RECOGNITION_THRESHOLD", 0.65),
        "stranger_score": _env_float(env, "MG_STRANGER_SCORE_THRESHOLD", 0.55),
        "stranger_max_smoothed": _env_float(env, "MG_STRANGER_MAX_SMOOTHED_SCORE", 0.58),
        "active_continuity_smooth": _env_float(env, "MG_ACTIVE_CONTINUITY_SMOOTH_THRESHOLD", 0.60),
        "active_continuity_track_min": _env_float(env, "MG_ACTIVE_CONTINUITY_TRACK_MIN_SCORE", 0.35),
        "presence_track_floor": _env_float(env, "MG_PRESENCE_TRACK_FLOOR", 0.65),
        "presence_track_min_score": _env_float(env, "MG_PRESENCE_TRACK_MIN_SCORE", 0.35),
        "presence_confidence_max_boost": _env_float(env, "MG_PRESENCE_CONFIDENCE_MAX_BOOST", 0.25, 0.0, 0.4),
        "presence_min_quality": _env_float(env, "MG_PRESENCE_MIN_QUALITY", 0.55),
        "liveness": _env_float(env, "MG_LIVENESS_THRESHOLD", 0.70),
        "active_liveness_jitter_floor": _env_float(env, "MG_ACTIVE_LIVENESS_JITTER_FLOOR", 0.55),
        "stranger_min_frame_quality": _env_float(env, "MG_STRANGER_MIN_FRAME_QUALITY", 0.42),
        "scanning_owner_ambiguity_grace_frames": _env_int(env, "MG_SCANNING_OWNER_AMBIGUITY_GRACE_FRAMES", 15, 0),
        "scanning_owner_ambiguity_min_score": _env_float(env, "MG_SCANNING_OWNER_AMBIGUITY_MIN_SCORE", 0.50),
        "scanning_owner_ambiguity_presence": _env_float(env, "MG_SCANNING_OWNER_AMBIGUITY_PRESENCE", 0.65),
    }
    no_face_reset = _env_int(env, "MG_NO_FACE_LIVENESS_RESET_FRAMES", 5, 1)
    absent_frames_lock = 75
    stranger_confirm_scanning = 3

    checks = [
        _check(
            "lock_disabled",
            allow_lock_enabled or not lock_enabled,
            "MG_ENABLE_LOCK must stay off outside an explicit recovery-planned lock test.",
        ),
        _check(
            "whcdf_ipc_disabled",
            allow_whcdf_ipc or not whcdf_ipc_enabled,
            "MG_ENABLE_WHCDF_IPC must stay off until WHCDF caller/key handling is secure.",
        ),
        _check(
            "adaface_flip_fusion_enabled",
            adaface_flip_fusion_enabled,
            "MG_ADAFACE_FLIP_FUSION should stay enabled for expression/head-turn robustness.",
        ),
        _check(
            "identity_threshold_order",
            thresholds["recognition"] > thresholds["active_recognition"] > thresholds["stranger_score"],
            "Identity thresholds must remain recognition > active maintenance > stranger evidence.",
        ),
        _check(
            "continuity_smoothing_buffer",
            thresholds["active_continuity_smooth"] > thresholds["stranger_max_smoothed"],
            "Owner-continuity hold should require stronger smoothing than stranger dampening.",
        ),
        _check(
            "track_hold_below_stranger_floor",
            thresholds["active_continuity_track_min"] < thresholds["stranger_score"],
            "Owner-track hold floor should stay below stranger evidence floor.",
        ),
        _check(
            "liveness_separate_from_identity",
            thresholds["liveness"] < thresholds["recognition"],
            "RGB liveness threshold must stay separate from the stricter identity threshold.",
        ),
        _check(
            "active_liveness_jitter_below_liveness",
            thresholds["active_liveness_jitter_floor"] < thresholds["liveness"],
            "Owner-continuity liveness jitter floor must stay below the normal liveness pass threshold.",
        ),
        _check(
            "active_liveness_jitter_not_too_low",
            thresholds["active_liveness_jitter_floor"] >= 0.50,
            "Owner-continuity liveness jitter floor must not hide hard liveness failures.",
        ),
        _check(
            "presence_confidence_below_unlock",
            thresholds["presence_track_floor"] < thresholds["recognition"],
            "Presence confidence must stay below unlock-grade identity threshold.",
        ),
        _check(
            "presence_confidence_reaches_active",
            thresholds["presence_track_floor"] >= thresholds["active_recognition"],
            "Tracked-owner presence floor should be high enough to hold ACTIVE.",
        ),
        _check(
            "presence_track_floor_below_stranger",
            thresholds["presence_track_min_score"] < thresholds["stranger_score"],
            "Presence track minimum should remain below stranger evidence floor.",
        ),
        _check(
            "liveness_reset_before_absent_lock",
            no_face_reset < absent_frames_lock,
            "Liveness/FaceState should reset before absent lock timing is reached.",
        ),
        _check(
            "scanning_ambiguity_grace_covers_confirm",
            thresholds["scanning_owner_ambiguity_grace_frames"] >= stranger_confirm_scanning,
            "Scanning owner-ambiguity grace must cover the stranger confirmation window.",
        ),
        _check(
            "scanning_ambiguity_score_below_stranger",
            thresholds["scanning_owner_ambiguity_min_score"] < thresholds["stranger_score"],
            "Multi-face ambiguity score floor must stay below the stranger evidence threshold.",
        ),
        _check(
            "scanning_ambiguity_presence_reaches_active",
            thresholds["scanning_owner_ambiguity_presence"] >= thresholds["active_recognition"],
            "Scanning owner-ambiguity presence should require active-grade owner evidence.",
        ),
        _check(
            "scanning_ambiguity_presence_below_unlock",
            thresholds["scanning_owner_ambiguity_presence"] < thresholds["recognition"],
            "Scanning owner-ambiguity presence must remain below unlock-grade identity.",
        ),
        _check(
            "bounded_run",
            not require_bound or max_frames > 0 or max_seconds > 0.0,
            "Set MG_MAX_SECONDS or MG_MAX_FRAMES for unattended/manual test runs.",
        ),
    ]

    return PolicyAudit(
        lock_enabled=lock_enabled,
        whcdf_ipc_enabled=whcdf_ipc_enabled,
        adaface_flip_fusion_enabled=adaface_flip_fusion_enabled,
        max_frames=max_frames,
        max_seconds=max_seconds,
        thresholds=thresholds,
        checks=checks,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit MajestyGuard daemon policy settings.")
    parser.add_argument("--allow-lock-enabled", action="store_true")
    parser.add_argument("--allow-whcdf-ipc", action="store_true")
    parser.add_argument("--require-bound", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    audit = audit_policy(
        allow_lock_enabled=args.allow_lock_enabled,
        allow_whcdf_ipc=args.allow_whcdf_ipc,
        require_bound=args.require_bound,
    )
    data = asdict(audit)
    data["ok"] = audit.ok
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print("MajestyGuard policy audit")
        print(f"  ok: {audit.ok}")
        print(f"  lock_enabled: {audit.lock_enabled}")
        print(f"  whcdf_ipc_enabled: {audit.whcdf_ipc_enabled}")
        print(f"  adaface_flip_fusion_enabled: {audit.adaface_flip_fusion_enabled}")
        print(f"  max_frames: {audit.max_frames}")
        print(f"  max_seconds: {audit.max_seconds}")
        print(f"  thresholds: {audit.thresholds}")
        for check in audit.checks:
            status = "PASS" if check.ok else "FAIL"
            print(f"  {status} {check.name}: {check.message}")
    return 0 if audit.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
