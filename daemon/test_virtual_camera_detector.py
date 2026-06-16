import pytest
import winreg
import subprocess
import time
from types import SimpleNamespace
from virtual_camera_detector import (
    VirtualCameraDetector,
    is_virtual_camera,
    invalidate_camera_cache,
    _VIRTUAL_CAMERA_CLSIDS
)

def test_obs_virtual_camera_clsid_stays_blocked():
    assert "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}" in _VIRTUAL_CAMERA_CLSIDS

def test_standard_vfw_capture_class_is_not_blocked():
    assert "{860BB310-5D01-11D0-BD3B-00A0C911CE86}" not in _VIRTUAL_CAMERA_CLSIDS

# ── Mock Classes for Winreg and Subprocess ───────────────────────────────────

class MockKey:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def Close(self):
        pass

class MockCompletedProcess:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

# Helper to configure mock subprocess responses
def get_mock_run(wmic_output="", ps_mf_output="", ps_hw_output="", ps_name_output=""):
    def mock_run(args, **kwargs):
        cmd = args[0]
        if cmd == "wmic":
            return MockCompletedProcess(0, wmic_output)
        elif cmd == "powershell":
            ps_cmd = args[-1]
            if "VideoCapture" in ps_cmd and "ConvertTo-Json" in ps_cmd:
                return MockCompletedProcess(0, ps_mf_output)
            elif "VideoCapture" in ps_cmd:
                return MockCompletedProcess(0, ps_name_output)
            elif "Get-PnpDevice" in ps_cmd:
                return MockCompletedProcess(0, ps_hw_output)
        return MockCompletedProcess(1, "")
    return mock_run

# ── Tests for VirtualCameraDetector ──────────────────────────────────────────

def test_virtual_camera_detector_caching(monkeypatch):
    detector = VirtualCameraDetector()
    call_count = 0

    def mock_detect(camera_index):
        nonlocal call_count
        call_count += 1
        return False

    monkeypatch.setattr(detector, "_detect", mock_detect)

    # First call: invokes _detect
    assert not detector.is_virtual(0)
    assert call_count == 1

    # Second call: uses cache, call_count remains 1
    assert not detector.is_virtual(0)
    assert call_count == 1

    # Invalidate cache
    detector.invalidate_cache()
    assert not detector.is_virtual(0)
    assert call_count == 2

def test_virtual_camera_detector_by_name(monkeypatch):
    detector = VirtualCameraDetector()

    # Mock wmic returning OBS Virtual Camera
    monkeypatch.setattr(subprocess, "run", get_mock_run(wmic_output="Name=OBS Virtual Camera\n"))
    
    # OBS Virtual Camera should be flagged by name matching _VIRTUAL_CAMERA_NAMES
    assert detector._detect(0) is True

def test_virtual_camera_detector_by_clsid(monkeypatch):
    detector = VirtualCameraDetector()

    # Mock camera name as "OBS Virtual Camera"
    monkeypatch.setattr(subprocess, "run", get_mock_run(wmic_output="Name=OBS Virtual Camera\n"))

    # Mock winreg database containing OBS virtual CLSID
    def mock_open_key(key, sub_key, *args, **kwargs):
        if sub_key in [r"SOFTWARE\Classes\CLSID", "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}"]:
            return MockKey(sub_key)
        raise FileNotFoundError("Key not found")

    def mock_query_value_ex(key, name):
        if key.name == "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}":
            return "OBS Virtual Camera", 1
        raise FileNotFoundError("Value not found")

    monkeypatch.setattr(winreg, "OpenKey", mock_open_key)
    monkeypatch.setattr(winreg, "QueryValueEx", mock_query_value_ex)

    assert detector._check_clsid_blocklist("OBS Virtual Camera") is True
    # Non-matching name should not trigger block
    assert detector._check_clsid_blocklist("Integrated Webcam") is False

def test_virtual_camera_detector_by_mf_source(monkeypatch):
    detector = VirtualCameraDetector()

    # Mock camera name as "Integrated Webcam" to pass name check
    # Mock powershell MF source returning a list with "Windows Virtual Camera" at index 1
    ps_mf_json = '[{"Name":"Integrated Webcam","Id":"usb1"},{"Name":"Windows Virtual Camera","Id":"virtual1"}]'
    monkeypatch.setattr(subprocess, "run", get_mock_run(wmic_output="Name=Integrated Webcam\n", ps_mf_output=ps_mf_json))

    # Index 0 is hardware (Integrated Webcam)
    assert detector._check_mf_hardware_source(0) is True
    # Index 1 is confirmed virtual camera
    assert detector._check_mf_hardware_source(1) is False

def test_virtual_camera_detector_by_hw_id_virtual(monkeypatch):
    detector = VirtualCameraDetector()

    # Mock wmic returning a camera name, and setupapi returning a ROOT\\ device ID
    ps_hw_json = '[{"FriendlyName":"Integrated Webcam","HardwareID":["ROOT\\\\OBSVIRTUALCAMERA"]}]'
    monkeypatch.setattr(subprocess, "run", get_mock_run(wmic_output="Name=Integrated Webcam\n", ps_hw_output=ps_hw_json))

    # Should flag virtual since hw ID starts with ROOT\ (in _VIRTUAL_HW_ID_PREFIXES)
    assert detector._detect(0) is True

def test_virtual_camera_detector_by_hw_id_non_physical(monkeypatch):
    detector = VirtualCameraDetector()

    # Mock setupapi returning an unrecognized bus ID (not USB, PCI, ACPI, SWD)
    ps_hw_json = '[{"FriendlyName":"Integrated Webcam","HardwareID":["UNKNOWN\\\\DEVICEID"]}]'
    monkeypatch.setattr(subprocess, "run", get_mock_run(wmic_output="Name=Integrated Webcam\n", ps_hw_output=ps_hw_json))

    # Should flag virtual since hw ID lacks physical prefixes
    assert detector._detect(0) is True

def test_virtual_camera_detector_physical_happy_path(monkeypatch):
    detector = VirtualCameraDetector()

    # Mock normal physical camera name and hardware ID (USB\)
    ps_hw_json = '[{"FriendlyName":"Integrated Webcam","HardwareID":["USB\\\\VID_04F2&PID_B6D8"]}]'
    monkeypatch.setattr(subprocess, "run", get_mock_run(wmic_output="Name=Integrated Webcam\n", ps_hw_output=ps_hw_json))

    # Should return False (not virtual)
    assert detector._detect(0) is False

def test_public_api_wrappers(monkeypatch):
    # Test public entry points is_virtual_camera and invalidate_camera_cache
    call_count = 0
    def mock_is_virtual(self, index):
        nonlocal call_count
        call_count += 1
        return True

    monkeypatch.setattr(VirtualCameraDetector, "is_virtual", mock_is_virtual)
    
    assert is_virtual_camera(0) is True
    assert call_count == 1

    # Invalidate cache wrapper call
    cache_cleared = False
    def mock_invalidate_cache(self):
        nonlocal cache_cleared
        cache_cleared = True

    monkeypatch.setattr(VirtualCameraDetector, "invalidate_cache", mock_invalidate_cache)
    invalidate_camera_cache()
    assert cache_cleared
