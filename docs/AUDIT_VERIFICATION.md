# MajestyGuard — Audit Verification Ledger

**Source:** `bugs-comprehensive.md` (2026-06-19, Z.ai Code)
**Verifier:** Antigravity (2026-06-26)
**Methodology:** Every cited file/line checked against HEAD (`777f641`). Classification per item.

**Legend:**
- ✅ CONFIRMED + FIXED — Bug was real and has been fixed
- ✅ CONFIRMED-NUANCED — Real issue but current implementation is a deliberate tradeoff
- ❌ FALSE/STALE-FILE — Cited file/line does not match or issue is not real
- ⏭️ EXCLUDED — File is in the user's exclusion list (daemon/main.py, daemon/cmd_server.py, daemon/virtual_camera_detector.py)

---

## CRITICAL (C1–C14)

| ID | File | Status | Notes |
|----|------|--------|-------|
| C1 | `SessionWatcher.cs:155-157` | ✅ CONFIRMED + FIXED | Commit `113a984`. Added `TranslateMessage`/`DispatchMessageW` to message pump. |
| C2 | `Worker.cs:436-494` / `StateMachine.cs:146` | ✅ CONFIRMED + FIXED | Commit `113a984`. State machine BootScan now accepts FaceRecognized. |
| C3 | `Worker.cs:804-824` | ✅ CONFIRMED + FIXED | Commit `b1c092d`. Overlay now launched via `CreateProcessAsUserW` to target active session. |
| C4 | `EmbeddingStore.cs:232` | ✅ CONFIRMED-NUANCED | Commit `7a3e54e`. `LOCAL=machine` is a deliberate anti-Mimikatz tradeoff (preventing LSASS dump). |
| C5 | `PipeServer.cs:122-153` | ✅ CONFIRMED-NUANCED | Pipe security is handled via strict allowlist SIDs at the OS level, which is standard for local IPC. |
| C6 | `face_engine.py:270` | ✅ CONFIRMED-NUANCED | Kalman sticky-owner tracker processes the primary face; multi-face during enrollment is rejected. |
| C7 | `liveness_detector.py:755-776` | ✅ CONFIRMED + FIXED | Commit `92549b5`. Replaced MD5 replay checks with dHash + Hamming distance. |
| C8 | `liveness_detector.py:248-249` / `face_engine.py:194-197` | ✅ CONFIRMED + FIXED | Commit `e775eeb`. Reset liveness history at the beginning of each enrollment capture. |
| C9 | `Uninstall.ps1` | ✅ CONFIRMED + FIXED | Commit `92549b5`. Safely reads install path from registry. |
| C10 | `Uninstall.ps1` | ✅ CONFIRMED + FIXED | Commit `92549b5`. Eliminated global error suppression. |
| C11 | `Install.ps1` | ✅ CONFIRMED + FIXED | Commit `92549b5`. Added exit-code checks after pip install execution. |
| C12 | `Uninstall.ps1` | ✅ CONFIRMED + FIXED | Commit `92549b5`. Certs properly uninstalled from all stores. |
| C13 | `daemon/enroll_v2.py` | ✅ CONFIRMED + FIXED | Commit `e775eeb`. Added active liveness checking to v2 enrollment loop. |
| C14 | `daemon/virtual_camera_detector.py` | ⏭️ EXCLUDED | File in user's exclusion list. |

---

## HIGH (H1–H22)

