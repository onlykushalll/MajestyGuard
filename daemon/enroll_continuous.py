"""MajestyGuard continuous, hands-free coverage enrollment.

    python daemon/enroll_continuous.py            # real run, writes the gallery
    python daemon/enroll_continuous.py --dry-run  # whole flow, writes nothing

One circular camera guide. Move your head slowly toward the dot; the ring fills
as distinct, high-quality views are collected. Nothing is written until the
coverage, liveness, quality and diversity checks all pass; the previous gallery
is backed up first and restored if the write fails. No images or video are
saved: only embeddings and scalar pose numbers, protected with DPAPI.

This is RGB-webcam enrollment. It is not Face ID / TrueDepth and makes no
depth-sensor claim.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from enrollment_commit import commit_result  # noqa: E402
from enrollment_coverage import (  # noqa: E402
    COMPLETE, CoverageCollector, CoverageConfig, Feedback, Observation, pose_from_5pt,
)

OUT_DIR = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "MajestyGuard"
OUT_FILE = OUT_DIR / "embeddings_v2.npy"
OUT_META_FILE = OUT_DIR / "embeddings_v2_meta.json"
log = logging.getLogger("majestyguard.enrollment")

MIN_FACE_HEIGHT = 0.28   # same enrollment framing floor as the guided flow
MAX_FACE_HEIGHT = 0.68
MAX_CENTER_OFFSET = 0.34
MAX_CAMERA_FAILURES = 100  # ~5 s of consecutive failed reads


def _max_seconds_from_env() -> float:
    try:
        value = float(os.environ.get("MG_MAX_SECONDS", "0") or 0)
    except ValueError:
        return 0.0
    return value if value > 0 else 0.0


# --------------------------------------------------------------------------
# One frame -> Observation
# --------------------------------------------------------------------------


def framing_problem(height_frac: float, center_dx: float, center_dy: float) -> str:
    if height_frac < MIN_FACE_HEIGHT:
        return "move_closer"
    if height_frac > MAX_FACE_HEIGHT:
        return "move_back"
    if center_dx > MAX_CENTER_OFFSET or center_dy > MAX_CENTER_OFFSET:
        return "center"
    return ""


@dataclass
class Analyzer:
    """Wraps the detector, quality metric and liveness model behind one call."""

    detect: Callable[[np.ndarray], list]            # frame -> faces
    measure_quality: Callable[[np.ndarray, Any], Any]
    liveness_score: Callable[[np.ndarray, Any], float]
    enhance: Callable[[np.ndarray], np.ndarray]
    config: CoverageConfig

    def observe(self, raw_frame: np.ndarray, t: float, frame_id: int) -> Observation:
        frame = self.enhance(raw_frame)
        faces: list = []
        try:
            faces = self.detect(frame)
            if len(faces) != 1:
                return Observation(t=t, frame_id=frame_id, face_count=len(faces))
            face = faces[0]
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = [float(v) for v in face.bbox]
            quality = self.measure_quality(frame, face)
            height_frac = float(quality.height_frac)
            framing = framing_problem(
                height_frac,
                abs((x1 + x2) / 2.0 - w / 2.0) / max(1.0, w / 2.0),
                abs((y1 + y2) / 2.0 - h / 2.0) / max(1.0, h / 2.0),
            )
            det = float(getattr(face, "det_score", 0.0))
            obs = Observation(
                t=t, frame_id=frame_id, face_count=1, det_score=det,
                quality=float(quality.score), height_frac=height_frac,
                illumination=float(getattr(quality, "illumination_mean", 0.0)), framing=framing,
            )
            pose = pose_from_5pt(getattr(face, "kps", None))
            emb = getattr(face, "normed_embedding", None)
            if pose is None or emb is None:
                return obs                      # collector reports no_embedding / quality
            obs.yaw, obs.pitch, obs.roll = pose
            obs.embedding = np.asarray(emb, dtype=np.float32).copy()
            cfg = self.config
            # Liveness is the expensive step: run it only for frames that
            # already passed every cheaper gate.
            if not framing and det >= cfg.min_det and obs.quality >= cfg.min_quality:
                obs.live_score = float(self.liveness_score(frame, face))
            return obs
        finally:
            for f in faces:
                e = getattr(f, "normed_embedding", None)
                if isinstance(e, np.ndarray) and e.flags.writeable:
                    e.fill(0)
            if frame is not raw_frame:
                frame[:] = 0


def release_observation(obs: Observation) -> None:
    if isinstance(obs.embedding, np.ndarray):
        obs.embedding.fill(0)


# --------------------------------------------------------------------------
# Capture session (pure loop over injected IO)
# --------------------------------------------------------------------------


@dataclass
class SessionOutcome:
    reason: str       # complete | cancelled | deadline | camera


def run_session(
    cap: Any,
    analyzer: Analyzer,
    collector: CoverageCollector,
    *,
    emit_preview: Callable[[np.ndarray], None],
    emit_feedback: Callable[[Feedback], None],
    should_stop: Callable[[], bool],
    deadline: Optional[float] = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    make_preview: Optional[Callable[[np.ndarray], np.ndarray]] = None,
) -> SessionOutcome:
    failures = 0
    frame_id = 0
    while True:
        if should_stop():
            collector.cancel()
            return SessionOutcome("cancelled")
        if deadline is not None and now() >= deadline:
            collector.cancel()
            return SessionOutcome("deadline")
        try:
            ok, raw = cap.read()
        except Exception:
            ok, raw = False, None
        if not ok or raw is None:
            failures += 1
            if isinstance(raw, np.ndarray):
                raw.fill(0)
            if failures >= MAX_CAMERA_FAILURES:
                collector.cancel()
                return SessionOutcome("camera")
            sleep(0.05)                      # never spin on a failing camera
            continue
        failures = 0
        frame_id += 1
        obs = None
        try:
            if make_preview is not None:
                emit_preview(make_preview(raw))
            obs = analyzer.observe(raw, now(), frame_id)
            fb = collector.feed(obs)
            emit_feedback(fb)
        finally:
            if obs is not None:
                release_observation(obs)
            raw[:] = 0
        if collector.state == COMPLETE:
            return SessionOutcome("complete")
        sleep(0.005)


# --------------------------------------------------------------------------
# Finish: validate -> commit (or dry run)
# --------------------------------------------------------------------------


@dataclass
class FinishResult:
    ok: bool
    message: str
    selected: int = 0


_FAIL_COPY = {
    "coverage_incomplete": "Not every direction was covered.",
    "liveness_failed": "A live face could not be confirmed for every view.",
    "coverage_lost_after_pruning": "Some views were too inconsistent to keep. Try again in even light.",
    "insufficient_diversity": "Not enough distinct views were collected.",
    "selection_undercovered": "The final selection missed a direction.",
    "duplicate_templates": "Too many near-identical views were collected.",
}


def _check_owner_continuity(result: Any, gallery_path: Path) -> "FinishResult | None":
    """None when the new face may replace the stored one, else the refusal to show."""
    import enrollment_gate as gate

    emb = result.embeddings()
    try:
        verdict = gate.check_continuity(emb, gallery_path)
    finally:
        emb.fill(0)
    if verdict.allowed:
        return None
    outcome, message = gate.refusal(verdict.reason)
    gate.write_result(Path(gallery_path).parent, outcome, message)
    return FinishResult(False, message)


def finish(
    collector: CoverageCollector,
    *,
    dry_run: bool,
    gallery_path: Path = OUT_FILE,
    meta_path: Path = OUT_META_FILE,
    commit: Callable[..., Any] = commit_result,
) -> FinishResult:
    result = collector.finalize()
    try:
        if not result.ok:
            return FinishResult(False, _FAIL_COPY.get(result.reason, result.reason) + " Nothing was saved.")
        if dry_run:
            return FinishResult(True, f"Dry run: {len(result.selected)} templates validated; nothing was saved.",
                                len(result.selected))
        verdict = _check_owner_continuity(result, gallery_path)
        if verdict is not None:
            return verdict
        outcome = commit(result, collector.config, gallery_path=gallery_path, meta_path=meta_path)
        if not outcome.ok:
            tail = "" if getattr(outcome, "rolled_back", True) else " Restoring the previous gallery failed."
            return FinishResult(False, f"Could not save the gallery ({outcome.error}).{tail}")
        return FinishResult(True, f"{len(result.selected)} templates saved.", len(result.selected))
    finally:
        collector.discard()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def _build_analyzer(config: CoverageConfig):
    import cv2  # noqa: F401
    from diagnostic_common import enhance_frame, resolve_resource_dir
    from face_quality import measure_face_quality
    from import_speedups import skip_unused_insightface_modules
    skip_unused_insightface_modules()
    from insightface.app import FaceAnalysis
    from liveness_detector import LivenessDetector

    models = resolve_resource_dir("models_insightface")
    live_dir = resolve_resource_dir("models")
    log.info("Loading face model")
    app = FaceAnalysis(name="buffalo_l", root=str(models), providers=["CPUExecutionProvider"],
                       allowed_modules=["detection", "recognition"])
    app.prepare(ctx_id=0, det_size=(320, 320))
    log.info("Loading liveness model")
    liveness = LivenessDetector(model_dir=str(live_dir))
    liveness.reset_session()
    analyzer = Analyzer(
        detect=app.get, measure_quality=measure_face_quality,
        liveness_score=liveness.score_fast, enhance=enhance_frame, config=config,
    )
    return analyzer, liveness


def _load_window():
    import importlib
    from diagnostic_common import resolve_resource_dir

    ui_dir = str(resolve_resource_dir("ui"))
    if ui_dir not in sys.path:
        sys.path.insert(0, ui_dir)
    return importlib.import_module("coverage_enrollment_window").CoverageEnrollmentWindow


def self_test() -> int:
    """Exercise native imports and model initialization without capture or UI."""
    _load_window()
    analyzer, liveness = _build_analyzer(CoverageConfig())
    try:
        from scipy import signal

        signal.butter(2, (0.1, 0.4), btype="band")
        analyzer.detect(np.full((480, 640, 3), 128, dtype=np.uint8))
        return 0
    finally:
        liveness.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--max-seconds", type=float, default=_max_seconds_from_env())
    parser.add_argument("--dry-run", action="store_true", help="run everything but write nothing")
    parser.add_argument("--gallery", type=Path, default=OUT_FILE)
    parser.add_argument("--meta", type=Path, default=OUT_META_FILE)
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()

    import enrollment_gate as gate
    if not args.dry_run and not gate.prepare_enrollment(args.gallery):
        return 1

    import cv2
    import importlib

    # Dynamic imports keep the daemon package free of static Qt imports
    # (tests/test_suite_contract.py); this tool is the only Qt-using entry point.
    QImage = importlib.import_module("PyQt6.QtGui").QImage
    QApplication = importlib.import_module("PyQt6.QtWidgets").QApplication
    CoverageEnrollmentWindow = _load_window()

    config = CoverageConfig()
    qt_app = QApplication.instance() or QApplication(sys.argv)
    window = CoverageEnrollmentWindow()
    window.set_loading(True)
    window.title.setText("Preparing face setup")
    window.hint.setText("Loading the recognition engine on this PC.")
    collector = CoverageCollector(config)
    stop = threading.Event()
    window.cancel_requested.connect(stop.set)
    status = {"code": 1}

    def make_preview(raw: np.ndarray) -> np.ndarray:
        small = cv2.resize(raw, (360, int(raw.shape[0] * 360 / raw.shape[1])))
        return cv2.flip(small, 1)           # mirror: user's left is the screen's left

    def emit_preview(arr: np.ndarray) -> None:
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, w * 3, QImage.Format.Format_BGR888).copy()
        arr[:] = 0
        window.preview_signal.emit(img)

    def worker() -> None:
        cap = liveness = None
        try:
            analyzer, liveness = _build_analyzer(config)
            if stop.is_set():
                window.result_signal.emit(False, "Enrollment canceled. Nothing was saved.")
                return
            cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(args.camera)
            if not cap.isOpened():
                window.result_signal.emit(False, "Camera unavailable. Close other camera apps and try again.")
                return
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 15)
            deadline = time.monotonic() + args.max_seconds if args.max_seconds > 0 else None
            outcome = run_session(
                cap, analyzer, collector, emit_preview=emit_preview,
                emit_feedback=window.feedback_signal.emit, should_stop=stop.is_set,
                deadline=deadline, make_preview=make_preview,
            )
            if stop.is_set():
                window.result_signal.emit(False, "Enrollment canceled. Nothing was saved.")
            elif outcome.reason == "complete":
                res = finish(collector, dry_run=args.dry_run, gallery_path=args.gallery, meta_path=args.meta)
                status["code"] = 0 if res.ok else 1
                if res.ok and not args.dry_run:
                    gate.prune_backups([args.gallery, args.meta])
                    gate.write_result(args.gallery.parent, "ok", f"{res.selected} templates")
                window.result_signal.emit(res.ok, res.message)
            else:
                copy = {
                    "cancelled": "Enrollment canceled. Nothing was saved.",
                    "deadline": "Enrollment timed out. Nothing was saved.",
                    "camera": "The camera stopped responding. Close other camera apps and try again.",
                }[outcome.reason]
                window.result_signal.emit(False, copy)
        except Exception as exc:  # keep the window honest instead of dying silently
            log.exception("Enrollment failed")
            if not args.dry_run:
                gate.write_result(args.gallery.parent, "failed")
            collector.discard()
            window.result_signal.emit(False, f"Enrollment failed: {exc}. Nothing was saved.")
        finally:
            collector.discard()
            if cap is not None:
                cap.release()
            try:
                if liveness is not None:
                    liveness.close()
            except Exception:
                pass

    thread = threading.Thread(target=worker, name="enroll-continuous", daemon=True)
    window.show()
    thread.start()
    qt_app.exec()
    stop.set()
    thread.join(timeout=5.0)
    return status["code"]


if __name__ == "__main__":
    raise SystemExit(main())
