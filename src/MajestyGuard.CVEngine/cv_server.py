# MajestyGuard.CVEngine/cv_server.py
# Named pipe server. Bridges the Python CV engine to the C# Windows Service.
#
# STARTUP:
#   1. Initialize FaceEngine (loads models, opens camera)
#   2. Connect to the Service via Named Pipe (MajestyGuard_CV)
#   3. Listen for commands (fps change, load embeddings, enroll)
#   4. Run detection loop at current FPS
#   5. Send FrameResult as JSON after each frame
#
# CODEX: The pipe client and JSON protocol are implemented.
#        Complete the main_loop() FPS throttling logic.

import os
import sys
import json
import time
import gc
import logging
import threading
import ctypes
import win32pipe
import win32file
import pywintypes

from face_engine import FaceEngine, FrameResult

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("MajestyGuard.CVServer")

# ── Configuration from environment variables (set by Worker.cs) ──────
PIPE_NAME   = os.environ.get("MG_CV_PIPE",    "MajestyGuard_CV")
MODEL_DIR   = os.environ.get("MG_MODEL_DIR",  os.path.join(os.path.dirname(__file__), "models"))
CAMERA_IDX  = int(os.environ.get("MG_CAMERA_IDX", "0"))


class CVServer:
    def __init__(self):
        self._engine   = FaceEngine(model_dir=MODEL_DIR, camera_idx=CAMERA_IDX)
        self._pipe     = None
        self._fps      = 1          # Start at 1 FPS (idle monitoring)
        self._running  = False
        self._lock     = threading.Lock()
        self._reconnect_lock = threading.Lock()
        self._reconnecting = False
        self._frame_count = 0
        self._last_heartbeat = 0.0
        self._paused = False

        # Commands received from Service
        self._pending_commands: list[dict] = []

    # ─────────────────────────────────────────────────────────────────
    # STARTUP
    # ─────────────────────────────────────────────────────────────────

    def start(self):
        logger.info("CV Server starting")
        logger.info("  Pipe: %s", PIPE_NAME)
        logger.info("  Model dir: %s", MODEL_DIR)
        logger.info("  Camera: %d", CAMERA_IDX)

        # Anti-debug: detect if someone attached a debugger
        self._check_debugger()

        if not self._engine.initialize():
            logger.error("FaceEngine failed to initialize. Exiting.")
            sys.exit(1)

        self._running = True
        self._connect_pipe()
        self._main_loop()

    # ─────────────────────────────────────────────────────────────────
    # PIPE CONNECTION
    # Reconnects automatically if Service restarts
    # ─────────────────────────────────────────────────────────────────

    def _connect_pipe(self):
        pipe_path = f"\\\\.\\pipe\\{PIPE_NAME}"
        logger.info("Connecting to pipe: %s", pipe_path)

        attempts = 0
        while self._running:
            try:
                # Wait up to 5s for the pipe to become available
                win32pipe.WaitNamedPipe(pipe_path, 5000)

                self._pipe = win32file.CreateFile(
                    pipe_path,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
                logger.info("Connected to Service pipe")

                # Start reader thread for incoming commands
                threading.Thread(
                    target=self._read_commands_loop,
                    daemon=True,
                    name="PipeReader",
                ).start()
                return

            except pywintypes.error as e:
                attempts += 1
                wait_s = min(0.2 * (2 ** attempts), 5.0)
                logger.warning("Pipe connect failed (%d): %s. Retry in %.1fs", attempts, e, wait_s)
                time.sleep(wait_s)

    def _read_commands_loop(self):
        """Background thread: reads commands from Service."""
        buffer = ""
        while self._running:
            try:
                _, data = win32file.ReadFile(self._pipe, 1024)
                buffer += data.decode("utf-8")

                # Messages are newline-delimited JSON
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        self._handle_command(json.loads(line))

            except pywintypes.error as e:
                if e.winerror == 109:  # ERROR_BROKEN_PIPE
                    logger.warning("Pipe disconnected — reconnecting")
                    self._pipe = None
                    self._begin_reconnect()
                    return
                logger.error("Pipe read error: %s", e)
                time.sleep(0.1)

    def _handle_command(self, cmd: dict):
        """
        Commands from Service:
          {"cmd": "set_fps", "fps": 10}
          {"cmd": "load_embeddings", "embeddings": [[0.1, ...], ...]}
          {"cmd": "enroll", "angle": "Front"}
          {"cmd": "shutdown"}
        """
        cmd_type = cmd.get("cmd")

        if cmd_type == "set_fps":
            new_fps = int(cmd.get("fps", 1))
            with self._lock:
                self._fps = max(1, min(30, new_fps))
            logger.info("FPS set to %d", self._fps)

        elif cmd_type == "load_embeddings":
            embeddings = cmd.get("embeddings", [])
            self._engine.load_enrolled_embeddings(embeddings)

        elif cmd_type == "set_det_size":
            width = int(cmd.get("w", 160))
            height = int(cmd.get("h", 160))
            self._engine.set_det_size(width, height)

        elif cmd_type == "enroll":
            angle = cmd.get("angle", "Front")
            logger.info("Enrollment capture requested: %s", angle)
            embedding = self._engine.capture_enrollment_frame()
            result = {
                "type": "EnrollResult",
                "angle": angle,
                "success": embedding is not None,
                "embedding": embedding.tolist() if embedding is not None else [],
            }
            self._send(result)

        elif cmd_type == "pause":
            logger.info("Pause command received — suspending detection loop")
            with self._lock:
                self._paused = True

        elif cmd_type == "resume":
            logger.info("Resume command received — resuming detection loop")
            with self._lock:
                self._paused = False

        elif cmd_type == "shutdown":
            logger.info("Shutdown command received")
            self._running = False

    # ─────────────────────────────────────────────────────────────────
    # MAIN DETECTION LOOP
    # ─────────────────────────────────────────────────────────────────

    def _main_loop(self):
        logger.info("Detection loop starting")

        while self._running:
            loop_start = time.perf_counter()

            with self._lock:
                target_fps = self._fps
                paused = self._paused

            if paused:
                time.sleep(0.5)
                continue

            # Process frame
            result = self._engine.process_frame()

            # Send result to Service
            self._send_result(result)
            self._frame_count += 1

            now = time.monotonic()
            if now - self._last_heartbeat >= 10.0:
                self._last_heartbeat = now
                self._send({
                    "MessageType": "Heartbeat",
                    "ProcessName": "CVEngine",
                    "CpuPercent": 0.0,
                    "RamBytes": 0,
                })

            if self._frame_count % 60 == 0:
                gc.collect()

            # FPS throttle: sleep for remainder of frame budget
            elapsed    = time.perf_counter() - loop_start
            frame_time = 1.0 / target_fps
            sleep_time = frame_time - elapsed

            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                # Running slower than target FPS — log if significantly behind
                if abs(sleep_time) > 0.05:
                    logger.debug(
                        "Frame processing too slow: %.0fms (target %.0fms)",
                        elapsed * 1000, frame_time * 1000,
                    )

        logger.info("Detection loop ended")
        self._engine.shutdown()

    # ─────────────────────────────────────────────────────────────────
    # PIPE WRITE
    # ─────────────────────────────────────────────────────────────────

    def _send_result(self, result: FrameResult):
        msg = {
            "MessageType":          "DetectionResult",
            "FaceCount":            result.face_count,
            "PrimaryUserPresent":   result.primary_user_present,
            "RecognitionScore":     round(result.recognition_score, 4),
            "LivenessScore":        round(result.liveness_score, 4),
            "LivenessPassed":       result.liveness_passed,
            "VirtualCameraDetected":result.virtual_camera_detected,
            "CameraObstructed":     result.camera_obstructed,
            "InferenceMs":          round(result.inference_ms, 1),
        }
        self._send(msg)

    def _send(self, obj: dict):
        if self._pipe is None:
            return
        try:
            line = json.dumps(obj) + "\n"
            win32file.WriteFile(self._pipe, line.encode("utf-8"))
        except pywintypes.error as e:
            if e.winerror == 109:  # ERROR_BROKEN_PIPE
                logger.warning("Pipe write failed: broken pipe — reconnecting")
                self._pipe = None
                self._begin_reconnect()
                return
            logger.error("Pipe write failed: %s", e)

    def _begin_reconnect(self):
        with self._reconnect_lock:
            if self._reconnecting or not self._running:
                return
            self._reconnecting = True

        def reconnect():
            try:
                self._connect_pipe()
            finally:
                with self._reconnect_lock:
                    self._reconnecting = False

        threading.Thread(target=reconnect, daemon=True, name="PipeReconnect").start()

    # ─────────────────────────────────────────────────────────────────
    # ANTI-DEBUG / ANTI-TAMPER
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _check_debugger():
        try:
            kernel32 = ctypes.windll.kernel32
            if kernel32.IsDebuggerPresent():
                logger.critical("Debugger detected — refusing to start")
                sys.exit(99)
        except Exception:
            pass


if __name__ == "__main__":
    server = CVServer()
    server.start()
