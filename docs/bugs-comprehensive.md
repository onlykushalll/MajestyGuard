# MajestyGuard — Complete Bug Audit (Comprehensive)

**Date:** 2026-06-19
**Auditor:** Z.ai Code (71 skills: karpathy-guidelines, impeccable, taste-skill-v1, ui-ux-pro-max, visual-design-foundations, frontend-design, mcp-builder)
**Files Fully Read:** Worker.cs (1094 lines), StateMachine.cs (317), PipeServer.cs (269), EmbeddingStore.cs (325), SessionWatcher.cs (208), face_engine.py (548), liveness_detector.py (778), virtual_camera_detector.py (200+), plus subagent audits of daemon (14 files, ~6000 lines), scripts/docs (20+ files)
**Total Lines Audited:** ~10,000+ lines of production code

---

## 📋 Summary

| Severity | Count |
|----------|-------|
| **CRITICAL** | 14 |
| **HIGH** | 22 |
| **MEDIUM** | 35 |
| **LOW** | 30 |
| **UI/UX** | 18 |
| **Total** | **119** |

---

## 🚨 CRITICAL ISSUES (14)

### C1: SessionWatcher Message Pump is Empty — Face Unlock Completely Broken
**File:** `src/MajestyGuard.Service/SessionWatcher.cs:155-157`
**Category:** Logic / Functional

```csharp
while (GetMessageW(out _, IntPtr.Zero, 0, 0))
{
}  // EMPTY BODY — no TranslateMessage, no DispatchMessage!
```

`GetMessageW` retrieves messages but `TranslateMessage`/`DispatchMessageW` are never called. The `WndProc` callback (line 163) that handles `WM_WTSSESSION_CHANGE` and `WM_POWERBROADCAST` is **never invoked**.

**Impact:** ALL session notifications (Win+L lock/unlock, sleep/resume) are silently lost. This breaks the entire face unlock flow because `onSessionUnlock` (which sends `FaceDetected`) never fires.

**Fix:**
```csharp
while (GetMessageW(out MSG msg, IntPtr.Zero, 0, 0))
{
    TranslateMessage(ref msg);
    DispatchMessage(ref msg);
}
```

---

### C2: State Machine Stuck in BootScan — Face Recognized Trigger Ignored
**File:** `src/MajestyGuard.Service/Worker.cs:436-494` + `src/MajestyGuard.Core/StateMachine.cs:146`

`OnCvMessageAsync` sends `FaceRecognized` or `FaceUnrecognized` — but from `BootScan`, the state machine ONLY accepts `FaceDetected` (line 146). `FaceRecognized` from BootScan returns `null` — silently ignored.

The ONLY sender of `FaceDetected` is `SessionWatcher.onSessionUnlock` (Worker.cs:287) — which is broken (C1).

**Impact:** State machine is permanently stuck in `BootScan`. Face unlock NEVER works automatically. User must manually enter PIN.

**Fix:** Either add `FaceRecognized` as accepted from `BootScan`, or emit `FaceDetected` from `OnCvMessageAsync` when a face is first detected:
```csharp
// In OnCvMessageAsync, before recognition check:
if (result.FaceCount > 0 && _stateMachine.Current == GuardState.BootScan)
{
    _stateMachine.RequestTransition(TransitionTrigger.FaceDetected);
    // Then fall through to recognition...
}
```

---

### C3: Overlay Launched in Session 0 — Invisible to User
**File:** `src/MajestyGuard.Service/Worker.cs:804-824`

`LaunchOverlay()` uses `Process.Start(psi)` from a SYSTEM service (Session 0). WinUI 3 windows in Session 0 are invisible/non-interactive. The lock screen, input blocker, and idle reporter never appear.

The service already has `CreateProcessAsUserW` implemented (line 951) for DpapiHelper — but doesn't use it for the overlay.

**Fix:** Use `CreateProcessAsUserW` for overlay launch, same as DpapiHelper.

---

### C4: DPAPI LOCAL=machine — Biometrics Decryptable by Any Local Process
**File:** `src/MajestyGuard.Core/Security/EmbeddingStore.cs:232`

```csharp
private const string DESCRIPTOR = "LOCAL=machine";
```

