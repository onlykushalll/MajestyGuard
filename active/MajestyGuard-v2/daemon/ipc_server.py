r"""
ipc_server.py — Named pipe broadcast server → Dynamic Island UI.

Pipe name: \\.\pipe\MajestyGuard_UI
Protocol: newline-terminated JSON, one message per connection.

The UI client connects, reads one JSON line, disconnects.
The daemon calls broadcast_state() whenever state changes.

State JSON schema:
  {"state": "active",   "confidence": 0.91, "liveness": 0.87}
  {"state": "scanning"}
  {"state": "stranger"}
  {"state": "locked"}
  {"state": "locked_passive", "detail": "idle_timeout"}
  {"state": "soft_locked", "detail": "idle_timeout"}
  {"state": "verifying_lock"}
  {"state": "social_lock"}
  {"state": "hostile_lock"}
  {"state": "idle"}
  {"state": "enrolling", "progress": 0.42, "detail": "Left 25 degrees"}
  {"state": "calibrating", "confidence": 0.86, "liveness": 0.78, "quality": 0.82}

Valid states: active | scanning | stranger | locked | locked_passive |
soft_locked | verifying_lock | social_lock | hostile_lock | idle | enrolling | calibrating
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Mapping, Optional

import pywintypes   # type: ignore
import win32file    # type: ignore
import win32pipe    # type: ignore
import win32security  # type: ignore

log = logging.getLogger(__name__)

PIPE_NAME = r"\\.\pipe\MajestyGuard_UI"
SERVICE_PIPE_NAME = r"\\.\pipe\MajestyGuard_CV"
VALID_STATES = frozenset({
    "active",
    "scanning",
    "stranger",
    "locked",
    "locked_passive",
    "soft_locked",
    "verifying_lock",
    "social_lock",
    "hostile_lock",
    "idle",
    "enrolling",
    "calibrating",
})


# ---------------------------------------------------------------------------
# Security descriptor — allow any local user to read (UI runs as same user)
# ---------------------------------------------------------------------------

def _build_sa() -> win32security.SECURITY_ATTRIBUTES:
    # Define SDDL: System (SY) and Admin (BA) Full Access, Interactive (IU) Read/Write
    sddl = "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GWGR;;;IU)"
    sd_compiled = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1
    )
    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = sd_compiled
    sa.bInheritHandle = 0
    return sa


# ---------------------------------------------------------------------------
# IPCServer — runs in its own thread, broadcasts latest state to UI clients
# ---------------------------------------------------------------------------

class IPCServer:
    """
    Runs a named-pipe server loop.  When the UI connects, it gets the latest
    state JSON immediately.  If no state has been set yet, it waits up to
    1 second then sends "idle".

    Thread-safe: call broadcast_state() from any thread.
    """

    def __init__(self):
        self._state: dict = {"state": "idle"}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API (called from daemon main loop)
    # ------------------------------------------------------------------

    def broadcast_state(
        self,
        state: str,
        confidence: Optional[float] = None,
        liveness: Optional[float] = None,
        progress: Optional[float] = None,
        quality: Optional[float] = None,
        face_position: Optional[float] = None,
        detail: Optional[str] = None,
    ) -> None:
        """Update the current state. Next UI poll will receive this."""
        if state not in VALID_STATES:
            log.warning("IPCServer: unknown state '%s' — ignoring", state)
            return
        payload: dict = {"state": state}
        if confidence is not None:
            payload["confidence"] = round(confidence, 4)
        if liveness is not None:
            payload["liveness"] = round(liveness, 4)
        if progress is not None:
            payload["progress"] = round(progress, 4)
        if quality is not None:
            payload["quality"] = round(quality, 4)
        if face_position is not None:
            payload["face_position"] = round(face_position, 4)
        if detail:
            payload["detail"] = detail
        with self._lock:
            self._state = payload
        log.debug("IPCServer: state → %s", payload)

    def get_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mg-ui-ipc", daemon=True)
        self._thread.start()
        log.info("IPCServer started on %s", PIPE_NAME)

    def stop(self) -> None:
        self._stop.set()
        # Unblock the waiting pipe by connecting to it briefly
        try:
            h = win32file.CreateFile(
                PIPE_NAME,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None,
            )
            win32file.CloseHandle(h)
        except pywintypes.error:
            pass
        if self._thread:
            self._thread.join(timeout=3.0)
        log.info("IPCServer stopped")

    # ------------------------------------------------------------------
    # Internal pipe loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        sa = _build_sa()
        while not self._stop.is_set():
            handle = None
            try:
                handle = win32pipe.CreateNamedPipe(
                    PIPE_NAME,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    4096, 4096, 0, sa,
                )
                # Block until UI client connects (or stop() unblocks us)
                win32pipe.ConnectNamedPipe(handle, None)

                if self._stop.is_set():
                    break

                # Send current state
                payload = json.dumps(self.get_state()) + "\n"
                win32file.WriteFile(handle, payload.encode("utf-8"))
                try:
                    win32file.FlushFileBuffers(handle)
                except pywintypes.error:
                    pass

            except pywintypes.error as e:
                if not self._stop.is_set():
                    log.warning("IPCServer pipe error: %s", e)
                    time.sleep(0.2)
            except Exception:
                log.exception("IPCServer unexpected error")
                time.sleep(0.5)
            finally:
                if handle is not None:
                    try:
                        win32pipe.DisconnectNamedPipe(handle)
                    except pywintypes.error:
                        pass
                    try:
                        win32file.CloseHandle(handle)
                    except pywintypes.error:
                        pass


def service_ipc_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Return whether the v2-to-service DetectionResult bridge should run."""
    import os

    source = os.environ if env is None else env
    return str(source.get("MG_ENABLE_SERVICE_IPC", "0")).strip() == "1"


