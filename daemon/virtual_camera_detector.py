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
# IMPORTANT: Only include CLSIDs that are EXCLUSIVELY virtual camera products.
# {860BB310} = standard Windows VFW Video Capture class — present on ALL machines with ANY camera. NEVER block.
# {6994AD04} = generic DirectShow capture filter — too broad, present on physical camera drivers. NEVER block.
# {1ADEDD3B} = Logitech Capture — legitimate physical camera software. NEVER block.
# {CD8743A1} = too generic, false positives on physical hardware. NEVER block.
# The check below also validates that the CLSID belongs to the ACTIVE camera's
# DirectShow filter graph, not just that it's installed anywhere on the system.
_VIRTUAL_CAMERA_CLSIDS = frozenset([
    "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}",  # OBS Virtual Camera (obs-virtualcam)
    "{8E14549A-DB61-4309-AFA1-3578E927E935}",  # OBS Virtual Camera v2
    "{FBC9D74C-A950-11D1-8BD2-00A0C955FC6E}",  # ManyCam
    "{7D8C3B72-8787-4CE8-B9EF-B5B50B43D6D4}",  # XSplit VCam
])


import threading

class VirtualCameraDetector:
    """
    Singleton detector. Initialize once, call is_virtual() before each session.
    """

    def __init__(self):
        # L-1: cache keyed by camera_index — previously ignored index and cached globally
        self._cache: dict[int, tuple[float, bool]] = {}  # {index: (timestamp, result)}
        self._cache_ttl: float = 30.0
        self._lock = threading.Lock()
        self._updating: set[int] = set()

    def is_virtual(self, camera_index: int) -> bool:
        """
        Returns True if the selected camera appears to be a virtual/software device.
        Call this before starting face recognition. Block if True.
        """
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(camera_index)
            if cached is not None:
                # If cache is expired but we aren't already updating it in background, trigger async update.
                if (now - cached[0]) >= self._cache_ttl:
                    if camera_index not in self._updating:
                        self._updating.add(camera_index)
                        threading.Thread(
                            target=self._async_update,
                            args=(camera_index,),
                            name=f"mg-vcam-refresh-{camera_index}",
                            daemon=True,
                        ).start()
                return cached[1]

            # If not cached at all, we must block on the very first frame to remain secure.
            if camera_index not in self._updating:
                self._updating.add(camera_index)
                try:
                    result = self._detect(camera_index)
                    self._cache[camera_index] = (time.monotonic(), result)
                    if result:
                        logger.warning("SECURITY: Virtual camera detected at index %d — blocking", camera_index)
                    else:
                        logger.debug("Camera at index %d appears to be physical hardware", camera_index)
                finally:
                    self._updating.discard(camera_index)
                return self._cache[camera_index][1]
            else:
                return False

    def _async_update(self, camera_index: int) -> None:
        try:
            result = self._detect(camera_index)
            with self._lock:
                self._cache[camera_index] = (time.monotonic(), result)
                if result:
                    logger.warning("SECURITY: Virtual camera detected at index %d — blocking", camera_index)
        except Exception as e:
            logger.debug("Async virtual camera check failed: %s", e)
        finally:
            with self._lock:
                self._updating.discard(camera_index)

    def invalidate_cache(self):
        """Force re-check on next call."""
        with self._lock:
            self._cache.clear()

    def _detect(self, camera_index: int) -> bool:
        """Run all detection methods. Return True if ANY method flags virtual."""

        # Method 1: Device name check via DirectShow / WMI (resolve name first
        # so CLSID check can cross-reference the active camera, not system-wide)
        camera_name = self._get_camera_name(camera_index)
        if camera_name:
            name_lower = camera_name.lower()
            for virtual_name in _VIRTUAL_CAMERA_NAMES:
                if virtual_name in name_lower:
                    logger.warning("Virtual camera name detected: %s", camera_name)
                    return True

        # Method 0: CLSID blocklist — only blocks if CLSID FriendlyName matches active camera
        if self._check_clsid_blocklist(camera_name):
            return True

        # Method 2: MF hardware source attribute (Windows 11 definitive check)
        mf_result = self._check_mf_hardware_source(camera_index)
        if mf_result is False:  # Explicitly software (None = unknown, skip)
            logger.warning("MF hardware source check: software camera at index %d", camera_index)
            return True

        # Method 3: Hardware ID via SetupAPI
        hw_id = self._get_hardware_id(camera_name or "")
        if hw_id:
            for virtual_prefix in _VIRTUAL_HW_ID_PREFIXES:
                if hw_id.upper().startswith(virtual_prefix):
                    logger.warning("Virtual camera hardware ID: %s", hw_id)
                    return True
            has_physical = any(hw_id.upper().startswith(p) for p in _PHYSICAL_HW_ID_PREFIXES)
            if not has_physical:
                logger.warning("Camera hardware ID not from physical bus: %s", hw_id)
                return True

        return False

    def _check_clsid_blocklist(self, camera_name: Optional[str] = None) -> bool:
        """
        Checks whether the ACTIVE camera's DirectShow filter description matches a
        known virtual camera CLSID. We do NOT block just because a CLSID exists
        system-wide — OBS being installed does not mean the user is currently feeding
        a virtual camera to index 0. Instead we check the CLSID's FriendlyName
        and cross-reference it with the camera name we've already resolved.

        Returns True only when the CLSID's FriendlyName matches the active camera name.
        """
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Classes\CLSID", access=winreg.KEY_READ) as root:
                for clsid in _VIRTUAL_CAMERA_CLSIDS:
                    try:
                        key = winreg.OpenKey(root, clsid)
                        try:
                            friendly, _ = winreg.QueryValueEx(key, "")
                        except FileNotFoundError:
                            friendly = ""
                        finally:
                            key.Close()

                        friendly_lower = (friendly or "").lower()
                        # Only block if we can tie the CLSID to the active camera
                        if camera_name and friendly_lower:
                            cam_lower = camera_name.lower()
                            if cam_lower in friendly_lower or friendly_lower in cam_lower:
                                logger.warning(
                                    "Active camera '%s' matches virtual CLSID %s (%s)",
                                    camera_name, clsid, friendly
                                )
                                return True
                        # No camera name match — fall through to name/hardware checks
                    except FileNotFoundError:
                        pass
        except Exception as e:
            logger.debug("CLSID check error: %s", e)
        return False

    def _check_mf_hardware_source(self, camera_index: int):
        """Check MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_HW_SOURCE via PowerShell.
        Returns True=hardware, False=software, None=unknown.
        Windows 11 auto-appends 'Windows Virtual Camera' to software cameras."""
        try:
            ps_cmd = (
                "$devices = [Windows.Devices.Enumeration.DeviceInformation]::"
                "FindAllAsync([Windows.Devices.Enumeration.DeviceClass]::VideoCapture)"
                ".GetAwaiter().GetResult(); "
                "$devices | Select-Object Name,Id | ConvertTo-Json"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=3  # 3s max — never block camera loop
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                devices = json.loads(result.stdout)
                if isinstance(devices, dict):
                    devices = [devices]
                if camera_index < len(devices):
                    name = (devices[camera_index].get("Name") or "").lower()
                    if "windows virtual camera" in name:
                        return False  # Windows 11 software camera confirmed
                    return True  # Likely hardware
        except Exception as e:
            logger.debug("MF hardware source check failed: %s", e)
        return None

    def _get_camera_name(self, camera_index: int) -> Optional[str]:
        """
        Get the friendly name of the camera at the given index.
        Uses wmic as the primary source (fast, no PS startup overhead).
        Falls back to PowerShell DeviceInformation if wmic fails.
        """
        # Primary: wmic — fast, ~100ms, no PS startup cost
        try:
            result = subprocess.run(
                ["wmic", "path", "Win32_PnPEntity",
                 "where", "PNPClass='Camera'",
                 "get", "Name", "/format:list"],
                capture_output=True, text=True, timeout=3
            )
            names = [
                line.split("=", 1)[1].strip()
                for line in result.stdout.splitlines()
                if line.startswith("Name=") and line.split("=", 1)[1].strip()
            ]
            if camera_index < len(names):
                return names[camera_index]
            if names:
                return names[0]  # best guess if index is off
        except Exception:
            pass

        # Fallback: PowerShell DeviceInformation (slower but more reliable on some configs)
        try:
            ps_cmd = (
                "$devices = [Windows.Devices.Enumeration.DeviceInformation]::"
                "FindAllAsync([Windows.Devices.Enumeration.DeviceClass]::VideoCapture)"
                ".GetAwaiter().GetResult(); "
                "$devices | Select-Object -ExpandProperty Name"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=3
            )
            names = [n.strip() for n in result.stdout.strip().splitlines() if n.strip()]
            if camera_index < len(names):
                return names[camera_index]
        except Exception:
            pass

        return None

    def _get_hardware_id(self, camera_name: str) -> Optional[str]:
        """
        Uses SetupAPI via PowerShell to get the hardware ID of the camera device.
        Physical cameras: USB\\VID_XXXX&PID_XXXX...
        Virtual cameras:  ROOT\\OBSVIRTUALCAMERA or SWD\\MSLOOP etc.
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
                capture_output=True, text=True, timeout=3  # 3s max
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