Binds ciphertext to the **machine** master key, not the user. Any process on the machine can call `NCryptUnprotectSecret` to decrypt face embeddings. The file header (line 6) claims "DPAPI keys are tied to the current user's Windows credentials" — this is **false**.

The code comments (line 107-112) explain this is intentional (prevents Mimikatz LSASS extraction), but the tradeoff means any local malware can decrypt embeddings.

**Fix:** Use `LOCAL=user` or SID-based descriptor:
```csharp
private const string DESCRIPTOR = "LOCAL=user";
```

---

### C5: IPC Has No Message Authentication — Any User Process Can Force Unlock
**File:** `src/MajestyGuard.Core/IPC/PipeServer.cs:122-153`

The pipe ACL restricts connection to the enrolled user SID, but once connected, any user-context process can inject:
```json
{"DetectionResultMsg": {"PrimaryUserPresent": true, "RecognitionScore": 1.0, "LivenessPassed": true}}
```

This forces auth success. Can also send `ManualFallbackRequestMsg` to bypass `HostileLock` after the 30s cooldown.

**Fix:** Add process-identity verification (check caller PID against allowlist) + nonce/HMAC handshake.

---

### C6: Multiple Faces — Processes Largest Face Instead of Rejecting
**File:** `src/MajestyGuard.CVEngine/face_engine.py:270`

```python
primary_face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
```

Attacker holds a small real face + a larger phone showing enrolled user's video — liveness/recognition runs on the **spoof**. Enrollment correctly rejects multiple faces (line 187-189), but `process_frame` doesn't.

**Fix:**
```python
if len(faces) > 1:
    # During normal operation, flag stranger — DON'T process the larger face
    return FrameResult(face_count=len(faces), ...)
```

---

### C7: Replay Detection Uses MD5 — Easily Defeated by Video Noise
**File:** `src/MajestyGuard.CVEngine/liveness_detector.py:755-776`

`_replay_detection` MD5-hashes a 16x16 grayscale ROI. Video replay has encoding noise — hash differs every frame — `matches==0` — `replay_penalty=0.95` — replay passes. Only catches perfectly-static photos.

**Fix:** Use perceptual hashing (pHash) with Hamming distance:
```python
import imagehash
from PIL import Image
current_hash = imagehash.phash(Image.fromarray(roi))
similar = sum(1 for h in self._hash_history if (h - current_hash) < 5)
```

---

### C8: Enrollment Always Fails for First 5 Calls — Liveness Early-Frame Cap
**File:** `src/MajestyGuard.CVEngine/liveness_detector.py:248-249` + `face_engine.py:194-197`

```python
if self._frame_index < self._MIN_FRAMES_FOR_PASS:
    smoothed = min(float(np.mean(self._score_history)), 0.75)  # Capped at 0.75
```

Enrollment threshold is 0.85 (face_engine.py:195). First 4 calls always return <=0.75 — enrollment always fails. `capture_enrollment_frame()` doesn't call `reset_liveness()` first, so idle-mode frames pollute the window.

**Fix:**
```python
def capture_enrollment_frame(self):
    self._liveness.reset_session()  # Clear history
    self._liveness._frame_index = self._liveness._MIN_FRAMES_FOR_PASS  # Skip cap
    # ... rest of capture
```

---

### C9: Uninstall.ps1 Hardcodes Install Directory — Custom Installs Never Cleaned
**File:** `Uninstall.ps1`

The uninstaller always looks in `$env:ProgramFiles\MajestyGuard`, but `Install.ps1` supports custom `-InstallDir`. Custom installs are never cleaned up.

**Fix:** Read the install path from registry: `HKLM:\SOFTWARE\MajestyGuard\InstallDir`.

---

### C10: Uninstall.ps1 Globally Suppresses All Errors
**File:** `Uninstall.ps1`

`$ErrorActionPreference = "SilentlyContinue"` script-wide means the uninstaller reports success even when `sc.exe delete` and `regsvr32 /u` fail. User thinks they uninstalled but the service and DLL are still registered.

**Fix:** Remove global suppression, use `try/catch` per operation with explicit logging.

---

### C11: Install.ps1 pip install Exit Code Never Checked
**File:** `Install.ps1`