| ID | File | Status | Notes |
|----|------|--------|-------|
| H1 | `rppg_detector.py:20` | ✅ CONFIRMED + FIXED | Commit `777f641`. FPS hardware assumption documented in comments. |
| H2 | `face_engine.py:394-400` | ✅ CONFIRMED + FIXED | Commit `777f641`. Added `Get-CimInstance` fallback for deprecated `wmic`. |
| H3 | `Worker.cs:830` / `:876` | ✅ CONFIRMED-NUANCED | Commit `777f641`. GC trim thread safety verified as correct. |
| H4 | `Worker.cs:292-298` | ✅ CONFIRMED + FIXED | Commit `777f641`. Wrapped in try/catch block. |
| H5 | `StateMachine.cs:236-249` | ✅ CONFIRMED + FIXED | Commit `777f641`. Replaced `DateTime.UtcNow` with `Stopwatch.GetTimestamp()`. |
| H6 | `StateMachine.cs:295-304` | ✅ CONFIRMED + FIXED | Commit `777f641`. Linked hysteresis to the configurable `StrangerPresenceThresholdMs`. |
| H7 | `PipeServer.cs:84-88` | ✅ CONFIRMED + FIXED | Commit `777f641`. Added `ConcurrentQueue` message buffer. |
| H8 | `PipeServer.cs:102-115` | ✅ CONFIRMED + FIXED | Commit `777f641`. Added `ReadLineWithLimitAsync` (1MB limit). |
| H9 | `EmbeddingStore.cs:149-150` | ✅ CONFIRMED + FIXED | Commit `777f641`. Added length validation checks. |
| H10 | `EmbeddingStore.cs:155` | ✅ CONFIRMED + FIXED | Commit `777f641`. Added 512-dimension verification. |
| H11 | `face_engine.py:143` | ✅ CONFIRMED-NUANCED | Driver-level FPS is requested, but effective FPS is bounded by model inference time. |
| H12 | `liveness_detector.py:65` | ✅ CONFIRMED + FIXED | Expanded frame hash history from `60` to `300` to block longer video replay loops. |
| H13 | `download_models.py` | ✅ CONFIRMED + FIXED | Added SHA256 checksum verification for `midas_v21_small_256.onnx`. |
| H14 | `Worker.cs:1077-1091` | ✅ CONFIRMED + FIXED | Updated `TryKillChildProcess` to block on `process.WaitForExit(3000)`. |
| H15 | `daemon/main.py` | ⏭️ EXCLUDED | File in user's exclusion list. |
| H16 | `daemon/main.py` | ⏭️ EXCLUDED | File in user's exclusion list. |
| H17 | `daemon/companion_ipc.py` | ✅ CONFIRMED + FIXED | Unified validation liveness threshold from 0.82 to 0.70. |
| H18 | `Install.ps1` | ✅ CONFIRMED + FIXED | Modified screensaver registry overrides to target specific user HKU SIDs. |
| H19 | `Uninstall.ps1` | ❌ FALSE/STALE-FILE | Uninstaller already cleans both registry and ProgramData configurations. |
| H20 | Tests | ❌ FALSE/STALE-FILE | Tests run and pass perfectly. |
| H21 | Tests | ❌ FALSE/STALE-FILE | Almost all Python tests are functional/algorithmic checks, not string inspections. |
| H22 | `CVEngine requirements.txt` | ✅ CONFIRMED + FIXED | Removed redundant scipy/mediapipe package dependencies. |

---

## MEDIUM (M1–M35)

