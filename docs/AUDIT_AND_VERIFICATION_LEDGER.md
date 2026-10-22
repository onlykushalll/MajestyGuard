# MajestyGuard — Consolidated Security, Code & UI/UX Audit Ledger

This document consolidates the six historical audit and verification reports conducted across the MajestyGuard codebase (June–September 2026). The raw historical point-in-time reports are preserved under `archive/historical-audits/`.

---

## 1. Security & Offensive Audit Summary (52 Canonical Findings — Sep 2026)

| Category | Key Remediations Landed & Verified | Status |
|---|---|---|
| **Biometric Gallery Protection (MG-001)** | Plaintext `embeddings_v2.npy` fallback removed (fail-closed); one-time SHA-256 pinned legacy migration to Windows DPAPI (`CryptProtectData`). | **FIXED & VERIFIED** |
| **Installer & Path Hygiene (MG-002 / MG-004)** | `Resolve-CvPythonPath` and `_resolve_dpapi_helper_path` reordered to prefer Program Files / ProgramData / install-root before any `C:\tmp` fallback. | **FIXED & VERIFIED** |
| **ONNX Model Integrity (MG-003 / MG-D-05)** | Trust-On-First-Use (TOFU) SHA-256 manifest (`%LOCALAPPDATA%\MajestyGuard\model_hashes.json`) backed by shipped `models/model_hashes.reference.json`. | **FIXED & VERIFIED** |
| **Named Pipe Authentication (MG-B-07)** | Kernel-derived `GetNamedPipeClientProcessId` check on both `MajestyGuard_CMD` and `MajestyGuard_UI` with `psutil.Process.create_time()` double-read TOCTOU guard and user-SID SDDL ACLs. | **FIXED & VERIFIED** |
| **Liveness & Anti-Replay Stack (MG-007 / MG-D-01 / MG-D-08)** | Warm-up frames capped at `0.65` (below `0.70` full gate); fast/quick/burst paths gated at `0.82`; session-scoped replay taint (`300s` decay) preserved across soft-lock retries; rPPG confident-negative SNR (`< 1.15`) heavy veto (`0.70/0.30`) and dynamic `_effective_fps()` window calculation. | **FIXED & VERIFIED** |
| **Virtual Camera Detection (MG-D-06)** | Native `pnputil.exe /enum-devices /class Camera` primary evidence + WMI/PowerShell fallbacks; synchronous re-evaluation on 30s TTL expiry; fail-closed on unverifiable identity. | **FIXED & VERIFIED** |
| **Certificate & Policy Hygiene (MG-C-06 / MG-E-03 / MG-E-04)** | Dev signing restricted to `LocalMachine\TrustedPublisher` (never `Root`); `Uninstall.ps1` and `setup/security_cleanup.ps1` purge legacy dev certs, `AppModelUnlock` keys, and machine-scope `MAJESTYGUARD_MUTUAL_AUTH_KEY`. | **FIXED & VERIFIED** |

---

## 2. Core Win32, IPC & Concurrency Bugs Resolved (`BUG-01` – `BUG-21`)

- **BUG-01 / BUG-02 (`ui/soft_lock.py`):** Explicit 64-bit `ctypes` `.argtypes`/`.restype` bindings (`LRESULT`, `WPARAM`, `LPARAM`, `HHOOK`) and synchronous hook-thread installation event (`_hooks_ready.wait(timeout=1.0)`) + safe join before releasing callback references.
- **BUG-03 / BUG-04 (`ui/island.py`):** Unified 60 FPS animation timer, particle-orb state renderers (`searching`, `working`, `resolving`, `guarded`), and `blipp` contraction exit sequence.
- **BUG-05 / BUG-06 (`ui/main.py`):** 64-bit unsigned `INVALID_HANDLE_VALUE` check (`18446744073709551615`) and background-threaded `CommandWriter` with honest delivery confirmation.
- **BUG-08 / BUG-09 / BUG-10 (`daemon/`):** Closed `log_fh` handle in `mg_monitor.py`, non-blocking `PeekNamedPipe` before `ReadFile` in `cmd_server.py`, and atomic temp-file + `Path.replace()` state writes in `main.py`.
- **BUG-11 / BUG-21 (`daemon/face_engine.py`, `daemon/main.py`):** Kalman filter `np.linalg.pinv` fallback on singular matrices and fail-closed workstation lock on repeated camera read/open failures while locked.

---

## 3. UI/UX & Multi-Monitor Verification Ledger (`U1` – `U37`)

- **Keyboard & Media Isolation (`U1`, `soft_lock.py`):** Only `WM_KEYDOWN`/`WM_SYSKEYDOWN` are consumed; `WM_KEYUP` passes through to prevent stuck keys. `Space` and `Tab` are consumed at the hook level and routed via thread-safe Qt signals (`hook_space_pressed`, `hook_tab_pressed`). `VK_MEDIA_STOP` is sent on lock engage.
- **Zero Unblurred Frame Retention (`U2`, `test_privacy_audit.py`):** Unblurred desktop capture is zeroed (`shot_image.fill(0)`) immediately after downscaling; camera frames and face embeddings are zeroed on every exit path in `FaceEngine.process_frame`.
- **Multi-Monitor Glass + Primary-Monitor HUD (`U16`, `U25`, `soft_lock.py`):** Fullscreen glass shield spans `virtualGeometry()` across all connected displays (updating dynamically on `screenAdded`/`screenRemoved`/`geometryChanged`), while the clock, status pill, brand signature, guidance capsule, and fallback button anchor to the primary screen (`_hud_rect()`).