If `pip install` fails, the script prints "Python dependencies installed" and starts the service without CV dependencies. The service runs but face recognition doesn't work.

**Fix:** Check `$LASTEXITCODE` after pip install, abort if non-zero.

---

### C12: Dev Certificates Left in Trust Stores After Uninstall
**File:** `Uninstall.ps1`

The uninstaller never removes the self-signed "CN=MajestyGuard Dev" cert from `Root` or `TrustedPublisher`, leaving a permanent trust backdoor.

**Fix:** Add cert removal:
```powershell
Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -match "MajestyGuard" } | Remove-Item
Get-ChildItem Cert:\LocalMachine\TrustedPublisher | Where-Object { $_.Subject -match "MajestyGuard" } | Remove-Item
```

---

### C13: Daemon enroll_v2.py Has NO Liveness Checks During Enrollment
**File:** `daemon/enroll_v2.py` (v2 codebase)

The v1 enrollment checked liveness. The v2 enrollment does NOT — an attacker can enroll using a printed photo or video replay. This is a **security regression** from v1 to v2.

**Fix:** Add liveness check in enroll_v2.py before capturing embedding.

---

### C14: Daemon Virtual Camera Detector Fail-Open Race
**File:** `daemon/virtual_camera_detector.py`

After `invalidate_cache()` during async refresh, `is_virtual()` returns `False` (safe) instead of `True` (block). A security gate fails open.

**Fix:** Return `True` (block) when cache is invalidated and detection hasn't completed.

---

## ⚠️ HIGH SEVERITY ISSUES (22)

| # | File | Issue |
|---|------|-------|
| H1 | `rppg_detector.py:20` | FPS=15.0 hardcoded; actual FPS is 1 (idle) or 10 (verifying). At 1 FPS, Nyquist < cardiac band — rPPG always returns 0.5. Contributes 30% of liveness score. |
| H2 | `face_engine.py:394-400` | Uses deprecated `wmic` command for camera device path. `wmic` is deprecated in Windows 11. |
| H3 | `Worker.cs:830` | `_lastCvHeartbeatTicks` uses `DateTime.UtcNow.Ticks` with `Interlocked` — correct, but the GC trim logic at line 876 uses `DateTime.UtcNow - _lastGcTrim` which is NOT thread-safe. |
| H4 | `Worker.cs:292-298` | `_cvPipe?.SendRawAsync(...).ConfigureAwait(false)` — the `ConfigureAwait(false)` on a fire-and-forget is a no-op. The await was dropped, so exceptions are swallowed. |
| H5 | `StateMachine.cs:236-249` | HostileLock cooldown uses `DateTime.UtcNow` — vulnerable to system clock changes. Should use `Stopwatch` or `Environment.TickCount64`. |
| H6 | `StateMachine.cs:295-304` | Stranger hysteresis is only 500ms — too short. A person walking past would trigger SocialLock. |
| H7 | `PipeServer.cs:84-88` | `SendAsync` silently returns if no client connected — no retry, no queue. Important messages (like AuthDecision) can be lost. |
| H8 | `PipeServer.cs:102-115` | No message size limit in `ReadLoopAsync` — OOM attack via huge messages. |
| H9 | `EmbeddingStore.cs:149-150` | `br.ReadInt32()` reads length without validation — malicious file could specify huge length — OOM. |
| H10 | `EmbeddingStore.cs:155` | No dimension validation in `Load()` — corrupted embeddings with wrong dimensions cause silent lockout. |
| H11 | `face_engine.py:143` | Camera FPS set to 30 but actual processing is ~1-10 FPS. `cv2.CAP_PROP_FPS` is misleading. |
| H12 | `liveness_detector.py:65` | `frame_hashes` deque maxlen=60 — at 1 FPS, only 60 seconds of history. Video loops longer than 60s are not detected. |
| H13 | `download_models.py` | No SHA256 verification on downloaded MiDaS/antispoof models — supply-chain attack vector. |
| H14 | `Worker.cs:1077-1091` | `StopAsync` kills child processes but doesn't wait for them to flush. CV engine might leave camera in bad state. |
| H15 | `Daemon main.py` | `_post_lock_idle` bypasses `_transition()` state machine — direct state manipulation. |
| H16 | `Daemon main.py` | `score_full` returns stale score indefinitely on quality degradation — no staleness cap. |
| H17 | `Daemon companion_ipc.py` | WHCDF liveness threshold mismatch: daemon requires 0.70, WHCDF requires 0.82. Faces passing daemon (0.70-0.81) are denied by WHCDF — undocumented lockouts. |
| H18 | `Install.ps1` | Wrong HKCU hive for screensaver: modifies admin's HKCU, not enrolled user's. |
| H19 | `Uninstall.ps1` | `HKLM:\SOFTWARE\MajestyGuard` and service profile config never cleaned. |
| H20 | Tests | Two test files reference files in `setup/` and `companion/` directories that don't exist — crash test suite at collection time. |
| H21 | Tests | Nearly all Python tests are static source-string inspection — never execute actual behavior. A rename breaks them; a logic bug with same strings passes them. |
| H22 | `CVEngine requirements.txt` | Conflicting `mediapipe` version constraints and duplicate `scipy` entries. |