def detection_result_payload(result) -> dict:
    """Convert a FrameResult-like object into the service wire protocol."""
    return {
        "MessageType": "DetectionResult",
        "FaceCount": int(getattr(result, "face_count", 0)),
        "PrimaryUserPresent": bool(getattr(result, "primary_user_present", False)),
        "RecognitionScore": round(float(getattr(result, "recognition_score", 0.0)), 4),
        "LivenessScore": round(float(getattr(result, "liveness_score", 0.0)), 4),
        "LivenessPassed": bool(getattr(result, "liveness_passed", False)),
        "VirtualCameraDetected": bool(getattr(result, "virtual_camera_detected", False)),
        "CameraObstructed": bool(getattr(result, "camera_obstructed", False)),
        "InferenceMs": round(float(getattr(result, "inference_ms", 0.0)), 1),
    }


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_detection_result_payload(payload: Mapping[str, object]) -> list[str]:
    """Return field names that violate the service DetectionResult schema."""
    required = {
        "MessageType",
        "FaceCount",
        "PrimaryUserPresent",
        "RecognitionScore",
        "LivenessScore",
        "LivenessPassed",
        "VirtualCameraDetected",
        "CameraObstructed",
        "InferenceMs",
    }
    issues: list[str] = []
    for field in sorted(required - set(payload)):
        issues.append(field)

    if payload.get("MessageType") != "DetectionResult":
        issues.append("MessageType")
    face_count = payload.get("FaceCount")
    if not isinstance(face_count, int) or isinstance(face_count, bool) or face_count < 0:
        issues.append("FaceCount")
    for field in ("PrimaryUserPresent", "LivenessPassed", "VirtualCameraDetected", "CameraObstructed"):
        if not isinstance(payload.get(field), bool):
            issues.append(field)
    for field in ("RecognitionScore", "LivenessScore"):
        value = payload.get(field)
        if not _is_number(value) or not 0.0 <= float(value) <= 1.0:
            issues.append(field)
    inference_ms = payload.get("InferenceMs")
    if not _is_number(inference_ms) or float(inference_ms) < 0.0:
        issues.append("InferenceMs")
    return sorted(set(issues))


def detection_result_json(result) -> str:
    return json.dumps(detection_result_payload(result), separators=(",", ":")) + "\n"


class ServiceIPCServer:
    """
    Default-off bridge from v2 daemon frames to the service DetectionResult pipe.

    The C# service owns ``MajestyGuard_CV`` as the named-pipe server. This class
    keeps the requested ServiceIPCServer name for the v2 daemon bridge, but it
    behaves as a reconnecting pipe client so it does not invert the service
    architecture from CREDENTIAL_PROVIDER_PLAN.md.
    """

    def __init__(
        self,
        pipe_name: str = SERVICE_PIPE_NAME,
        *,
        connect_timeout_ms: int = 250,
        reconnect_backoff_s: float = 1.0,
    ):
        self.pipe_name = pipe_name
        self.connect_timeout_ms = max(1, int(connect_timeout_ms))
        self.reconnect_backoff_s = max(0.1, float(reconnect_backoff_s))
        self._pipe = None
        self._running = False
        self._reconnecting = False
        self._lock = threading.Lock()
        self._last_payload: dict = detection_result_payload(type("EmptyResult", (), {})())

    def start(self) -> None:
        self._running = True
        self._begin_reconnect()
        log.info("ServiceIPCServer enabled for %s", self.pipe_name)

    def stop(self) -> None:
        self._running = False
        self._close_pipe()
        log.info("ServiceIPCServer stopped")

    def get_last_payload(self) -> dict:
        with self._lock:
            return dict(self._last_payload)

    def broadcast_detection_result(self, result) -> None:
        payload = detection_result_payload(result)
        with self._lock:
            self._last_payload = payload
        if not self._running:
            return
        self._send(json.dumps(payload, separators=(",", ":")) + "\n")

    def _send(self, line: str) -> None:
        with self._lock:
            pipe = self._pipe
        if pipe is None:
            self._begin_reconnect()
            return
        try:
            win32file.WriteFile(pipe, line.encode("utf-8"))
        except pywintypes.error as e:
            if getattr(e, "winerror", None) == 109:
                log.warning("ServiceIPCServer pipe broken; reconnecting")
            else:
                log.warning("ServiceIPCServer pipe write failed: %s", e)
            self._close_pipe()
            self._begin_reconnect()

    def _begin_reconnect(self) -> None:
        with self._lock:
            if self._reconnecting or not self._running:
                return
            self._reconnecting = True
        threading.Thread(target=self._reconnect_loop, name="mg-service-ipc", daemon=True).start()

    def _reconnect_loop(self) -> None:
        try:
            while self._running and self._pipe is None:
                try:
                    win32pipe.WaitNamedPipe(self.pipe_name, self.connect_timeout_ms)
                    handle = win32file.CreateFile(
                        self.pipe_name,
                        win32file.GENERIC_WRITE,
                        0,
                        None,
                        win32file.OPEN_EXISTING,
                        0,
                        None,
                    )
                    with self._lock:
                        self._pipe = handle
                    log.info("ServiceIPCServer connected to %s", self.pipe_name)
                    return
                except pywintypes.error:
                    time.sleep(self.reconnect_backoff_s)
        finally:
            with self._lock:
                self._reconnecting = False

    def _close_pipe(self) -> None:
        with self._lock:
            pipe = self._pipe
            self._pipe = None
        if pipe is not None:
            try:
                win32file.CloseHandle(pipe)
            except pywintypes.error:
                pass
