"""
MajestyGuard v2 Python daemon.

Owns camera capture, frame-diff/YuNet prefiltering, FaceEngine recognition,
12-layer liveness, state transitions, WHCDF authorization state, and UI IPC.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from enum import Enum, auto
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

_DAEMON_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _DAEMON_DIR.parent
_MODELS_DIR = _ROOT_DIR / "models"
sys.path.insert(0, str(_DAEMON_DIR))

_LOG_DIR = Path(os.environ.get("LOCALAPPDATA", "C:/tmp")) / "MajestyGuard"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_PATH = _LOG_DIR / "daemon.log"
_MG_STATE_DIR = Path(
    os.environ.get("LOCALAPPDATA", os.environ.get("ProgramData", r"C:\ProgramData"))
) / "MajestyGuard"
_MG_STATE_DIR.mkdir(parents=True, exist_ok=True)
_LOG_MAX_BYTES = _env_log_max_bytes = int(os.environ.get("MG_LOG_MAX_BYTES", "5242880"))
_LOG_BACKUP_COUNT = int(os.environ.get("MG_LOG_BACKUP_COUNT", "3"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(
            _LOG_PATH,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,
)
log = logging.getLogger("majestyguard.daemon")

_ACTIVE_DAEMON: Optional[MajestyGuardDaemon] = None

from companion_ipc import FaceState, start_companion_ipc_thread
from face_engine import FaceEngine
from input_idle import get_idle_seconds
from cmd_server import CMDServer
from idle_monitor import get_idle_seconds, read_idle_timeout
from ipc_server import IPCServer, ServiceIPCServer
from presence import MotionFilter, PresenceDetector
from session_monitor import SessionEvent, SessionMonitor

def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("Invalid %s=%r; using %d", name, raw, default)
        return default
    if value < minimum:
        log.warning("Invalid %s=%d; using %d", name, value, default)
        return default
    return value


def _env_float(name: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("Invalid %s=%r; using %.3f", name, raw, default)
        return default
    if value < minimum or value > maximum:
        log.warning("Invalid %s=%.3f; using %.3f", name, value, default)
        return default
    return value


CAMERA_INDEX = _env_int("MG_CAMERA_IDX", 0, 0)
TARGET_FPS = _env_int("MG_TARGET_FPS", 15, 1)
MAX_FRAMES = _env_int("MG_MAX_FRAMES", 0, 0)
MAX_SECONDS = _env_float("MG_MAX_SECONDS", 0.0, 0.0, 86400.0)
LOG_EVERY_N_FRAMES = _env_int("MG_LOG_EVERY_N_FRAMES", 30, 0)
FRAME_INTERVAL_S = 1.0 / TARGET_FPS
CAMERA_RETRY_ATTEMPTS = _env_int("MG_CAMERA_RETRY_ATTEMPTS", 10, 1)
CAMERA_RETRY_DELAY_S = _env_float("MG_CAMERA_RETRY_DELAY_S", 2.0, 0.0, 60.0)
CAMERA_UNAVAILABLE_RETRY_S = _env_float("MG_CAMERA_UNAVAILABLE_RETRY_S", 30.0, 0.1, 3600.0)
CAMERA_READ_FAILURES_BEFORE_UNAVAILABLE = _env_int("MG_CAMERA_READ_FAILURES_BEFORE_UNAVAILABLE", 3, 1)
SOFT_LOCK_IDLE_SECONDS = read_idle_timeout()
SOFT_LOCK_IDLE_REARM_SECONDS = _env_float("MG_SOFT_LOCK_IDLE_REARM_SECONDS", 1.0, 0.0, 10.0)
SOFT_LOCK_RELEASE_GRACE_SECONDS = _env_float("MG_SOFT_LOCK_RELEASE_GRACE_SECONDS", 15.0, 0.0, 300.0)
SOFT_LOCK_VERIFY_WINDOW_SECONDS = _env_float("MG_SOFT_LOCK_VERIFY_WINDOW_SECONDS", 12.0, 3.0, 60.0)
PASSIVE_FPS = _env_float("MG_PASSIVE_FPS", 0.0, 0.0, 5.0)
PASSIVE_LOOP_SLEEP_S = 0.5 if PASSIVE_FPS <= 0 else max(0.2, 1.0 / PASSIVE_FPS)
BURST_FAST_PATH_SECONDS = _env_float("MG_BURST_FAST_PATH_SECONDS", 5.0, 0.5, 10.0)
BURST_FAST_LIVENESS_THRESHOLD = _env_float("MG_BURST_FAST_LIVENESS_THRESHOLD", 0.82, 0.70, 0.99)
BURST_FAST_CONFIRM_FRAMES = _env_int("MG_BURST_FAST_CONFIRM_FRAMES", 3, 1)

ABSENT_FRAMES_LOCK = 75
NO_FACE_LIVENESS_RESET_FRAMES = _env_int("MG_NO_FACE_LIVENESS_RESET_FRAMES", 5, 1)
ACTIVE_REACQUIRE_GRACE_FRAMES = _env_int("MG_ACTIVE_REACQUIRE_GRACE_FRAMES", 8, 0)
# SCANNING: 3 consecutive unrecognized frames → social lock (fast response to intruder)
# ACTIVE: 8 consecutive unrecognized frames → social lock (tolerates brief quality drops)
STRANGER_CONFIRM_FRAMES_SCANNING = 3
STRANGER_CONFIRM_FRAMES_ACTIVE   = 8
RECOGNITION_THRESHOLD = _env_float("MG_RECOGNITION_THRESHOLD", 0.78)
# Fast owner path: combine two strong live owner-like frames instead of waiting
# for one perfect peak frame. This improves UX without lowering stranger gates.
SCANNING_FAST_OWNER_SCORE = _env_float("MG_SCANNING_FAST_OWNER_SCORE", 0.72)
SCANNING_FAST_OWNER_PRESENCE = _env_float("MG_SCANNING_FAST_OWNER_PRESENCE", 0.72)
SCANNING_FAST_OWNER_CONFIRM_FRAMES = _env_int("MG_SCANNING_FAST_OWNER_CONFIRM_FRAMES", 2, 1)
SCANNING_FAST_OWNER_MIN_QUALITY = _env_float("MG_SCANNING_FAST_OWNER_MIN_QUALITY", 0.78)
SCANNING_FAST_OWNER_MIN_FACE_HEIGHT = _env_float("MG_SCANNING_FAST_OWNER_MIN_FACE_HEIGHT", 0.38)
SCANNING_FAST_OWNER_MAX_CENTER_OFFSET = _env_float("MG_SCANNING_FAST_OWNER_MAX_CENTER_OFFSET", 0.26)
SCANNING_QUICK_OWNER_SCORE = _env_float("MG_SCANNING_QUICK_OWNER_SCORE", 0.70)
SCANNING_QUICK_OWNER_PRESENCE = _env_float("MG_SCANNING_QUICK_OWNER_PRESENCE", 0.70)
SCANNING_QUICK_OWNER_MIN_LIVENESS = _env_float("MG_SCANNING_QUICK_OWNER_MIN_LIVENESS", 0.74)
SCANNING_QUICK_OWNER_MIN_QUALITY = _env_float("MG_SCANNING_QUICK_OWNER_MIN_QUALITY", 0.80)
SCANNING_QUICK_OWNER_MIN_FACE_HEIGHT = _env_float("MG_SCANNING_QUICK_OWNER_MIN_FACE_HEIGHT", 0.36)
SCANNING_QUICK_OWNER_MAX_CENTER_OFFSET = _env_float("MG_SCANNING_QUICK_OWNER_MAX_CENTER_OFFSET", 0.25)
SCANNING_QUICK_OWNER_MIN_TRACK_IOU = _env_float("MG_SCANNING_QUICK_OWNER_MIN_TRACK_IOU", 0.75)
# After a high-confidence match moves us to ACTIVE, tolerate normal webcam angle
# drift without lowering the initial unlock-grade threshold.
ACTIVE_RECOGNITION_THRESHOLD = _env_float("MG_ACTIVE_RECOGNITION_THRESHOLD", 0.65)
STRANGER_SCORE_THRESHOLD = _env_float("MG_STRANGER_SCORE_THRESHOLD", 0.55)
STRANGER_MIN_FACE_HEIGHT = _env_float("MG_STRANGER_MIN_FACE_HEIGHT", 0.24)
STRANGER_MAX_CENTER_OFFSET = _env_float("MG_STRANGER_MAX_CENTER_OFFSET", 0.42)
STRANGER_MIN_FRAME_QUALITY = _env_float("MG_STRANGER_MIN_FRAME_QUALITY", 0.42)
STRANGER_MAX_SMOOTHED_SCORE = _env_float("MG_STRANGER_MAX_SMOOTHED_SCORE", 0.58)
SCANNING_OWNER_AMBIGUITY_GRACE_FRAMES = _env_int("MG_SCANNING_OWNER_AMBIGUITY_GRACE_FRAMES", 15, 0)
SCANNING_OWNER_AMBIGUITY_MIN_SCORE = _env_float("MG_SCANNING_OWNER_AMBIGUITY_MIN_SCORE", 0.50)
SCANNING_OWNER_AMBIGUITY_PRESENCE = _env_float("MG_SCANNING_OWNER_AMBIGUITY_PRESENCE", 0.65)
ACTIVE_CONTINUITY_SMOOTH_THRESHOLD = _env_float("MG_ACTIVE_CONTINUITY_SMOOTH_THRESHOLD", 0.60)
ACTIVE_CONTINUITY_GRACE_FRAMES = _env_int("MG_ACTIVE_CONTINUITY_GRACE_FRAMES", 5, 0)
ACTIVE_CONTINUITY_TRACK_MIN_SCORE = _env_float("MG_ACTIVE_CONTINUITY_TRACK_MIN_SCORE", 0.35)
# Liveness threshold: 0.70 is the calibrated value for RGB-only webcam hardware.
# The 12-layer stack is designed for 0.82 with IR depth, but on monocular RGB:
#   - MiDaS depth returns 0.40-0.72 (uncertain range, now skip-blended)
#   - rPPG blending now additive not substitutive
#   - Per-frame combined for a real face: ~0.78-0.85
#   - 10th percentile of 30-frame window: ~0.70-0.78
# 0.70 is still very robust — ONNX alone carries 0.28 weight at 1.0 for real faces.
LIVENESS_THRESHOLD = _env_float("MG_LIVENESS_THRESHOLD", 0.70)
ACTIVE_LIVENESS_JITTER_FLOOR = _env_float("MG_ACTIVE_LIVENESS_JITTER_FLOOR", 0.55)
LOCK_ENABLED = os.environ.get("MG_ENABLE_LOCK", "0") == "1"
WHCDF_IPC_ENABLED = os.environ.get("MG_ENABLE_WHCDF_IPC", "0") == "1"
SERVICE_IPC_ENABLED = os.environ.get("MG_ENABLE_SERVICE_IPC", "0") == "1"
OVERLAY_WATCHDOG_ENABLED = os.environ.get("MG_OVERLAY_WATCHDOG", "0") == "1"
OVERLAY_WATCHDOG_INTERVAL_S = _env_float("MG_OVERLAY_WATCHDOG_INTERVAL_S", 2.0, 0.5, 30.0)

BIOMETRIC_PATH = (
    Path(os.environ.get("LOCALAPPDATA", "C:/tmp"))
    / "MajestyGuard"
    / "biometric.mgd"
)
INSIGHTFACE_MODEL_DIR = _ROOT_DIR / "models_insightface"
DPAPI_HELPER_PATH = Path(
    os.environ.get(
        "MG_DPAPI_HELPER",
        r"C:\tmp\MajestyGuard\build\Debug\MajestyGuard.DpapiHelper\MajestyGuard.DpapiHelper.exe",
    )
)


class State(Enum):
    IDLE = auto()
    SCANNING = auto()
    ACTIVE = auto()
    SOFT_LOCK = auto()
    LOCKED = auto()
    SOCIAL_LOCK = auto()
    HOSTILE_LOCK = auto()
    SYSTEM_LOCKED = auto()
    CAMERA_UNAVAILABLE = auto()


def lock_workstation() -> None:
    if not LOCK_ENABLED:
        log.warning("LOCK SUPPRESSED (set MG_ENABLE_LOCK=1 to enable real locking)")
        return
    log.warning("LOCKING WORKSTATION")
    ctypes.windll.user32.LockWorkStation()


class MajestyGuardDaemon:
    def __init__(self):
        if os.environ.get("MG_FORCE_LOCK_STARTUP") == "1":
            self.state = State.SOFT_LOCK
        else:
            self.state = State.IDLE
        self._stop = threading.Event()
        self._whcdf_stop = threading.Event()
        self._is_tearing_down = False

        self.motion = MotionFilter()
        self.presence = PresenceDetector()
        self.face_eng: Optional[FaceEngine] = None
        self.ipc = IPCServer()
        self.service_ipc: Optional[ServiceIPCServer] = None
        self.command_ipc: Optional[CMDServer] = None
        self.session_monitor = SessionMonitor(self._queue_session_event)
        self._session_events: "queue.Queue[SessionEvent]" = queue.Queue()
        self._ui_commands: "queue.Queue[tuple[str, str]]" = queue.Queue()

        self._absent_frames = 0
        self._stranger_frames = 0
        self._active_reacquire_grace_frames = 0
        self._owner_continuity_grace_frames = 0
        self._scanning_owner_ambiguity_grace_frames = 0
        self._scanning_owner_candidate_frames = 0
        self._cap: Optional[cv2.VideoCapture] = None
        self._last_camera_retry_at = 0.0
        self._camera_read_failures = 0
        self._background_processes_restricted = False
        self._input_idle_soft_lock_armed = True
        self._soft_lock_release_grace_until = 0.0
        self._soft_lock_verify_until = 0.0
        self._soft_lock_verification_started_at = 0.0
        self._soft_lock_owner_candidate_frames = 0
        self._soft_lock_fast_pass_frames = 0
        self._verify_cooldown_until = 0.0
        self._verify_failed_until = 0.0
        self._cold_start_ms: dict[str, int] = {}
        self._overlay_proc: Optional[subprocess.Popen] = None
        self._overlay_watchdog_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        log.info("MajestyGuard daemon starting")
        log.info("Biometric: %s (exists=%s)", BIOMETRIC_PATH, BIOMETRIC_PATH.exists())
        self._write_daemon_pid()

        self.ipc.start()
        self.command_ipc = CMDServer(self._queue_ui_command)
        self.command_ipc.start()
        if self.state == State.SOFT_LOCK:
            try:
                (_MG_STATE_DIR / "lock_state.txt").write_text("LOCKED\n", encoding="utf-8")
            except OSError:
                pass
            self.ipc.broadcast_state("locked_passive", detail="Force lock startup")
        else:
            self.ipc.broadcast_state("idle")
        self.session_monitor.start()
        self._start_overlay_watchdog()

        if WHCDF_IPC_ENABLED:
            start_companion_ipc_thread(self._whcdf_stop)
            log.info("WHCDF companion IPC thread started")
        else:
            log.info("WHCDF companion IPC disabled (set MG_ENABLE_WHCDF_IPC=1 to enable)")

        if SERVICE_IPC_ENABLED:
            self.service_ipc = ServiceIPCServer()
            self.service_ipc.start()
            log.info("Service DetectionResult IPC enabled")
        else:
            log.info("Service DetectionResult IPC disabled (set MG_ENABLE_SERVICE_IPC=1 to enable)")

        t0 = time.perf_counter()
        self._load_cv_engine()
        t_model = time.perf_counter()
        self._warmup_inference()
        t_warm = time.perf_counter()
        self._open_camera()
        t_cam = time.perf_counter()
        self._cold_start_ms = {
            "model_load": round((t_model - t0) * 1000),
            "warmup": round((t_warm - t_model) * 1000),
            "camera_open": round((t_cam - t_warm) * 1000),
            "total": round((t_cam - t0) * 1000),
        }
        log.info(
            "Cold start complete: model=%dms warmup=%dms camera=%dms total=%dms",
            self._cold_start_ms["model_load"],
            self._cold_start_ms["warmup"],
            self._cold_start_ms["camera_open"],
            self._cold_start_ms["total"],
        )

        try:
            self._run_loop()
        except KeyboardInterrupt:
            log.info("Keyboard interrupt - shutting down")
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._stop.set()

    def _load_cv_engine(self) -> None:
        log.info("Loading FaceEngine from %s", INSIGHTFACE_MODEL_DIR)
        self.face_eng = FaceEngine(
            model_dir=str(INSIGHTFACE_MODEL_DIR),
            camera_idx=CAMERA_INDEX,
            recognition_threshold=RECOGNITION_THRESHOLD,
            liveness_threshold=LIVENESS_THRESHOLD,
            open_camera=False,
            liveness_model_dir=str(_MODELS_DIR),
        )
        if not self.face_eng.initialize():
            raise RuntimeError("FaceEngine failed to initialize")
        self._load_enrolled_embeddings()
        log.info("FaceEngine loaded")

    def _warmup_inference(self) -> None:
        if self.face_eng is None:
            return
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        try:
            self.face_eng.process_frame(blank, liveness_mode="fast")
            log.info("Warmup inference complete (blank frame)")
        except Exception as exc:
            log.warning("Warmup inference failed (non-fatal): %s", exc)

    def _load_enrolled_embeddings(self) -> None:
        engine = self._require_face_engine()

        # ── V2 numpy file (preferred — same pipeline as v2 engine) ───────────
        v2_path = Path(os.environ.get("LOCALAPPDATA", "C:/tmp")) / "MajestyGuard" / "embeddings_v2.npy"
        if v2_path.exists():
            try:
                data = np.load(str(v2_path))
            except Exception as e:
                raise RuntimeError(f"Failed to load v2 embeddings from {v2_path}: {e}") from e
            try:
                accepted_count = engine.load_enrolled_embeddings(data.tolist())
            except Exception as e:
                raise RuntimeError(f"Failed to load v2 embeddings from {v2_path}: {e}") from e
            if accepted_count <= 0:
                raise RuntimeError(f"No valid v2 enrolled embeddings loaded from {v2_path}")
            log.info("Loaded %d valid v2 embeddings from %s", accepted_count, v2_path)
            return

        # ── Legacy DPAPI path (v1 C# enrollment) ─────────────────────────────
        if not BIOMETRIC_PATH.exists():
            log.warning("No biometric found — run: python daemon/enroll_v2.py")
            return
        if not DPAPI_HELPER_PATH.exists():
            log.warning("DPAPI helper not found: %s", DPAPI_HELPER_PATH)
            return
        proc = subprocess.run(
            [str(DPAPI_HELPER_PATH), str(BIOMETRIC_PATH)],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        if proc.returncode != 0:
            log.warning("Embedding load failed (%d): %s", proc.returncode, proc.stderr.strip())
            return
        embeddings = json.loads(proc.stdout)
        engine.load_enrolled_embeddings(embeddings)
        log.warning("Loaded %d LEGACY v1 embeddings — recognition may fail. Run enroll_v2.py.", len(embeddings))

    def _open_camera(
        self,
        *,
        max_attempts: int = CAMERA_RETRY_ATTEMPTS,
        retry_delay_s: float = CAMERA_RETRY_DELAY_S,
    ) -> bool:
        self._release_camera()
        max_attempts = max(1, max_attempts)
        for attempt in range(1, max_attempts + 1):
            log.info("Opening camera index %d (attempt %d/%d)", CAMERA_INDEX, attempt, max_attempts)
            cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(CAMERA_INDEX)
            if cap.isOpened():
                self._cap = cap
                self._camera_read_failures = 0
                self._cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self._last_camera_retry_at = time.monotonic()
                log.info(
                    "Camera opened: %dx%d @ %.1f FPS",
                    self._cap.get(cv2.CAP_PROP_FRAME_WIDTH),
                    self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
                    self._cap.get(cv2.CAP_PROP_FPS),
                )
                if self.state == State.CAMERA_UNAVAILABLE:
                    self._transition(State.IDLE)
                return True
            cap.release()
            if attempt < max_attempts and retry_delay_s > 0:
                time.sleep(retry_delay_s)
        self._last_camera_retry_at = time.monotonic()
        if self.state in (State.SOFT_LOCK, State.SOCIAL_LOCK, State.HOSTILE_LOCK):
            log.error("[Camera] Cannot open camera after all retries — writing UNLOCKED and exiting")
            try:
                (_MG_STATE_DIR / "lock_state.txt").write_text("UNLOCKED\n", encoding="utf-8")
                (_MG_STATE_DIR / "daemon.pid").unlink(missing_ok=True)
            except OSError:
                pass
            sys.exit(1)

        log.error("Camera unavailable after %d open attempts", max_attempts)
        if self.state != State.SYSTEM_LOCKED:
            self._transition(State.CAMERA_UNAVAILABLE)
        return False

    def _release_camera(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    def _queue_session_event(self, event: SessionEvent) -> None:
        self._session_events.put(event)

    def _queue_ui_command(self, command: str, detail: str) -> None:
        self._ui_commands.put((command, detail))

    def _drain_session_events(self) -> None:
        while True:
            try:
                event = self._session_events.get_nowait()
            except queue.Empty:
                return
            self._handle_session_event(event)

    def _drain_ui_commands(self) -> None:
        while True:
            try:
                command, detail = self._ui_commands.get_nowait()
            except queue.Empty:
                return
            self._handle_ui_command(command, detail)

    def _handle_session_event(self, event: SessionEvent) -> None:
        if event in (SessionEvent.SESSION_LOCK, SessionEvent.SESSION_LOGOFF):
            log.info("Session event %s -> SYSTEM_LOCKED", event.value)
            self._transition(State.SYSTEM_LOCKED)
            return
        if event in (SessionEvent.SESSION_UNLOCK, SessionEvent.SESSION_LOGON):
            log.info("Session event %s -> Unlocked, stopping main processes", event.value)
            try:
                (_MG_STATE_DIR / "lock_state.txt").write_text("UNLOCKED\n", encoding="utf-8")
            except OSError as e:
                log.warning("Failed to write lock_state.txt: %s", e)
            self._transition(State.IDLE)
            self.schedule_exit()
            return
        log.info(
            "Ignoring unknown session event: %s",
            getattr(event, "value", event),
        )

    def _handle_ui_command(self, command: str, detail: str) -> None:
        if command == "simulate_crash":
            log.warning("Simulating unhandled exception/crash")
            raise RuntimeError("Simulated unhandled exception")
        if command == "emergency_lock":
            log.warning("UI requested emergency lock from %s", detail or "unknown")
            self._transition(State.HOSTILE_LOCK)
            return
        if command == "windows_lock_used":
            log.info("[CMD] Windows lock used by user — scheduling clean teardown")
            self.schedule_exit()
            return
        if command != "verify_requested":
            log.warning("Ignoring unknown UI command: %s", command)
            return
        if self.state not in (State.SOFT_LOCK, State.SOCIAL_LOCK):
            log.debug("Ignoring verify_requested while state=%s", self.state.name)
            return
        cooldown_until = getattr(self, "_verify_cooldown_until", 0.0)
        if cooldown_until and time.monotonic() < cooldown_until:
            remaining = cooldown_until - time.monotonic()
            log.info("[CMD] verify_requested ignored — cooldown active, %.1fs remaining", remaining)
            return
        self._start_soft_lock_verification(detail or "ui")

    def schedule_exit(self) -> None:
        """Schedule clean exit of daemon process."""
        log.info("[Daemon] schedule_exit called — scheduling clean teardown in 0.5s")
        try:
            lock_state_file = _MG_STATE_DIR / "lock_state.txt"
            lock_state_file.write_text("UNLOCKED\n", encoding="utf-8")
        except OSError as e:
            log.warning("Failed to write lock_state.txt on scheduled exit: %s", e)
        self._exit_at = time.monotonic() + 0.5

    def _write_daemon_pid(self) -> None:
        """Write this daemon's PID to daemon.pid for monitor watchdog."""
        try:
            pid_file = _MG_STATE_DIR / "daemon.pid"
            pid_file.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as e:
            log.warning("Failed to write daemon.pid: %s", e)

    def teardown(self) -> None:
        """Called exactly once on successful owner verification to cleanly exit.

        Idempotent — calling twice must not crash.
        """
        if getattr(self, "_is_tearing_down", False):
            log.info("[Teardown] Already tearing down — skipping duplicate call")
            return
        self._is_tearing_down = True

        log.info("[Teardown] Starting clean teardown sequence")

        # 1. Signal UI to exit cleanly and wait briefly
        try:
            self.ipc.broadcast_state("exit")
            time.sleep(0.5)
        except Exception as e:
            log.warning("[Teardown] Failed to broadcast exit: %s", e)

        # 2. Restore taskbar visibility (SW_SHOW) just in case
        try:
            hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 5)
            hwnd2 = ctypes.windll.user32.FindWindowW("Shell_SecondaryTrayWnd", None)
            if hwnd2:
                ctypes.windll.user32.ShowWindow(hwnd2, 5)
            log.info("[Teardown] Taskbar restored")
        except Exception as e:
            log.error("[Teardown] Taskbar restore failed: %s", e)

        # 3. Clear FaceState
        log.info("[Teardown] Clearing FaceState")
        try:
            FaceState.clear()
        except Exception as e:
            log.error("[Teardown] FaceState clear failed: %s", e)

        # 4. Write UNLOCKED to lock_state.txt
        log.info("[Teardown] Writing UNLOCKED to lock_state.txt")
        try:
            lock_state_file = _MG_STATE_DIR / "lock_state.txt"
            lock_state_file.write_text("UNLOCKED\n", encoding="utf-8")
        except OSError as e:
            log.warning("[Teardown] Failed to write lock_state.txt: %s", e)

        # 5. Remove daemon.pid
        log.info("[Teardown] Removing daemon.pid")
        try:
            pid_file = _MG_STATE_DIR / "daemon.pid"
            if pid_file.exists():
                pid_file.unlink(missing_ok=True)
        except OSError as e:
            log.warning("[Teardown] Failed to remove daemon.pid: %s", e)

        # 6. Stop overlay (terminates the process if it's still alive)
        log.info("[Teardown] Stopping overlay process")
        try:
            self._stop_owned_overlay()
        except Exception as e:
            log.error("[Teardown] Overlay stop failed: %s", e)

        # 7. Shut down all subsystems
        log.info("[Teardown] Shutting down subsystems")
        try:
            self._shutdown()
        except Exception as e:
            log.error("[Teardown] Subsystem shutdown failed: %s", e)

        # 8. Exit cleanly
        log.info("[Teardown] Exiting full daemon")
        sys.exit(0)

    def _shutdown(self) -> None:
        log.info("Shutting down")
        if hasattr(self, "_stop"):
            self._stop.set()
        if hasattr(self, "_whcdf_stop"):
            self._whcdf_stop.set()
        if getattr(self, "session_monitor", None):
            self.session_monitor.stop()
        if hasattr(self, "ipc"):
            self.ipc.stop()
        if getattr(self, "command_ipc", None):
            self.command_ipc.stop()
        if getattr(self, "service_ipc", None):
            self.service_ipc.stop()
        self._release_camera()
        self._stop_owned_overlay()
        if getattr(self, "face_eng", None):
            self.face_eng.shutdown()
        FaceState.clear()
        log.info("Daemon stopped")

    def _run_loop(self) -> None:
        run_started_at = time.monotonic()
        limits = []
        if MAX_FRAMES > 0:
            limits.append(f"max frames {MAX_FRAMES}")
        if MAX_SECONDS > 0:
            limits.append(f"max seconds {MAX_SECONDS:.1f}")
        limit_text = ", " + ", ".join(limits) if limits else ""
        log.info("Camera loop started - target %d FPS%s", TARGET_FPS, limit_text)
        frame_no = 0

        while not self._stop.is_set():
            self._drain_session_events()
            self._drain_ui_commands()
            if hasattr(self, "_exit_at") and time.monotonic() >= self._exit_at:
                log.info("[MainLoop] Scheduled exit time reached — tearing down")
                self.teardown()
                break
            if self._stop_after_time_limit(run_started_at):
                break
            loop_start = time.monotonic()
            if self.state == State.SYSTEM_LOCKED:
                self._release_camera()
                time.sleep(0.5)
                continue
            if self.state == State.CAMERA_UNAVAILABLE:
                now = time.monotonic()
                if now - self._last_camera_retry_at >= CAMERA_UNAVAILABLE_RETRY_S:
                    self._open_camera(max_attempts=1, retry_delay_s=0)
                time.sleep(0.5)
                continue
            if self._maybe_enter_soft_lock_for_input_idle():
                self._pace(loop_start)
                continue
            if self._is_soft_lock_passive():
                self._handle_passive_soft_lock(loop_start)
                continue
            if self._cap is None:
                max_att = 5 if self.state in (State.SOFT_LOCK, State.SOCIAL_LOCK, State.HOSTILE_LOCK) else 1
                delay = 0.5 if self.state in (State.SOFT_LOCK, State.SOCIAL_LOCK, State.HOSTILE_LOCK) else 0.0
                if not self._open_camera(max_attempts=max_att, retry_delay_s=delay):
                    time.sleep(0.5)
                    continue
            ret, frame = self._cap.read()
            if not ret or frame is None:
                self._handle_camera_read_failure()
                time.sleep(0.5)
                continue
            self._camera_read_failures = 0

            frame_no += 1

            if self.state == State.IDLE and not self.motion.has_motion(frame):
                if self._stop_after_frame_limit(frame_no):
                    break
                self._pace(loop_start)
                continue

            self._tick(frame, frame_no)
            if self._stop_after_frame_limit(frame_no) or self._stop_after_time_limit(run_started_at):
                break
            self._pace(loop_start)

    def _stop_after_frame_limit(self, frame_no: int) -> bool:
        if MAX_FRAMES <= 0 or frame_no < MAX_FRAMES:
            return False
        log.info("Dry-run frame limit reached (%d); stopping daemon", frame_no)
        self._stop.set()
        return True

    def _stop_after_time_limit(self, run_started_at: float) -> bool:
        if MAX_SECONDS <= 0:
            return False
        elapsed = time.monotonic() - run_started_at
        if elapsed < MAX_SECONDS:
            return False
        log.info("Dry-run time limit reached (%.1fs); stopping daemon", elapsed)
        self._stop.set()
        return True

    def _pace(self, loop_start: float) -> None:
        sleep_for = FRAME_INTERVAL_S - (time.monotonic() - loop_start)
        if sleep_for > 0:
            time.sleep(sleep_for)

    def _handle_passive_soft_lock(self, loop_start: float) -> None:
        now = time.monotonic()
        if now < getattr(self, "_verify_failed_until", 0.0):
            self.ipc.broadcast_state("verify_failed")
        else:
            self.ipc.broadcast_state(self._lock_overlay_state_name(), detail="Press Space to verify")
        if OVERLAY_WATCHDOG_ENABLED:
            self._ensure_overlay_alive_if_needed()
        if PASSIVE_FPS <= 0:
            self._release_camera()
            time.sleep(PASSIVE_LOOP_SLEEP_S)
            return
        if self._cap is None and not self._open_camera(max_attempts=1, retry_delay_s=0):
            time.sleep(PASSIVE_LOOP_SLEEP_S)
            return
        ret, frame = self._cap.read()
        if not ret or frame is None:
            self._handle_camera_read_failure()
            time.sleep(PASSIVE_LOOP_SLEEP_S)
            return
        if (
            self.presence.has_face(frame)
            and self.state in (State.SOFT_LOCK, State.SOCIAL_LOCK)
            and now >= getattr(self, "_verify_cooldown_until", 0.0)
        ):
            self._start_soft_lock_verification("passive_face")
        elapsed = time.monotonic() - loop_start
        sleep_for = max(0.0, PASSIVE_LOOP_SLEEP_S - elapsed)
        if sleep_for:
            time.sleep(sleep_for)

    def _start_overlay_watchdog(self) -> None:
        if not OVERLAY_WATCHDOG_ENABLED:
            return
        if self._overlay_watchdog_thread and self._overlay_watchdog_thread.is_alive():
            return
        self._overlay_watchdog_thread = threading.Thread(
            target=self._watch_overlay,
            name="mg-overlay-watchdog",
            daemon=True,
        )
        self._overlay_watchdog_thread.start()

    def _watch_overlay(self) -> None:
        while not self._stop.is_set():
            self._ensure_overlay_alive_if_needed()
            self._stop.wait(OVERLAY_WATCHDOG_INTERVAL_S)

    def _ensure_overlay_alive_if_needed(self) -> None:
        if self.state not in (State.SOFT_LOCK, State.SOCIAL_LOCK):
            return
        if self._overlay_proc is not None and self._overlay_proc.poll() is None:
            return
        log.warning("[Overlay] Overlay died or is missing while locked - restarting")
        self._launch_overlay()

    def _launch_overlay(self) -> None:
        ui_path = _ROOT_DIR / "ui" / "main.py"
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        self._overlay_proc = subprocess.Popen(
            [sys.executable, str(ui_path)],
            cwd=str(_ROOT_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _stop_owned_overlay(self) -> None:
        proc = getattr(self, "_overlay_proc", None)
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _start_monitor_watchdog(self) -> None:
        """Start background thread that relaunches the monitor if it dies while locked."""
        t = threading.Thread(target=self._monitor_watchdog_loop, name="mg-monitor-watchdog", daemon=True)
        t.start()

    def _monitor_watchdog_loop(self) -> None:
        """If monitor daemon dies while we are locked, relaunch it."""
        monitor_script = _DAEMON_DIR / "mg_monitor.py"
        while not self._stop.is_set():
            if self.state in (State.SOFT_LOCK, State.SOCIAL_LOCK, State.HOSTILE_LOCK):
                monitor_pid_file = _MG_STATE_DIR / "monitor.pid"
                if monitor_pid_file.exists():
                    try:
                        pid = int(monitor_pid_file.read_text(encoding="utf-8").strip())
                    except (OSError, ValueError):
                        pid = 0
                    if pid > 0:
                        try:
                            os.kill(pid, 0)
                        except (OSError, SystemError):
                            log.warning("[Watchdog] Monitor daemon (PID %d) died while locked — relaunching", pid)
                            subprocess.Popen(
                                [sys.executable, str(monitor_script)],
                                cwd=str(_ROOT_DIR),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            )
            self._stop.wait(0.5)

    def _tick(self, frame: np.ndarray, frame_no: int) -> None:
        if self.state == State.IDLE:
            self._tick_idle(frame)
        elif self.state == State.SCANNING:
            self._tick_scanning(frame, frame_no)
        elif self.state == State.ACTIVE:
            self._tick_active(frame, frame_no)
        elif self.state in (State.SOFT_LOCK, State.SOCIAL_LOCK, State.HOSTILE_LOCK):
            self._tick_soft_lock(frame, frame_no)

    def _maybe_enter_soft_lock_for_input_idle(self) -> bool:
        if getattr(self, "state", None) != State.ACTIVE:
            return False
        idle_seconds = get_idle_seconds()
        if not getattr(self, "_input_idle_soft_lock_armed", True):
            if idle_seconds <= SOFT_LOCK_IDLE_REARM_SECONDS:
                self._input_idle_soft_lock_armed = True
            elif time.monotonic() < getattr(self, "_soft_lock_release_grace_until", 0.0):
                return False
            else:
                self._input_idle_soft_lock_armed = True
        if idle_seconds < SOFT_LOCK_IDLE_SECONDS:
            return False
        self._enter_soft_lock(f"input_idle_{idle_seconds:.0f}s")
        return True

    def _handle_camera_read_failure(self) -> None:
        self._camera_read_failures += 1
        log.warning(
            "Camera read failed (%d/%d)",
            self._camera_read_failures,
            CAMERA_READ_FAILURES_BEFORE_UNAVAILABLE,
        )
        if self._camera_read_failures < CAMERA_READ_FAILURES_BEFORE_UNAVAILABLE:
            return
        log.warning("Camera read failure threshold reached - entering CAMERA_UNAVAILABLE")
        self._camera_read_failures = 0
        self._release_camera()
        self._transition(State.CAMERA_UNAVAILABLE)

    def _tick_idle(self, frame: np.ndarray) -> None:
        if self.presence.has_face(frame):
            log.info("STATE: IDLE -> SCANNING (face detected)")
            self._stranger_frames = 0
            self._transition(State.SCANNING)

    def _tick_scanning(self, frame: np.ndarray, frame_no: int) -> None:
        result = self._require_face_engine().process_frame(frame)
        self._broadcast_detection_result(result)
        self._log_frame_result("Scanning", frame_no, result)

        if result.face_count == 0:
            self._absent_frames += 1
            if self._absent_frames < NO_FACE_LIVENESS_RESET_FRAMES:
                log.debug(
                    "Scanning: brief face loss (%d/%d frames), carrying liveness window",
                    self._absent_frames,
                    NO_FACE_LIVENESS_RESET_FRAMES,
                )
                self.ipc.broadcast_state("scanning")
                return

            self._require_face_engine().reset_liveness()
            log.info("STATE: SCANNING -> IDLE (face lost for %d frames)", self._absent_frames)
            self._scanning_owner_candidate_frames = 0
            self._transition(State.IDLE)
            return

        self._absent_frames = 0

        self.ipc.broadcast_state("scanning")

        # Use recognition_score directly — bypass face_engine's internal 3-frame
        # consensus counter which races against our own stranger_frames counter.
        score   = result.recognition_score
        liveness_ok = result.liveness_score >= LIVENESS_THRESHOLD

        if score >= RECOGNITION_THRESHOLD and liveness_ok:
            log.info("STATE: SCANNING -> ACTIVE (score=%.3f, liveness=%.3f)",
                     score, result.liveness_score)
            FaceState.set_recognized(liveness_score=result.liveness_score)
            self._absent_frames = 0
            self._stranger_frames = 0
            self._scanning_owner_ambiguity_grace_frames = 0
            self._scanning_owner_candidate_frames = 0
            self._transition(State.ACTIVE)
            return

        if self._is_scanning_fast_owner_candidate(result, liveness_ok):
            self._scanning_owner_candidate_frames += 1
            log.info(
                "Scanning: fast owner candidate (score=%.3f, presence=%.3f, "
                "liveness=%.3f, quality=%.2f, face_h=%.2f, center=%.2f, frames=%d/%d)",
                score,
                self._presence_confidence(result),
                result.liveness_score,
                result.frame_quality,
                result.face_height_frac,
                result.face_center_offset,
                self._scanning_owner_candidate_frames,
                SCANNING_FAST_OWNER_CONFIRM_FRAMES,
            )
            if self._scanning_owner_candidate_frames >= SCANNING_FAST_OWNER_CONFIRM_FRAMES:
                log.info(
                    "STATE: SCANNING -> ACTIVE (fast owner consensus score=%.3f, liveness=%.3f)",
                    score,
                    result.liveness_score,
                )
                FaceState.set_recognized(liveness_score=result.liveness_score)
                self._absent_frames = 0
                self._stranger_frames = 0
                self._scanning_owner_ambiguity_grace_frames = 0
                self._scanning_owner_candidate_frames = 0
                self._transition(State.ACTIVE)
            return

        self._scanning_owner_candidate_frames = 0

        if self._is_scanning_owner_ambiguous(result, liveness_ok):
            current_grace = getattr(self, "_scanning_owner_ambiguity_grace_frames", 0)
            self._scanning_owner_ambiguity_grace_frames = max(
                current_grace,
                SCANNING_OWNER_AMBIGUITY_GRACE_FRAMES,
            )
            self._stranger_frames = 0
            log.debug(
                "Scanning: owner ambiguity held for more evidence "
                "(score=%.3f, presence=%.3f, liveness=%.3f, quality=%.2f, raw_faces=%d)",
                score,
                self._presence_confidence(result),
                result.liveness_score,
                result.frame_quality,
                result.raw_face_count,
            )
            return

        # Only count as a confirmed stranger when score is DEFINITIVELY low.
        # Scores 0.55-0.78 are uncertain (quality variation, slight angle) — not stranger.
        # Scores < 0.55 with liveness passing = different person confirmed.
        if self._is_stranger_evidence(result, liveness_ok):
            self._stranger_frames += 1
            log.info(
                "Scanning: definite stranger (score=%.3f, liveness=%.3f, "
                "smooth=%.3f, quality=%.2f, face_h=%.2f, center=%.2f, "
                "raw_faces=%d, frames=%d/%d)",
                score,
                result.liveness_score,
                result.smoothed_recognition_score,
                result.frame_quality,
                result.face_height_frac,
                result.face_center_offset,
                result.raw_face_count,
                self._stranger_frames,
                STRANGER_CONFIRM_FRAMES_SCANNING,
            )
            if self._stranger_frames >= STRANGER_CONFIRM_FRAMES_SCANNING:
                log.warning("STATE: SCANNING -> SOCIAL_LOCK (stranger confirmed)")
                self._soft_lock_stranger()
            return

        # Uncertain frame (low liveness OR score 0.55-0.78) — keep scanning, don't penalise
        self._stranger_frames = 0
        self._decay_scanning_owner_ambiguity_grace()
        log.debug(
            "Scanning: uncertain frame reason=%s (score=%.3f, smooth=%.3f, "
            "liveness=%.3f, quality=%.2f)",
            self._stranger_evidence_reason(result, liveness_ok),
            score,
            result.smoothed_recognition_score,
            result.liveness_score,
            result.frame_quality,
        )

    def _tick_active(self, frame: np.ndarray, frame_no: int) -> None:
        result = self._require_face_engine().process_frame(frame)
        self._broadcast_detection_result(result)
        self._log_frame_result("Active", frame_no, result)

        if result.face_count == 0:
            self._absent_frames += 1
            self._active_reacquire_grace_frames = max(
                self._active_reacquire_grace_frames,
                ACTIVE_REACQUIRE_GRACE_FRAMES,
            )
            log.debug(
                "Active: no face (%d/%d absent frames)",
                self._absent_frames,
                ABSENT_FRAMES_LOCK,
            )
            if self._absent_frames == NO_FACE_LIVENESS_RESET_FRAMES:
                self._require_face_engine().reset_liveness()
                FaceState.clear()
                log.debug(
                    "Active: reset liveness after %d consecutive no-face frames",
                    self._absent_frames,
                )
            self.ipc.broadcast_state("scanning")
            if self._absent_frames >= ABSENT_FRAMES_LOCK:
                log.warning("STATE: ACTIVE -> SOFT_LOCK (absent %d frames)", self._absent_frames)
                self._enter_soft_lock("owner_absent")
            return

        self._absent_frames = 0
        score       = result.recognition_score
        liveness_ok = result.liveness_score >= LIVENESS_THRESHOLD
        if not liveness_ok:
            FaceState.clear()
        elif score < RECOGNITION_THRESHOLD:
            FaceState.clear()

        if score >= ACTIVE_RECOGNITION_THRESHOLD and liveness_ok:
            self._stranger_frames = 0
            self._active_reacquire_grace_frames = 0
            self._decay_owner_continuity_grace()
            if score >= RECOGNITION_THRESHOLD:
                FaceState.set_recognized(liveness_score=result.liveness_score)
            self.ipc.broadcast_state("active",
                                     confidence=self._presence_confidence(result),
                                     liveness=result.liveness_score)
            return

        # Only count as stranger when score is definitively not-owner (< 0.55).
        # Brief quality drops (0.55-0.78) are uncertain — reset stranger counter.
        if self._is_stranger_evidence(result, liveness_ok):
            FaceState.clear()
            self._stranger_frames += 1
            log.warning(
                "Active: definite stranger (score=%.3f, liveness=%.3f, "
                "smooth=%.3f, quality=%.2f, face_h=%.2f, center=%.2f, "
                "raw_faces=%d, select_reason=%s, sticky_iou=%.2f, kalman_iou=%.2f, "
                "template=%d, frames=%d/%d)",
                score,
                result.liveness_score,
                result.smoothed_recognition_score,
                result.frame_quality,
                result.face_height_frac,
                result.face_center_offset,
                result.raw_face_count,
                result.selection_reason,
                result.sticky_iou,
                result.predicted_iou,
                result.best_template_index,
                self._stranger_frames,
                STRANGER_CONFIRM_FRAMES_ACTIVE,
            )
            if self._stranger_frames >= STRANGER_CONFIRM_FRAMES_ACTIVE:
                log.warning("STATE: ACTIVE -> SOCIAL_LOCK (stranger while active)")
                self._soft_lock_stranger()
            else:
                self.ipc.broadcast_state("stranger")
            self._decay_active_reacquire_grace()
            self._decay_owner_continuity_grace()
        else:
            reason = self._stranger_evidence_reason(result, liveness_ok)
            liveness_jitter = self._is_owner_liveness_jitter(result, reason, liveness_ok)
            if self._is_owner_continuity_dip(result, reason, liveness_ok) or liveness_jitter:
                confidence = self._presence_confidence(result)
                if liveness_jitter:
                    confidence = min(confidence, max(0.0, RECOGNITION_THRESHOLD - 1e-3))
                    reason = "owner_liveness_jitter"
                self._owner_continuity_grace_frames = max(
                    self._owner_continuity_grace_frames,
                    ACTIVE_CONTINUITY_GRACE_FRAMES,
                )
                log.info(
                    "Active: owner-continuity dip held active "
                    "reason=%s (score=%.3f, smooth=%.3f, liveness=%.3f, "
                    "quality=%.2f, face_h=%.2f, center=%.2f, raw_faces=%d)",
                    reason,
                    score,
                    result.smoothed_recognition_score,
                    result.liveness_score,
                    result.frame_quality,
                    result.face_height_frac,
                    result.face_center_offset,
                    result.raw_face_count,
                )
                self._stranger_frames = 0
                self._decay_active_reacquire_grace()
                self.ipc.broadcast_state("active", confidence=confidence, liveness=result.liveness_score)
                return

            if liveness_ok and score < STRANGER_SCORE_THRESHOLD:
                log.info(
                    "Active: low-score face treated as uncertain "
                    "reason=%s (score=%.3f, smooth=%.3f, liveness=%.3f, "
                    "quality=%.2f, face_h=%.2f, center=%.2f, raw_faces=%d)",
                    reason,
                    score,
                    result.smoothed_recognition_score,
                    result.liveness_score,
                    result.frame_quality,
                    result.face_height_frac,
                    result.face_center_offset,
                    result.raw_face_count,
                )
            self._stranger_frames = 0
            self._decay_active_reacquire_grace()
            self._decay_owner_continuity_grace()
            self.ipc.broadcast_state("scanning")

    def _tick_soft_lock(self, frame: np.ndarray, frame_no: int) -> None:
        if not self._is_soft_lock_verifying():
            self.ipc.broadcast_state(self._lock_overlay_state_name(), detail="Press Space to verify")
            return

        liveness_mode = self._soft_lock_liveness_mode()
        result = self._require_face_engine().process_frame(frame, liveness_mode=liveness_mode)
        self._broadcast_detection_result(result)
        self._log_frame_result("SoftLock", frame_no, result)

        if result.face_count == 0:
            self._absent_frames += 1
            self._soft_lock_fast_pass_frames = 0
            self.ipc.broadcast_state("verifying_lock", detail="Look at camera")
            return

        self._absent_frames = 0
        score = result.recognition_score
        liveness_threshold = (
            BURST_FAST_LIVENESS_THRESHOLD
            if liveness_mode == "fast" else LIVENESS_THRESHOLD
        )
        liveness_ok = result.liveness_score >= liveness_threshold

        self.ipc.broadcast_state(
            "verifying_lock",
            confidence=self._presence_confidence(result),
            liveness=result.liveness_score,
            detail="Face verification",
        )

        if liveness_mode == "fast":
            if score >= RECOGNITION_THRESHOLD and liveness_ok:
                self._soft_lock_fast_pass_frames += 1
                if self._soft_lock_fast_pass_frames >= BURST_FAST_CONFIRM_FRAMES:
                    self._clear_soft_lock(
                        confidence=self._presence_confidence(result),
                        liveness=result.liveness_score,
                    )
                return
            if result.liveness_score < 0.50:
                self._soft_lock_fast_pass_frames = 0
            return

        if score >= RECOGNITION_THRESHOLD and liveness_ok:
            self._clear_soft_lock(
                confidence=self._presence_confidence(result),
                liveness=result.liveness_score,
            )
            return

        if self._is_soft_lock_fast_owner_candidate(result, liveness_ok):
            self._soft_lock_owner_candidate_frames += 1
            log.info(
                "SoftLock: fast owner candidate (score=%.3f, presence=%.3f, "
                "liveness=%.3f, quality=%.2f, face_h=%.2f, center=%.2f, frames=%d/%d)",
                score,
                self._presence_confidence(result),
                result.liveness_score,
                result.frame_quality,
                result.face_height_frac,
                result.face_center_offset,
                self._soft_lock_owner_candidate_frames,
                SCANNING_FAST_OWNER_CONFIRM_FRAMES,
            )
            if self._soft_lock_owner_candidate_frames >= SCANNING_FAST_OWNER_CONFIRM_FRAMES:
                self._clear_soft_lock(
                    confidence=self._presence_confidence(result),
                    liveness=result.liveness_score,
                )
            return

        self._soft_lock_owner_candidate_frames = 0

        if self._is_stranger_evidence(result, liveness_ok):
            self._stranger_frames += 1
            if self._stranger_frames >= STRANGER_CONFIRM_FRAMES_SCANNING:
                self._soft_lock_stranger()
            return

        self._stranger_frames = 0

    def _soft_lock_liveness_mode(self) -> str:
        started_at = getattr(self, "_soft_lock_verification_started_at", 0.0)
        if started_at <= 0.0:
            return "full"
        if time.monotonic() - started_at <= BURST_FAST_PATH_SECONDS:
            return "fast"
        return "full"

    def _is_soft_lock_passive(self) -> bool:
        if self.state not in (State.SOFT_LOCK, State.SOCIAL_LOCK, State.HOSTILE_LOCK):
            return False
        if self._is_soft_lock_verifying():
            return False
        self._expire_soft_lock_verification_if_needed()
        return True

    def _is_soft_lock_verifying(self) -> bool:
        if self.state not in (State.SOFT_LOCK, State.SOCIAL_LOCK):
            return False
        return time.monotonic() <= getattr(self, "_soft_lock_verify_until", 0.0)

    def _expire_soft_lock_verification_if_needed(self) -> None:
        verify_until = getattr(self, "_soft_lock_verify_until", 0.0)
        if verify_until <= 0.0 or time.monotonic() <= verify_until:
            return
        self._soft_lock_verify_until = 0.0
        self._soft_lock_verification_started_at = 0.0
        self._soft_lock_owner_candidate_frames = 0
        self._soft_lock_fast_pass_frames = 0
        self._stranger_frames = 0
        self._on_verify_inconclusive()

    def _on_verify_inconclusive(self) -> None:
        now = time.monotonic()
        self._verify_failed_until = now + 2.0
        self._verify_cooldown_until = now + 5.0
        self.ipc.broadcast_state("verify_failed")
        log.info("[Verify] Inconclusive — cooldown until +5.0s")

    def _start_soft_lock_verification(self, detail: str) -> None:
        now = time.monotonic()
        self._soft_lock_verify_until = now + SOFT_LOCK_VERIFY_WINDOW_SECONDS
        self._soft_lock_verification_started_at = now
        self._soft_lock_owner_candidate_frames = 0
        self._soft_lock_fast_pass_frames = 0
        self._absent_frames = 0
        self._stranger_frames = 0
        self._verify_failed_until = 0.0
        if self.face_eng is not None:
            self.face_eng.reset_liveness()
        self.ipc.broadcast_state("verifying_lock", detail="Face verification")
        log.info("SoftLock: verification requested by %s", detail)

    def _lock_overlay_state_name(self) -> str:
        if self.state == State.SOCIAL_LOCK:
            return "social_lock"
        if self.state == State.HOSTILE_LOCK:
            return "hostile_lock"
        return "locked_passive"

    def _enter_soft_lock(self, reason: str) -> None:
        old = self.state
        self.state = State.SOFT_LOCK
        self._absent_frames = 0
        self._stranger_frames = 0
        self._active_reacquire_grace_frames = 0
        self._owner_continuity_grace_frames = 0
        self._scanning_owner_ambiguity_grace_frames = 0
        self._soft_lock_verify_until = 0.0
        self._soft_lock_verification_started_at = 0.0
        self._soft_lock_owner_candidate_frames = 0
        self._soft_lock_fast_pass_frames = 0
        self._background_processes_restricted = False
        FaceState.clear()
        self.ipc.broadcast_state("locked_passive", detail=reason)
        log.info("STATE: %s -> SOFT_LOCK (%s)", old.name, reason)
        try:
            (_MG_STATE_DIR / "lock_state.txt").write_text("LOCKED\n", encoding="utf-8")
        except OSError:
            pass

    def _clear_soft_lock(self, *, confidence: float, liveness: float) -> None:
        self._soft_lock_verify_until = 0.0
        self._soft_lock_verification_started_at = 0.0
        self._soft_lock_owner_candidate_frames = 0
        self._soft_lock_fast_pass_frames = 0
        FaceState.set_recognized(liveness_score=liveness)
        self._transition(State.ACTIVE)
        self.ipc.broadcast_state("active", confidence=confidence, liveness=liveness)

        # Write UNLOCKED + schedule teardown so overlay dissolve sequence completes first.
        # The overlay exit sequence (verified → welcome → fade) takes ~2.4s.
        log.info("[ClearSoftLock] Lock lifted — scheduling teardown in 3s")
        try:
            (_MG_STATE_DIR / "lock_state.txt").write_text("UNLOCKED\n", encoding="utf-8")
        except OSError as e:
            log.warning("Failed to write lock_state.txt: %s", e)
        self._exit_at = time.monotonic() + 3.0

    def _defer_input_idle_soft_lock(self) -> None:
        self._input_idle_soft_lock_armed = False
        self._soft_lock_release_grace_until = time.monotonic() + SOFT_LOCK_RELEASE_GRACE_SECONDS

    def _soft_lock_stranger(self) -> None:
        self._soft_lock_verify_until = 0.0
        self._soft_lock_verification_started_at = 0.0
        self._soft_lock_owner_candidate_frames = 0
        self._soft_lock_fast_pass_frames = 0
        FaceState.clear()
        self._transition(State.SOCIAL_LOCK)
        try:
            (_MG_STATE_DIR / "lock_state.txt").write_text("LOCKED\n", encoding="utf-8")
        except OSError:
            pass

    def _is_foreground_face(self, result) -> bool:
        return (
            result.face_height_frac >= STRANGER_MIN_FACE_HEIGHT and
            result.face_center_offset <= STRANGER_MAX_CENTER_OFFSET
        )

    def _stranger_evidence_reason(self, result, liveness_ok: bool) -> str:
        if not liveness_ok:
            return "low_liveness"
        if not self._is_foreground_face(result):
            return "background_geometry"
        if result.frame_quality < STRANGER_MIN_FRAME_QUALITY:
            return "low_quality"
        if result.recognition_score >= STRANGER_SCORE_THRESHOLD:
            return "uncertain_or_owner_score"
        if result.smoothed_recognition_score > STRANGER_MAX_SMOOTHED_SCORE:
            return "recent_owner_smooth"
        if self._is_scanning_owner_ambiguity_gracing():
            return "scanning_owner_ambiguity"
        if self._is_owner_continuity_gracing():
            return "owner_continuity_grace"
        if self._is_active_reacquiring():
            return "active_reacquiring"
        if self._is_owner_track_associated(result):
            return "owner_track_uncertain"
        return "definite_stranger"

    def _is_stranger_evidence(self, result, liveness_ok: bool) -> bool:
        return self._stranger_evidence_reason(result, liveness_ok) == "definite_stranger"

    def _is_scanning_owner_ambiguous(self, result, liveness_ok: bool) -> bool:
        if getattr(self, "state", None) != State.SCANNING or not liveness_ok:
            return False
        if not self._is_foreground_face(result) or result.frame_quality < STRANGER_MIN_FRAME_QUALITY:
            return False
        if self._presence_confidence(result) >= SCANNING_OWNER_AMBIGUITY_PRESENCE:
            return True
        return (
            result.raw_face_count > 1 and
            result.recognition_score >= SCANNING_OWNER_AMBIGUITY_MIN_SCORE
        )

    def _is_scanning_fast_owner_candidate(self, result, liveness_ok: bool) -> bool:
        if getattr(self, "state", None) != State.SCANNING or not liveness_ok:
            return False
        return self._is_fast_owner_candidate(result) or self._is_quick_owner_candidate(result)

    def _is_soft_lock_fast_owner_candidate(self, result, liveness_ok: bool) -> bool:
        if getattr(self, "state", None) not in (State.SOFT_LOCK, State.SOCIAL_LOCK) or not liveness_ok:
            return False
        return self._is_fast_owner_candidate(result) or self._is_quick_owner_candidate(result)

    def _is_fast_owner_candidate(self, result) -> bool:
        if result.raw_face_count != 1 or result.face_count != 1:
            return False
        return (
            result.recognition_score >= SCANNING_FAST_OWNER_SCORE
            and self._presence_confidence(result) >= SCANNING_FAST_OWNER_PRESENCE
            and result.frame_quality >= SCANNING_FAST_OWNER_MIN_QUALITY
            and result.face_height_frac >= SCANNING_FAST_OWNER_MIN_FACE_HEIGHT
            and result.face_center_offset <= SCANNING_FAST_OWNER_MAX_CENTER_OFFSET
            and not getattr(result, "virtual_camera_detected", False)
            and not getattr(result, "camera_obstructed", False)
        )

    def _is_quick_owner_candidate(self, result) -> bool:
        if result.raw_face_count != 1 or result.face_count != 1:
            return False
        track_iou = max(float(result.sticky_iou), float(result.predicted_iou))
        return (
            result.recognition_score >= SCANNING_QUICK_OWNER_SCORE
            and self._presence_confidence(result) >= SCANNING_QUICK_OWNER_PRESENCE
            and result.liveness_score >= SCANNING_QUICK_OWNER_MIN_LIVENESS
            and result.frame_quality >= SCANNING_QUICK_OWNER_MIN_QUALITY
            and result.face_height_frac >= SCANNING_QUICK_OWNER_MIN_FACE_HEIGHT
            and result.face_center_offset <= SCANNING_QUICK_OWNER_MAX_CENTER_OFFSET
            and track_iou >= SCANNING_QUICK_OWNER_MIN_TRACK_IOU
            and result.selection_reason in {"sticky_iou", "kalman_iou", "identity"}
            and not getattr(result, "virtual_camera_detected", False)
            and not getattr(result, "camera_obstructed", False)
        )

    def _is_scanning_owner_ambiguity_gracing(self) -> bool:
        return (
            getattr(self, "state", None) == State.SCANNING and
            getattr(self, "_scanning_owner_ambiguity_grace_frames", 0) > 0
        )

    def _decay_scanning_owner_ambiguity_grace(self) -> None:
        frames = getattr(self, "_scanning_owner_ambiguity_grace_frames", 0)
        if frames > 0:
            self._scanning_owner_ambiguity_grace_frames = frames - 1

    @staticmethod
    def _is_owner_continuity_dip(result, reason: str, liveness_ok: bool) -> bool:
        if not liveness_ok:
            return False
        if reason == "recent_owner_smooth":
            return result.smoothed_recognition_score >= ACTIVE_CONTINUITY_SMOOTH_THRESHOLD
        if reason == "owner_track_uncertain":
            return result.recognition_score >= ACTIVE_CONTINUITY_TRACK_MIN_SCORE
        return False

    def _is_owner_liveness_jitter(self, result, reason: str, liveness_ok: bool) -> bool:
        if liveness_ok or reason != "low_liveness":
            return False
        if getattr(self, "state", None) != State.ACTIVE:
            return False
        if result.liveness_score < ACTIVE_LIVENESS_JITTER_FLOOR:
            return False
        if not self._is_foreground_face(result) or result.frame_quality < STRANGER_MIN_FRAME_QUALITY:
            return False
        if result.recognition_score >= ACTIVE_RECOGNITION_THRESHOLD:
            return True
        if (
            result.smoothed_recognition_score >= ACTIVE_CONTINUITY_SMOOTH_THRESHOLD and
            result.recognition_score >= ACTIVE_CONTINUITY_TRACK_MIN_SCORE
        ):
            return True
        return self._is_owner_track_associated(result)

    @staticmethod
    def _presence_confidence(result) -> float:
        return max(
            float(getattr(result, "recognition_score", 0.0)),
            float(getattr(result, "presence_confidence", 0.0)),
        )

    def _is_active_reacquiring(self) -> bool:
        return (
            getattr(self, "state", None) == State.ACTIVE and
            getattr(self, "_active_reacquire_grace_frames", 0) > 0
        )

    def _is_owner_continuity_gracing(self) -> bool:
        return (
            getattr(self, "state", None) == State.ACTIVE and
            getattr(self, "_owner_continuity_grace_frames", 0) > 0
        )

    @staticmethod
    def _is_owner_track_associated(result) -> bool:
        if result.selection_reason not in {"sticky_iou", "kalman_iou", "identity"}:
            return False
        return max(float(result.sticky_iou), float(result.predicted_iou)) >= 0.20

    def _decay_active_reacquire_grace(self) -> None:
        if self._active_reacquire_grace_frames > 0:
            self._active_reacquire_grace_frames -= 1

    def _decay_owner_continuity_grace(self) -> None:
        if self._owner_continuity_grace_frames > 0:
            self._owner_continuity_grace_frames -= 1

    def _log_frame_result(self, phase: str, frame_no: int, result) -> None:
        if LOG_EVERY_N_FRAMES <= 0 or frame_no % LOG_EVERY_N_FRAMES != 0:
            return
        log.info(
            "%s frame=%d faces=%d raw_faces=%d owner=%s score=%.3f liveness=%.3f "
            "live=%s smooth=%.3f presence=%.3f quality=%.2f face_h=%.2f center=%.2f select=%.2f "
            "reason=%s candidate=%.3f sticky_iou=%.2f kalman_iou=%.2f template=%d inference=%.1fms",
            phase,
            frame_no,
            result.face_count,
            result.raw_face_count,
            result.primary_user_present,
            result.recognition_score,
            result.liveness_score,
            result.liveness_passed,
            result.smoothed_recognition_score,
            self._presence_confidence(result),
            result.frame_quality,
            result.face_height_frac,
            result.face_center_offset,
            result.selected_face_score,
            result.selection_reason,
            result.candidate_owner_score,
            result.sticky_iou,
            result.predicted_iou,
            result.best_template_index,
            result.inference_ms,
        )

    def _broadcast_detection_result(self, result) -> None:
        service_ipc = getattr(self, "service_ipc", None)
        if service_ipc is not None:
            service_ipc.broadcast_detection_result(result)

    def _transition(self, new_state: State) -> None:
        old = self.state
        self.state = new_state

        if new_state == State.IDLE:
            self._absent_frames = 0
            self._stranger_frames = 0
            self._active_reacquire_grace_frames = 0
            self._owner_continuity_grace_frames = 0
            self.motion.reset()
            self.ipc.broadcast_state("idle")
        elif new_state == State.SCANNING:
            self._absent_frames = 0
            self._stranger_frames = 0
            self._active_reacquire_grace_frames = 0
            self._owner_continuity_grace_frames = 0
            self.ipc.broadcast_state("scanning")
        elif new_state == State.ACTIVE:
            self._absent_frames = 0
            self._stranger_frames = 0
            self._active_reacquire_grace_frames = 0
            self._owner_continuity_grace_frames = 0
            self._soft_lock_verify_until = 0.0
            self._soft_lock_verification_started_at = 0.0
            self._soft_lock_owner_candidate_frames = 0
            self._soft_lock_fast_pass_frames = 0
            self._defer_input_idle_soft_lock()
        elif new_state == State.LOCKED:
            self._absent_frames = 0
            self._stranger_frames = 0
            self._active_reacquire_grace_frames = 0
            self._owner_continuity_grace_frames = 0
            self.ipc.broadcast_state("locked")
            threading.Timer(2.0, self._post_lock_idle).start()
        elif new_state == State.SOCIAL_LOCK:
            self._absent_frames = 0
            self._stranger_frames = 0
            self._active_reacquire_grace_frames = 0
            self._owner_continuity_grace_frames = 0
            self._scanning_owner_ambiguity_grace_frames = 0
            self._soft_lock_verify_until = 0.0
            self._soft_lock_verification_started_at = 0.0
            self._soft_lock_owner_candidate_frames = 0
            self._soft_lock_fast_pass_frames = 0
            self._background_processes_restricted = False
            self.ipc.broadcast_state("social_lock", detail="Unknown person detected")
        elif new_state == State.HOSTILE_LOCK:
            self._absent_frames = 0
            self._stranger_frames = 0
            self._active_reacquire_grace_frames = 0
            self._owner_continuity_grace_frames = 0
            self._scanning_owner_ambiguity_grace_frames = 0
            self._soft_lock_verify_until = 0.0
            self._soft_lock_verification_started_at = 0.0
            self._soft_lock_owner_candidate_frames = 0
            self._soft_lock_fast_pass_frames = 0
            self._background_processes_restricted = False
            FaceState.clear()
            self.ipc.broadcast_state("hostile_lock", detail="Security hold")
            lock_workstation()
            try:
                (_MG_STATE_DIR / "lock_state.txt").write_text("LOCKED\n", encoding="utf-8")
            except OSError:
                pass
        elif new_state == State.SYSTEM_LOCKED:
            self._absent_frames = 0
            self._stranger_frames = 0
            self._active_reacquire_grace_frames = 0
            self._owner_continuity_grace_frames = 0
            self._scanning_owner_ambiguity_grace_frames = 0
            self._soft_lock_verify_until = 0.0
            self._soft_lock_verification_started_at = 0.0
            self._soft_lock_owner_candidate_frames = 0
            self._soft_lock_fast_pass_frames = 0
            self._release_camera()
            FaceState.clear()
            self.ipc.broadcast_state("locked")
        elif new_state == State.CAMERA_UNAVAILABLE:
            self._absent_frames = 0
            self._stranger_frames = 0
            self._active_reacquire_grace_frames = 0
            self._owner_continuity_grace_frames = 0
            self._scanning_owner_ambiguity_grace_frames = 0
            FaceState.clear()
            self.ipc.broadcast_state("idle")

        log.info("STATE: %s -> %s", old.name, new_state.name)

    def _post_lock_idle(self) -> None:
        if self.state == State.LOCKED:
            self.state = State.IDLE
            self.motion.reset()
            self.ipc.broadcast_state("idle")
            log.info("Post-lock: returned to IDLE monitoring")

    def _require_face_engine(self) -> FaceEngine:
        if self.face_eng is None:
            raise RuntimeError("FaceEngine is not initialized")
        return self.face_eng


def main() -> None:
    global _ACTIVE_DAEMON
    daemon = MajestyGuardDaemon()
    _ACTIVE_DAEMON = daemon

    def _sig_handler(sig, frame):
        log.info("Signal %d received - stopping", sig)
        daemon.stop()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)
    daemon.start()


if __name__ == "__main__":
    import ctypes
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, True, "Global\\MajestyGuardDaemon")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)

    # Hide console window (no-op when launched via pythonw.exe)
    _hwnd_console = ctypes.windll.kernel32.GetConsoleWindow()
    if _hwnd_console:
        ctypes.windll.user32.ShowWindow(_hwnd_console, 0)  # SW_HIDE

    import atexit as _atexit
    def _emergency_unlock():
        """Write UNLOCKED on any unhandled exit so monitor stops relaunching a broken daemon.
        If we crash while locked, call LockWorkStation() to secure the system using Windows Lock.
        """
        try:
            _state_dir = Path(os.environ.get("LOCALAPPDATA", "C:/tmp")) / "MajestyGuard"
            _state_dir.mkdir(parents=True, exist_ok=True)
            
            # Read current lock state to see if it was locked
            lock_file = _state_dir / "lock_state.txt"
            was_locked_in_file = False
            if lock_file.exists():
                was_locked_in_file = lock_file.read_text(encoding="utf-8").strip() == "LOCKED"
            
            # Write UNLOCKED so monitor stops relaunching
            lock_file.write_text("UNLOCKED\n", encoding="utf-8")
            (_state_dir / "daemon.pid").unlink(missing_ok=True)
            
            # Check if this was a clean exit
            is_clean_teardown = False
            state_was_locked = False
            if _ACTIVE_DAEMON is not None:
                is_clean_teardown = getattr(_ACTIVE_DAEMON, "_is_tearing_down", False)
                state_was_locked = _ACTIVE_DAEMON.state in (
                    State.SOFT_LOCK, State.SOCIAL_LOCK, State.HOSTILE_LOCK, State.SYSTEM_LOCKED
                )
            
            force_lock_startup = os.environ.get("MG_FORCE_LOCK_STARTUP") == "1"
            
            should_lock = False
            if not is_clean_teardown:
                if was_locked_in_file or force_lock_startup or state_was_locked:
                    should_lock = True
            
            if should_lock:
                if not LOCK_ENABLED:
                    log.warning("LOCK SUPPRESSED on abnormal exit (set MG_ENABLE_LOCK=1 to enable real locking)")
                else:
                    # Restore taskbar so user can enter credentials on Windows lock screen
                    hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
                    if hwnd:
                        ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                    hwnd2 = ctypes.windll.user32.FindWindowW("Shell_SecondaryTrayWnd", None)
                    if hwnd2:
                        ctypes.windll.user32.ShowWindow(hwnd2, 5)
                    # Call Windows lock
                    ctypes.windll.user32.LockWorkStation()
        except Exception:
            pass

    _atexit.register(_emergency_unlock)

    main()