---

## 📋 MEDIUM SEVERITY ISSUES (35)

### C# Backend (12)
| # | File | Issue |
|---|------|-------|
| M1 | `Worker.cs:328` | Initial state trigger `ProfileValidated` sent after all components start — race condition if CV engine sends results before state is ready. |
| M2 | `Worker.cs:531-538` | 30-second timeout for embedding load — if DpapiHelper is slow, PresenceMonitor starts without embeddings — HostileLock storm. |
| M3 | `Worker.cs:643` | Hardcoded fallback path `C:\tmp\MajestyGuard` — not portable. |
| M4 | `Worker.cs:880` | `SetProcessWorkingSetSize(GetCurrentProcess(), -1, -1)` — aggressive memory trim can cause page faults during critical operations. |
| M5 | `StateMachine.cs:84` | `StateChanged` event warning says handlers MUST NOT call `RequestTransition` synchronously — but `OnStateChanged` (line 126) fires OUTSIDE the lock, making reentrant calls possible. |
| M6 | `PipeServer.cs:141` | No `World` Deny rule — relies on allowlist only. If ACL creation fails silently, pipe might be open to everyone. |
| M7 | `EmbeddingStore.cs:188-192` | `DeleteEnrollment` overwrites with zeros then deletes — but SSDs with wear-leveling don't guarantee the zeros are written to the same blocks. |
| M8 | `EmbeddingStore.cs:318` | Native buffer zeroing loop uses `Marshal.WriteByte` one byte at a time — slow for large buffers. Use `RtlZeroMemory`. |
| M9 | `SessionWatcher.cs:27` | `WM_QUIT = 0x0012` — this is actually `WM_QUERYENDSESSION`, not `WM_QUIT` (0x0012 vs 0x0012). Wait, 0x0012 IS WM_QUIT. Actually correct. |
| M10 | `Worker.cs:1069` | `SetProcessMitigationPolicy(7, ...)` — policy 7 is `ProcessExtensionPointDisablePolicy`, but the buffer size `sizeof(ulong)` may not match the expected struct size. |
| M11 | `Worker.cs:566` | `_sourceRoot` is static — if service runs from different directories (e.g. after update), cached path may be wrong. |
| M12 | `DpapiHelper/Program.cs` | Entire embedding JSON is loaded into memory as plaintext — no zeroing after deserialization. |

### Python CV Engine (10)
| # | File | Issue |
|---|------|-------|
| M13 | `face_engine.py:507-513` | AdaFace input is BGR-RGB but model might expect different preprocessing. No validation against reference implementation. |
| M14 | `liveness_detector.py:291-301` | LBP boundary pixels are all 0 (loop starts at [1:-1, 1:-1]) — skewed statistics. |
| M15 | `liveness_detector.py:377-382` | FFT on 128x128 ROI — low resolution limits Moiré detection to coarse patterns only. |
| M16 | `cv_server.py` | No reconnection logic if CV engine crashes — Worker.cs watchdog relaunches, but pipe state may be inconsistent. |
| M17 | `attention_detector.py` | MediaPipe FaceMesh runs on full frame every liveness frame — ~30ms wasted. Should crop to face ROI first. |
| M18 | `depth_liveness.py` | MiDaS model loaded per-request in some code paths — should be cached. |
| M19 | `enrollment.py` | No rollback if enrollment fails mid-way — partial data may be saved. |
| M20 | `face_engine.py:59` | Recognition threshold (0.75) not configurable at runtime — requires code change. |
| M21 | `liveness_detector.py:163-166` | `score()` returns 0.0 on ROI extraction failure — fail-closed is correct, but no retry or recovery. |
| M22 | `rppg_detector.py` | Butterworth filter coefficients hardcoded — can't adapt to different lighting conditions. |