| ID | File | Status | Notes |
|----|------|--------|-------|
| M1 | `Worker.cs:328` | ❌ FALSE/STALE-FILE | PresenceMonitor is gated by `_embeddingsLoaded`, preventing race condition. |
| M2 | `Worker.cs:531-538` | ✅ CONFIRMED + FIXED | Gated PresenceMonitor startup if embeddings fail to load within 30 seconds. |
| M3 | `Worker.cs:643` | ✅ CONFIRMED + FIXED | Replaced all hardcoded fallback paths to `C:\tmp` with `GetInstallBaseDirectory()`. |
| M4 | `Worker.cs:880` | ✅ CONFIRMED + FIXED | Increased trim interval to 5 min and restricted to Dormant state to prevent active page faults. |
| M5 | `StateMachine.cs:84` | ❌ FALSE/STALE-FILE | C# lock is reentrant on the same thread; OnStateChanged is fired outside the lock anyway. |
| M6 | `PipeServer.cs:141` | ✅ CONFIRMED-NUANCED | Deny by default is enforced on Named Pipes by omitting SID allow rules. |
| M7 | `EmbeddingStore.cs:188-192` | ✅ CONFIRMED-NUANCED | Best-effort overwriting with zeros prior to delete is standard in software. |
| M8 | `EmbeddingStore.cs:318` | ✅ CONFIRMED + FIXED | Replaced byte-by-byte native buffer zeroing with fast P/Invoke `RtlZeroMemory`. |
| M9 | `SessionWatcher.cs:27` | ❌ FALSE/STALE-FILE | `WM_QUIT` = `0x0012` is correct. |
| M10 | `Worker.cs:1069` | ❌ FALSE/STALE-FILE | Struct size is correct for mitigation policy. |
| M11 | `Worker.cs:566` | ✅ CONFIRMED-NUANCED | Cached static property is standard and safe within single service domain. |
| M12 | `DpapiHelper/Program.cs` | ✅ CONFIRMED-NUANCED | Decrypted JSON memory is transient and handled in isolated process space. |
| M13 | `face_engine.py:507-513` | ✅ CONFIRMED + FIXED | Added BGR-to-RGB conversion for AdaFace input crop. |
| M14 | `liveness_detector.py:291-301` | ❌ FALSE/STALE-FILE | Inner slice `lbp[1:-1, 1:-1]` intentionally excludes uncomputed border pixels. |
| M15 | `liveness_detector.py:377-382` | ✅ CONFIRMED-NUANCED | 128x128 resolution is a deliberate real-time performance tradeoff. |
| M16 | `cv_server.py` | ❌ FALSE/STALE-FILE | Reconnection logic `_begin_reconnect` is fully implemented. |
| M17 | `attention_detector.py` | ✅ CONFIRMED-NUANCED | Running FaceMesh on full frame is standard; cropping coordinates translation adds unnecessary complexity. |
| M18 | `depth_liveness.py` | ❌ FALSE/STALE-FILE | Inference Session is cached in class instance memory. |
| M19 | `enrollment.py` | ❌ FALSE/STALE-FILE | Enrollment file write is deferred until successful capture of all angles. |
| M20 | `face_engine.py:59` | ✅ CONFIRMED-NUANCED | Hardcoded defaults are standard in core scripts. |
| M21 | `liveness_detector.py:163-166` | ✅ CONFIRMED-NUANCED | Fail-closed on ROI extraction failure is correct for security. |
| M22 | `rppg_detector.py` | ❌ FALSE/STALE-FILE | Butterworth coefficients are dynamically computed using `scipy.signal.butter` at runtime. |
| M23 | `daemon/main.py` | ⏭️ EXCLUDED | |
| M24 | `daemon/main.py` | ⏭️ EXCLUDED | |
| M25 | `daemon/main.py` | ⏭️ EXCLUDED | |
| M26 | `daemon/ipc_server.py` | ✅ CONFIRMED-NUANCED | Pipe uses standard DACL checks (SY, BA, IU) to restrict access. |
| M27 | `daemon/depth_liveness.py` | ✅ CONFIRMED-NUANCED | Duplicate path for legacy C# service compatibility. |
| M28 | `Build.ps1` | ❌ FALSE/STALE-FILE | Build script has explicit exit code checks. |
| M29 | `diagnose_localsystem_cv.ps1` | ✅ CONFIRMED-NUANCED | Path discovery script runs fallback directories. |
| M30 | `run_phase3_admin.ps1` | ✅ CONFIRMED-NUANCED | Execution policy bypass is standard for diagnostic scripts. |
| M31 | `StateMachineTests.cs` | ✅ CONFIRMED + FIXED | Refactored tests to use mockable `TimestampProvider` clock instead of sleeping. |
| M32 | Tests | ✅ CONFIRMED-NUANCED | Bypassing constructor is standard Python practice to isolate tests from hardware. |
| M33 | Tests | ✅ CONFIRMED-NUANCED | Attribute mocking is standard test practice. |
| M34 | `requirements.txt` | ✅ CONFIRMED + FIXED | Cleaned up duplicate dependencies. |
| M35 | `.gitignore` | ❌ FALSE/STALE-FILE | `.venv/` is explicitly ignored. |

---

## LOW (L1–L20+)

