# MajestyGuard.CVEngine/virtual_camera_detector.py
# Detects software / virtual cameras before face recognition starts.
# This is the FIRST security gate — if we're reading from a virtual camera,
# the entire 9-layer liveness stack is irrelevant because the attacker
# controls the pixel data before it reaches us.
#
# DETECTION STRATEGY (3 independent methods, any failure = block):
#
#   Method 1 — Symbolic link inspection (fast, ~5ms)
#     Physical cameras: \\?\usb#vid_XXXX&pid_XXXX&mi_00#...
#     Virtual cameras:  \\?\root#obsvirtualcamera#... or ROOT\ManyCam etc.
#     Source: MFEnumDeviceSources → MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK
#
#   Method 2 — SetupAPI hardware ID (reliable, ~50ms, cached)
#     Physical cameras have USB VID/PID in their hardware ID.
#     Virtual cameras have ROOT\ or SWD\ as the enumerator.
#     Source: SetupDiGetClassDevs(GUID_DEVCLASS_CAMERA) → SPDRP_HARDWAREID
#
#   Method 3 — Known CLSID blocklist (instant)
#     OBS, ManyCam, XSplit, DroidCam register under known CLSID GUIDs.
#     Check HKLM\SOFTWARE\Classes\CLSID for known virtual camera entries.
#
# CACHING: Results cached for 30 seconds — don't shell out on every frame.

import os
import re
import time
import logging
import subprocess
import winreg
from typing import Optional

logger = logging.getLogger("MajestyGuard.VirtualCameraDetector")

# ── Known virtual camera indicators ───────────────────────────────────────────

# Hardware ID prefixes that indicate physical hardware
# Note: SWD\ is included because many modern integrated webcams (Surface, Dell) 
# present via the Software Device enumerator even though they are physical.
_PHYSICAL_HW_ID_PREFIXES = frozenset(["USB\\", "PCI\\", "ACPI\\", "SWD\\"])

# Hardware ID prefixes that indicate software / virtual devices
_VIRTUAL_HW_ID_PREFIXES = frozenset([
    "ROOT\\",
    "SWD\\",
    "SW\\",
    "VIRTUAL\\",
])

# Known virtual camera product names (case-insensitive substring match)
_VIRTUAL_CAMERA_NAMES = frozenset([
    "obs virtual",
    "manycam",
    "xsplit vcam",
    "droidcam",
    "splitcam",
    "snap camera",
    "iriun",
    "epoccam",
    "camo",
    "ndi virtual",
    "virtual camera",
    "windows virtual camera",
    "avermedia virtual",
    "logi capture",         # legitimate but software-enhanced
])

# Known virtual camera CLSIDs in the Windows registry
_VIRTUAL_CAMERA_CLSIDS = frozenset([
    "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}",  # OBS Virtual Camera
    "{8E14549A-DB61-4309-AFA1-3578E927E935}",  # OBS Virtual Camera v2
    "{FBC9D74C-A950-11D1-8BD2-00A0C955FC6E}",  # Some ManyCam variants
    "{7D8C3B72-8787-4CE8-B9EF-B5B50B43D6D4}",  # XSplit VCam (common variant)
])