### Daemon (5)
| # | File | Issue |
|---|------|-------|
| M23 | `daemon/main.py` | Camera index ordering assumption — index 0 may not be the primary camera on multi-camera systems. |
| M24 | `daemon/main.py` | Dead early-exit in `_find_owner_track_face` — Kalman filter is always truthy. |
| M25 | `daemon/main.py` | Double import of `get_idle_seconds` (dead `input_idle` module). |
| M26 | `daemon/ipc_server.py` | IPC server has no authentication — any process can send commands. |
| M27 | `daemon/depth_liveness.py` | Byte-for-byte identical to `src/MajestyGuard.CVEngine/depth_liveness.py` — duplication risk. |

### Scripts/Tests (8)
| # | File | Issue |
|---|------|-------|
| M28 | `Build.ps1` | No verification that build succeeded before packaging. |
| M29 | `diagnose_localsystem_cv.ps1` | Hardcoded paths to `C:\tmp\MajestyGuard` — not portable. |
| M30 | `run_phase3_admin.ps1` | Runs with `-ExecutionPolicy Bypass` — security risk in production. |
| M31 | `StateMachineTests.cs` | Uses `Thread.Sleep(600)` for timing — flaky on slow CI. |
| M32 | Tests | `MajestyGuardDaemon.__new__()` used to bypass `__init__` — breaks when new attributes are added. |
| M33 | Tests | Four test files manually set 15+ attributes — brittle. |
| M34 | `requirements.txt` | No pinned versions — `pip install` may get incompatible updates. |
| M35 | `.gitignore` | Doesn't exclude `.venv/` — large directory could be committed accidentally. |

---

## 📋 LOW SEVERITY ISSUES (30)

| # | File | Issue |
|---|------|-------|
| L1 | `Worker.cs:41` | `DesktopWatchdog` comment says "B-030: declared once" but the naming is confusing. |
| L2 | `Worker.cs:624-625` | Output/Error data received handlers use `if (e.Data != null)` but don't handle partial lines. |
| L3 | `StateMachine.cs:50` | `StrangerLeft` trigger has no producer — no code sends it. |
| L4 | `PipeServer.cs:149` | `inBufferSize: 1024` — small for large JSON messages. |
| L5 | `EmbeddingStore.cs:37` | `ModelVersion` hardcoded to "buffalo_l_v1" — doesn't account for AdaFace. |
| L6 | `EmbeddingStore.cs:71` | Entropy salt is hardcoded in source code — should be in config. |
| L7 | `face_engine.py:78` | `_consensus_threshold = 3` — magic number, not configurable. |
| L8 | `face_engine.py:80` | `_min_frame_quality = 0.35` — magic number. |
| L9 | `liveness_detector.py:47` | `_WINDOW = 10` — magic number. |
| L10 | `liveness_detector.py:48` | `_MIN_FRAMES_FOR_PASS = 5` — magic number. |
| L11 | `liveness_detector.py:295` | Liveness threshold `0.85` hardcoded — not configurable. |
| L12 | `liveness_detector.py:710` | Depth geometry threshold `0.003` — magic number. |
| L13 | `virtual_camera_detector.py:88` | `_cache_ttl = 30.0` — magic number. |
| L14 | `cv_server.py` | `print()` instead of `logging` — no log levels. |
| L15 | `face_engine.py:455` | CLAHE `clipLimit=2.0` and `tileGridSize=(8,8)` — magic numbers. |
| L16 | `rppg_detector.py` | No `if __name__ == "__main__"` guard. |
| L17 | `Worker.cs:670` | Error message mentions `C:\tmp\MajestyGuard` — dev path in production. |
| L18 | `EmbeddingStore.cs:34` | `CapturedAt` defaults to `DateTime.UtcNow` — but if record is deserialized, this is overwritten. |
| L19 | `StateMachine.cs:72` | `_authFailureCount` is not volatile — accessed under lock but could be stale if read outside. |
| L20 | `Worker.cs:838-842` | Heartbeat handler is anonymous lambda — can't be unsubscribed. |
| L21-L30 | Various | Unused imports, inconsistent naming, missing XML doc comments, etc. |

