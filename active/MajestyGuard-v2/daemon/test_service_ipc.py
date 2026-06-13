import json
import threading
import uuid
from types import SimpleNamespace

import pywintypes
import win32file
import win32pipe

from ipc_server import (
    SERVICE_PIPE_NAME,
    ServiceIPCServer,
    detection_result_json,
    detection_result_payload,
    service_ipc_enabled,
    validate_detection_result_payload,
)


def _frame_result(**overrides):
    data = {
        "face_count": 1,
        "primary_user_present": True,
        "recognition_score": 0.88234,
        "liveness_score": 0.81449,
        "liveness_passed": True,
        "virtual_camera_detected": False,
        "camera_obstructed": False,
        "inference_ms": 44.24,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_detection_result_payload_matches_service_schema():
    payload = detection_result_payload(_frame_result())

    assert payload == {
        "MessageType": "DetectionResult",
        "FaceCount": 1,
        "PrimaryUserPresent": True,
        "RecognitionScore": 0.8823,
        "LivenessScore": 0.8145,
        "LivenessPassed": True,
        "VirtualCameraDetected": False,
        "CameraObstructed": False,
        "InferenceMs": 44.2,
    }


def test_detection_result_json_is_newline_delimited_valid_json():
    line = detection_result_json(_frame_result(face_count=0, primary_user_present=False))

    assert line.endswith("\n")
    parsed = json.loads(line)
    assert parsed["MessageType"] == "DetectionResult"
    assert parsed["FaceCount"] == 0
    assert parsed["PrimaryUserPresent"] is False


def test_detection_result_payload_schema_validator_checks_required_types_and_ranges():
    assert validate_detection_result_payload(detection_result_payload(_frame_result())) == []

    issues = validate_detection_result_payload(
        {
            "MessageType": "DetectionResult",
            "FaceCount": -1,
            "PrimaryUserPresent": "yes",
            "RecognitionScore": 1.2,
            "LivenessScore": -0.1,
            "LivenessPassed": True,
            "VirtualCameraDetected": False,
            "CameraObstructed": False,
            "InferenceMs": -5.0,
        }
    )

    assert "FaceCount" in issues
    assert "PrimaryUserPresent" in issues
    assert "RecognitionScore" in issues
    assert "LivenessScore" in issues
    assert "InferenceMs" in issues


def test_service_ipc_flag_is_default_off():
    assert not service_ipc_enabled({})
    assert not service_ipc_enabled({"MG_ENABLE_SERVICE_IPC": "0"})
    assert service_ipc_enabled({"MG_ENABLE_SERVICE_IPC": "1"})


def test_service_ipc_server_queues_latest_message_without_pipe_connection():
    server = ServiceIPCServer(pipe_name=SERVICE_PIPE_NAME)

    server.broadcast_detection_result(_frame_result(recognition_score=0.5))

    assert server.get_last_payload()["RecognitionScore"] == 0.5
    assert server.pipe_name == SERVICE_PIPE_NAME


def test_service_ipc_server_writes_detection_result_to_connected_pipe():
    pipe_name = rf"\\.\pipe\MajestyGuard_CV_test_{uuid.uuid4().hex}"
    created = threading.Event()
    connected = threading.Event()
    received: list[str] = []
    errors: list[BaseException] = []

    def pipe_server():
        handle = None
        try:
            handle = win32pipe.CreateNamedPipe(
                pipe_name,
                win32pipe.PIPE_ACCESS_INBOUND,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_WAIT,
                1,
                4096,
                4096,
                0,
                None,
            )
            created.set()
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as exc:
                if exc.winerror != 535:
                    raise
            connected.set()
            _, data = win32file.ReadFile(handle, 4096)
            received.append(data.decode("utf-8"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            if handle is not None:
                try:
                    win32pipe.DisconnectNamedPipe(handle)
                except pywintypes.error:
                    pass
                win32file.CloseHandle(handle)

    thread = threading.Thread(target=pipe_server, daemon=True)
    thread.start()
    assert created.wait(2.0)

    server = ServiceIPCServer(pipe_name=pipe_name, connect_timeout_ms=100, reconnect_backoff_s=0.05)
    server.start()
    try:
        assert connected.wait(3.0)
        server.broadcast_detection_result(_frame_result(recognition_score=0.75))
        thread.join(timeout=3.0)
    finally:
        server.stop()

    assert errors == []
    assert len(received) == 1
    payload = json.loads(received[0])
    assert payload["MessageType"] == "DetectionResult"
    assert payload["RecognitionScore"] == 0.75
    assert validate_detection_result_payload(payload) == []