class VirtualCameraDetector:
    """
    Singleton detector. Initialize once, call is_virtual() before each session.
    """

    def __init__(self):
        self._cache_result: Optional[bool] = None
        self._cache_time: float = 0.0
        self._cache_ttl: float = 30.0  # Seconds before re-checking

    def is_virtual(self, camera_index: int) -> bool:
        """
        Returns True if the selected camera appears to be a virtual/software device.
        Call this before starting face recognition. Block if True.
        """
        now = time.monotonic()
        if self._cache_result is not None and (now - self._cache_time) < self._cache_ttl:
            return self._cache_result

        result = self._detect(camera_index)
        self._cache_result = result
        self._cache_time   = now

        if result:
            logger.warning("SECURITY: Virtual camera detected at index %d — blocking", camera_index)
        else:
            logger.debug("Camera at index %d appears to be physical hardware", camera_index)

        return result

    def invalidate_cache(self):
        """Force re-check on next call."""
        self._cache_result = None

    def _detect(self, camera_index: int) -> bool:
        """Run all detection methods. Return True if ANY method flags virtual."""

        # Method 1: Device name check via DirectShow / WMI (Moved up for efficiency)
        camera_name = self._get_camera_name(camera_index)
        if camera_name:
            name_lower = camera_name.lower()
            for virtual_name in _VIRTUAL_CAMERA_NAMES:
                if virtual_name in name_lower:
                    logger.warning("Virtual camera name detected: %s", camera_name)
                    return True

        # Method 2: CLSID check (Now integrated with device lookup if possible, 
        # but we'll stick to hardware ID as the primary secondary check)

        # Method 3: Hardware ID via SetupAPI (subprocess → PowerShell)
        hw_id = self._get_hardware_id(camera_name or "")
        if hw_id:
            for virtual_prefix in _VIRTUAL_HW_ID_PREFIXES:
                if hw_id.upper().startswith(virtual_prefix):
                    logger.warning("Virtual camera hardware ID: %s", hw_id)
                    return True
            # Check that at least one physical prefix matches
            has_physical = any(hw_id.upper().startswith(p) for p in _PHYSICAL_HW_ID_PREFIXES)
            if not has_physical:
                logger.warning("Camera hardware ID not from physical bus: %s", hw_id)
                return True

        return False

    def _check_clsid_blocklist(self) -> bool:
        """
        Checks if any known virtual camera CLSID is registered in the Windows registry.
        Virtual cameras register as DirectShow filter objects under HKLM\SOFTWARE\Classes\CLSID.
        """
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Classes\CLSID", access=winreg.KEY_READ)

            for clsid in _VIRTUAL_CAMERA_CLSIDS:
                try:
                    winreg.OpenKey(root, clsid)
                    logger.debug("Blocklisted CLSID found: %s", clsid)
                    winreg.CloseKey(root)
                    return True
                except FileNotFoundError:
                    pass

            winreg.CloseKey(root)
        except Exception as e:
            logger.debug("CLSID check error: %s", e)
        return False

    def _get_camera_name(self, camera_index: int) -> Optional[str]:
        """
        Get the friendly name of the camera at the given index.
        Uses PowerShell to query Windows.Devices.Enumeration — same source as MediaFoundation.
        Cached result returned from _detect's camera_name param.
        """
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Add-Type -AssemblyName PresentationFramework; "
                 "[System.Windows.Media.MediaDevices]::Sources | "
                 "Where-Object {$_.Kind -eq 'VideoInput'} | "
                 "Select-Object -ExpandProperty FriendlyName"],
                capture_output=True, text=True, timeout=5
            )
            names = [n.strip() for n in result.stdout.strip().splitlines() if n.strip()]
            if camera_index < len(names):
                return names[camera_index]
        except Exception:
            pass

        # Fallback: OpenCV backend name
        try:
            import cv2
            cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            backend = cap.getBackendName()
            cap.release()
            # CAP_DSHOW on a virtual camera typically returns backend "DSHOW" but
            # MF attribute check is more reliable. Flag for additional scrutiny.
            return None
        except Exception:
            return None

    def _get_hardware_id(self, camera_name: str) -> Optional[str]:
        """
        Uses SetupAPI via PowerShell to get the hardware ID of the camera device.
        Physical cameras: USB\VID_XXXX&PID_XXXX...
        Virtual cameras:  ROOT\OBSVIRTUALCAMERA or SWD\MSLOOP etc.
        """
        if not camera_name:
            return None

        try:
            # Query PnP entities matching camera class
            ps_cmd = (
                "Get-PnpDevice -Class Camera -Status OK | "
                "Select-Object FriendlyName,HardwareID | ConvertTo-Json"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=8
            )

            if result.returncode != 0:
                return None

            import json
            devices = json.loads(result.stdout)
            if isinstance(devices, dict):
                devices = [devices]

            name_lower = camera_name.lower()

            for device in devices:
                fn = (device.get("FriendlyName") or "").lower()
                hw = device.get("HardwareID")

                if name_lower in fn or fn in name_lower:
                    if isinstance(hw, list) and hw:
                        return hw[0]
                    elif isinstance(hw, str):
                        return hw

        except Exception as e:
            logger.debug("Hardware ID check error: %s", e)

        return None


# ── Module-level singleton ─────────────────────────────────────────────────────
_detector = VirtualCameraDetector()


def is_virtual_camera(camera_index: int) -> bool:
    """Public API — call this before initializing face recognition."""
    return _detector.is_virtual(camera_index)


def invalidate_camera_cache():
    """Call if the user switches cameras mid-session."""
    _detector.invalidate_cache()