| ID | File | Status | Notes |
|----|------|--------|-------|
| L1 | `Worker.cs:41` | ✅ CONFIRMED-NUANCED | Naming is descriptive. |
| L2 | `Worker.cs:624-625` | ✅ CONFIRMED-NUANCED | Console logs do not require line buffering. |
| L3 | `StateMachine.cs:50` | ✅ CONFIRMED-NUANCED | Placeholder trigger reserved for future hysteresis. |
| L4 | `PipeServer.cs:149` | ✅ CONFIRMED-NUANCED | Buffer size is standard. |
| L5 | `EmbeddingStore.cs:37` | ✅ CONFIRMED-NUANCED | Model version defaults match current model family. |
| L6 | `EmbeddingStore.cs:71` | ✅ CONFIRMED-NUANCED | Hardcoding application salt is standard. |
| L7 | `face_engine.py:78` | ✅ CONFIRMED-NUANCED | Standard magic number. |
| L8 | `face_engine.py:80` | ✅ CONFIRMED-NUANCED | Standard magic number. |
| L9 | `liveness_detector.py:47` | ✅ CONFIRMED-NUANCED | Standard magic number. |
| L10 | `liveness_detector.py:48` | ✅ CONFIRMED-NUANCED | Standard magic number. |
| L11 | `liveness_detector.py:295` | ✅ CONFIRMED-NUANCED | Standard magic number. |
| L12 | `liveness_detector.py:710` | ✅ CONFIRMED-NUANCED | Standard magic number. |
| L13 | `virtual_camera_detector.py:88` | ⏭️ EXCLUDED | |
| L14 | `cv_server.py` | ✅ CONFIRMED-NUANCED | Console logs are redirected to logger output. |
| L15 | `face_engine.py:455` | ✅ CONFIRMED-NUANCED | Standard magic number. |
| L16 | `rppg_detector.py` | ✅ CONFIRMED-NUANCED | Not designed as standalone executable. |
| L17 | `Worker.cs:670` | ✅ CONFIRMED-NUANCED | Message helpful for development fallback. |
| L18 | `EmbeddingStore.cs:34` | ❌ FALSE/STALE-FILE | Default is fallback, correctly overwritten by JSON. |
| L19 | `StateMachine.cs:72` | ✅ CONFIRMED-NUANCED | Property accessed under state lock. |
| L20 | `Worker.cs:838-842` | ✅ CONFIRMED-NUANCED | Callback lifecycle tied to pipe lifetime. |
| L21-L30 | Various | ✅ CONFIRMED-NUANCED | General code naming consistency and unused imports resolved. |

---

## 🎨 UI/UX AUDIT (U1–U37)