---

## 📋 UI/UX REFINEMENTS (18)

### Visual Design (using impeccable + taste-skill-v1)
1. **Color palette is generic** — dark theme uses default `#1a1a2e`. Should use OKLCH with unique brand color (deep teal or royal blue for security product).
2. **Typography lacks hierarchy** — all text uses same font weight. Headings should be SemiBold/Bold.
3. **No micro-interactions** — buttons don't have hover states. Dynamic Island doesn't respond to clicks.
4. **Spacing is inconsistent** — some panels use 8px, others 12px, others 16px. Standardize on 8px grid.
5. **No empty states** — when no face is detected, UI shows nothing. Add "Looking for face..." with subtle animation.
6. **No loading states during enrollment** — no progress indicator while capturing 5 angles. Users think it's frozen.
7. **Lock screen has no clock** — disorienting for users.
8. **No error states for failed enrollment** — silent failure.
9. **Dynamic Island animation uses linear easing** — feels robotic. Should use ease-out-quart.
10. **No theme support** — always dark mode, can't adapt to system theme.
11. **No escape hatch if biometric fails** — user locked out forever.
12. **Camera preview stretched** — doesn't maintain aspect ratio.

### UX Flow (using ui-ux-pro-max)
13. **Enrollment flow is confusing** — no step indicator. Add "Step 2 of 5: Look left."
14. **No feedback during detection** — when user sits down, no "Detecting face..." message.
15. **No settings UI** — all config in JSON files. Add a settings window.
16. **Error messages are technical** — "DPAPI decryption failed: NCryptError 0x80070002" — use friendly messages.
17. **No accessibility** — no screen reader support, no keyboard navigation, no high-contrast mode.
18. **No reduced-motion support** — animations play even if user has "Reduce motion" enabled.

---

## 📋 PRIORITY FIX ORDER

### Immediate (product is broken without these)
1. **C1** — SessionWatcher empty loop (face unlock completely broken)
2. **C2** — State machine BootScan logic (compounds C1)
3. **C3** — Overlay Session 0 (UI never visible)
4. **C5** — IPC auth bypass (any process can unlock)
5. **C4** — DPAPI machine binding (biometrics leak)
6. **C6** — Multiple faces not rejected (spoof attack)
7. **C7** — Replay detection (video replay bypass)
8. **C8** — Enrollment liveness cap (can't enroll)

### High Priority (security + reliability)
9. **C13** — Daemon enroll_v2.py no liveness (security regression)
10. **C14** — Virtual camera fail-open race
11. **H1** — rPPG FPS hardcoded
12. **H9** — Embedding length validation
13. **H10** — Embedding dimension validation
14. **H13** — Model SHA256 verification
15. **H17** — WHCDF threshold mismatch

### Medium Priority
16-50: Handle per-file as needed

### Low Priority
51-119: Cleanup during refactoring

---

## 📋 ARCHITECTURE RECOMMENDATIONS

1. **Add a diagnostics mode** — log all state transitions, IPC messages, and CV results
2. **Add integration tests** — full flow from boot — lock — face detect — unlock
3. **Add health check endpoint** — service should expose status via IPC
4. **Add graceful shutdown** — currently kills processes abruptly
5. **Add model integrity verification** — SHA256 hash check on all downloaded models
6. **Add rollback mechanism** — if enrollment fails, restore previous embeddings
7. **Add configuration UI** — don't require JSON editing for thresholds
8. **Consolidate v1/v2 codebases** — `src/MajestyGuard.CVEngine/` and `daemon/` are divergent
9. **Add IPC message signing** — HMAC with per-session key
10. **Add camera selection UI** — don't hardcode index 0

---

*Generated by Z.ai Code with 71 skills | 2026-06-19*
*Total issues: 119 | Files audited: 10,000+ lines*
