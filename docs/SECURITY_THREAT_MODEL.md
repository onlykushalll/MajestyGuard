# MajestyGuard Security Threat Model

> **Scope**: User-space desktop soft-lock layered on top of an already signed-in Windows 11 session. MajestyGuard is **not** a Windows login replacement, Secure Desktop credential provider, or full-disk encryption system.

## Trust Boundaries

### 1. User-Space Daemon ↔ UI (Named Pipes)
- **Pipes**: `\\.\pipe\MajestyGuard_State` (daemon → UI, read-only broadcast) and `\\.\pipe\MajestyGuard_CMD` (UI → daemon, command injection).
- **Authentication**: PID-based client verification with TOCTOU stability checks (`_client_pid_is_stable`) using `psutil.Process.create_time()` double-read. The daemon only accepts commands from the UI process it launched (or a direct child).
- **SDDL ACL**: Pipes are restricted to `SYSTEM (SY)`, `Administrators (BA)`, and the current user's specific SID via `pipe_security.build_user_pipe_sddl()`. Falls back to `Interactive-User (IU)` with logged error on token query failure.
- **Threat**: A same-user process (malware already running in the user session) could attempt to connect. PID auth + SDDL mitigate but do not eliminate this — same-user malware with admin privileges can bypass any user-space protection.

### 2. Biometric Template Storage (DPAPI)
- **At rest**: Owner's enrollment gallery (`%LOCALAPPDATA%\MajestyGuard\embeddings_v2.npy`) is encrypted with Windows DPAPI (`CryptProtectData`) bound to the current user's credentials.
- **Legacy migration**: A one-time migration path exists for the original plaintext gallery, gated by exact SHA-256 hash match (`_LEGACY_V2_PLAINTEXT_SHA256`) before any `np.load` is attempted. Non-matching plaintext files are refused fail-closed.
- **Threat**: DPAPI user-scope encryption protects against offline disk theft and other-user access. It does **not** protect against same-user malware or a memory dump of the running process. TPM-bound key storage is on the roadmap.

### 3. ONNX Model Integrity (TOFU + Reference Hashes)
- **TOFU (Trust On First Use)**: Each model file's SHA-256 is pinned to `%LOCALAPPDATA%\MajestyGuard\model_hashes.json` on first load. Subsequent loads must match the pinned hash or the model is refused.
- **Shipped reference hashes**: `models/model_hashes.reference.json` contains known-good SHA-256 hashes for all shipped ONNX models. On first use (before the TOFU manifest exists), the reference manifest is checked, closing the first-use gap.
- **Threat**: An attacker who replaces a model file before the first run can pin a malicious hash (mitigated by the reference manifest). An attacker with write access to `%LOCALAPPDATA%` after first run can modify the TOFU manifest (mitigated by Authenticode signing of the installer, on the roadmap).

### 4. Liveness / Anti-Spoof Stack
- **Layers**: RGB anti-spoof ONNX model (MiniFASNet v2), LBP texture, specular reflection, color space, moiré/FFT, temporal blink tracking, boundary analysis, depth geometry, histogram consistency, CHROM rPPG pulse detection, and attention tracking.
- **Replay detection**: Perceptual hash (dHash) frame fingerprinting with hamming distance < 3. Duplicate frame counts trigger replay taint that persists across `reset_temporal_buffers()` (soft-lock retry) and decays only after `MG_REPLAY_TAINT_DECAY_SECONDS` (300s). Fully cleared only by `reset_session()`.
- **Known limitation (MG-008)**: A long (>20s), non-looping phone video replay can bypass the replay detector because the dHash fingerprints are sufficiently varied frame-to-frame. The multi-layer liveness stack (especially rPPG confident-negative veto at SNR < 1.15) provides the primary defense against this attack vector. Physical camera matrix testing is required to tune the full-path liveness threshold.

### 5. Virtual Camera Detection
- **Detection methods**: WMI name enumeration, PnP device hardware ID prefix checking (`ROOT\`, `SW\`, `VIRTUAL\`), CLSID blocklist cross-reference (registry), MediaFoundation source enumeration, and `pnputil.exe /enum-devices /class Camera` parsing.
- **Fail-closed**: If all detection backends return empty results, the camera is treated as virtual. Cache TTL is 30 seconds with synchronous re-evaluation on expiry (no stale cache serving).

### 6. Soft-Lock Overlay (UI)
- **Coverage**: Full virtual desktop span via `screen.virtualGeometry()` with dynamic resize on `screenAdded`/`screenRemoved`/`geometryChanged` signals.
- **Input capture**: Low-level keyboard hook blocks all keys except Tab (→ Windows lock) and Space (→ verify request). Cursor is clipped to the overlay window. Task Manager and taskbar are suppressed during lock.
- **Limitation**: This is a user-space overlay, not a Secure Desktop session. A sufficiently privileged process (or Safe Mode boot) can bypass it. The Tab → `LockWorkStation` fallback ensures the user always has a path to the genuine Windows lock screen.

## Roadmap
1. **Authenticode / SignPath OSS signing** — Eliminate Smart App Control blocks and protect against binary tampering.
2. **TPM-bound key storage** — Upgrade DPAPI user-scope to TPM-sealed keys for enrollment gallery.
3. **Mutual authentication for companion apps** — HMAC-based handshake for any future WHCDF or companion IPC bridges (currently disabled by default).