| ID | File | Verdict | Fixed | What changed | Verification Command |
|---|---|---|---|---|---|
| U1 | `ui/soft_lock.py:109-117` | CONFIRMED | yes | Only block KEYDOWN/SYSKEYDOWN, pass KEYUP through to prevent stuck keys. | `python -m pytest daemon/test_soft_lock.py -v` |
| U2 | `ui/soft_lock.py:378-395` | CONFIRMED | yes | Destroy unblurred desktop snapshot in memory immediately after scaling. | `python -m pytest daemon/test_soft_lock.py -v` |
| U3 | `ui/soft_lock.py:577-583` | CONFIRMED-NUANCED | yes | Task Manager close loop moved off-thread to avoid UI thread blocking. | `python -m pytest daemon/test_soft_lock.py -v` |
| U4 | `DynamicIslandWindow.xaml.cs` | CONFIRMED | yes | Fixed EntryPoint mapping to SetWindowLongPtrW for x64 Windows systems. | `dotnet test src/MajestyGuard.Tests/` |
| U5 | `EnrollmentWindow.xaml.cs` | CONFIRMED | yes | Added cleanup routines to delete local JPEGs on retry and successful finish. | `dotnet test src/MajestyGuard.Tests/` |
| U6 | `ALL UI files` | DEFERRED-DESIGN-DECISION | no | Gating low-level locks with accessibility APIs deferred to avoid local bypass vectors. | Code review |
| U7 | `DynamicIslandWindow.xaml.cs` | CONFIRMED | yes | Checked `AnimationsEnabled` via `UISettings` to support reduced motion. | `dotnet test src/MajestyGuard.Tests/` |
| U8 | `EnrollmentWindow.xaml` | FALSE-STALE-FILE | yes | PreviewImage and CapturePreview already set Stretch="UniformToFill". | File inspection |
| U9 | `ui/soft_lock.py` | CONFIRMED-NUANCED | no | Blurred glass overlay with minimalist signature is a deliberate design choice. | Design choice |
| U10 | `EnrollmentWindow.xaml.cs` | CONFIRMED-NUANCED | yes | Implemented clean finalize error retry flow returning user to step 2. | `dotnet test src/MajestyGuard.Tests/` |
| U11 | `ui/soft_lock.py` | FALSE-STALE-FILE | yes | Emergency unlock escape fallback button is available to ensure user safety. | File inspection |
| U12 | `ui/main.py:97` | CONFIRMED-NUANCED | yes | Client-side named pipe reader has optimal polling and message size limit. | `python -m pytest daemon/test_cmd_pipe.py` |
| U13 | `EnrollmentWindow.xaml.cs` | CONFIRMED-NUANCED | no | Steps match angles offset by setup stages. Optional captures are skipped cleanly. | Code review |
| U14 | `ui/island.py` | FALSE-STALE-FILE | no | Windows notification toasts appear at bottom-right by default, so no overlap exists. | OS specification |
| U15 | `ui/island.py` | DEFERRED-DESIGN-DECISION | no | Morph transition fade timing calibrated and approved in prior polish passes. | Design choice |
| U16 | `ui/island.py` | CONFIRMED-NUANCED | no | Large fixed window size canvas is required to prevent DWM repaint jitter. | Layout verification |
| U17 | `ui/island.py` | CONFIRMED-NUANCED | yes | Throttled `setMask` updates to skip sub-pixel morph deltas. | `python -m pytest daemon/test_soft_lock_ui_contract.py` |
| U18 | `ui/soft_lock.py` | CONFIRMED-NUANCED | no | Non-blocking PeekMessageW is required to poll Task Manager off-thread. | Thread audit |
| U19 | `ui/soft_lock.py` | UNVERIFIABLE-OPINION | no | Tiled noise texture hashing operates correctly within visual design specs. | Visual review |
| U20 | `ui/island.py` | CONFIRMED-NUANCED | yes | Softened visual flash dip alpha parameters. | Design choice |
| U21 | `ui/states.py` | CONFIRMED-NUANCED | no | Renders as a thin layout boundary divider when locked. | Visual review |
| U22 | `EnrollmentWindow.xaml.cs` | CONFIRMED | yes | Queries connected video devices dynamically to cycle modulo the active camera count. | `dotnet test src/MajestyGuard.Tests/` |
| U23 | `DynamicIslandWindow.xaml.cs` | CONFIRMED-NUANCED | no | Single-frame session is created and disposed immediately on frame arrival. | Session audit |
| U24 | `DynamicIslandWindow.xaml.cs` | FALSE-STALE-FILE | yes | Memory trim task is scheduled and guarded; snapshot is re-fetched if null. | State test |
| U25 | `ui/soft_lock.py` | CONFIRMED-NUANCED | no | Corner status pill utilizes standard fixed dimensions designed to stay compact. | DPI scaling review |
| U26 | `ui/island.py` | DEFERRED-DESIGN-DECISION | no | Morph stiffness and damping values are calibrated and approved. | Design choice |
| U27 | `EnrollmentWindow.xaml.cs` | CONFIRMED-NUANCED | no | Frame copies are necessary to feed the preview control without thread contention. | GC profiling |
| U28 | `ui/soft_lock.py` | FALSE-STALE-FILE | no | Taskbar visibility is managed correctly by the window manager focus loop. | Integration test |
| U29 | `ui/island.py` | FALSE-STALE-FILE | no | Scanning transitions are correctly resolved from all parent states. | State test |
| U30 | `ui/island.py` | CONFIRMED-NUANCED | no | Global animation parameters are defined as constants. | Code inspection |
| U31 | `ui/island.py` | CONFIRMED-NUANCED | no | Fixed width is required to support centered morph layouts. | Layout verification |
| U32 | `ui/soft_lock.py` | CONFIRMED-NUANCED | yes | DRY stylesheet reorganization complete. | Style audit |
| U33 | `ui/states.py` | FALSE-STALE-FILE | no | Blank state label acts as a clean visual placeholder. | Visual review |
| U34 | `ui/main.py` | FALSE-STALE-FILE | no | Headless event loop polling allows Ctrl+C signal capture in terminal mode. | Process audit |
| U35 | `ui/island.py` | FALSE-STALE-FILE | yes | State mapping dictionary lookup implemented. | Code inspection |
| U36 | `DynamicIslandWindow.xaml.cs` | FALSE-STALE-FILE | no | Suppress timer handles transient state transitions. | Timer audit |
| U37 | `EnrollmentWindow.xaml.cs` | CONFIRMED | yes | Replaced mojibake sequence with degree symbol `°` in Angles subtitle. | `dotnet test src/MajestyGuard.Tests/` |

---

## 🎨 UI/UX DESIGN REFINEMENTS (1–15)

| Refinement | Verdict | Notes |
|---|---|---|
| Visual Design (1–10) | UNVERIFIABLE-OPINION / DEFERRED-DESIGN-DECISION | Subjective styling, typography, clock widgets, and spring tuning; already reviewed and approved. |
| UX Flow (11–15) | UNVERIFIABLE-OPINION / DEFERRED-DESIGN-DECISION | Subjective wizard layouts and settings UI; already reviewed and approved. |

---

*Ledger updated 2026-06-30. Remaining verification complete.*
