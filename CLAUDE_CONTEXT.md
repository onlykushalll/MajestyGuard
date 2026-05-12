# MAJESTY GUARD — CLAUDE CONTEXT FILE
# Paste this at the start of any new Claude session for instant project context.
# Last updated: after full security hardening pass (Kimi K2.6 + Gemini reports)

## WHAT THIS IS
Windows 11 face-recognition lock system. Profile-specific. Dynamic Island UI.
Repo: https://github.com/onlykushalll/MajestyGuard  (not really needed to be used)

## ARCHITECTURE
| Component | Language | Role |
|---|---|---|
| MajestyGuard.Core | C# .NET 8 | StateMachine, IPC contracts, EmbeddingStore |
| MajestyGuard.Service | C# .NET 8 Worker | Orchestration, runs as SYSTEM |
| MajestyGuard.Overlay | C# WinUI 3 | Dynamic Island UI, user session |
| MajestyGuard.CVEngine | Python 3.11 | InsightFace + 9-layer liveness |
| MajestyGuard.CredentialProvider | C++ COM DLL | Login screen intercept |
| MajestyGuard.DpapiHelper | C# .NET 8 | DPAPI bridge (user context) |

## STATE MACHINE (7 states)
Dormant → BootScan → Verifying → Unlocked → InactivityLock / HostileLock / SocialLock
- SocialLock exits to BootScan (not Unlocked) [B-010 fixed]
- HostileLock has 30s ManualFallback cooldown [B-009 fixed]
- Verifying times out at 5s → HostileLock [FIX-007 done]
- All transitions go through StateMachine.RequestTransition() — never direct

## IPC (Named Pipes, newline-delimited JSON)
MajestyGuard_CV       : CVEngine → Service (DetectionResultMsg)
MajestyGuard_Overlay  : Service → Overlay (OverlayCommandMsg)
MajestyGuard_CredProv : Service ↔ CredentialProvider (AuthDecisionMsg)

## KEY FILES & ONE-LINE DESCRIPTION
- Core/StateMachine.cs         — 7 states, transition table, guard conditions
- Core/IPC/IpcMessage.cs       — all pipe message types
- Core/Security/EmbeddingStore.cs — DPAPI-NG NCryptProtectSecret storage
- Service/Worker.cs            — orchestrates all components, OnStateChanged
- Service/DesktopWatchdog.cs   — EnumDesktops + WinEventHook vs CreateDesktop attack
- Service/InactivityWatcher.cs — GetLastInputInfo (NOTE: Session 0 blind, see debts)
- Service/SocialLockEngine.cs  — NtSuspendProcess + DACL restriction
- Overlay/DynamicIslandWindow  — HWND_TOPMOST pill UI, all state animations
- Overlay/LockScreenGuard.cs   — BlockInput (16ms), accessibility suppression
- Overlay/EnrollmentWindow     — 6-step enrollment wizard
- CVEngine/face_engine.py      — InsightFace buffalo_l + CLAHE + virtual cam
- CVEngine/liveness_detector.py— 9 liveness layers, min() rolling window
- CVEngine/virtual_camera_detector.py — CLSID blocklist + MF hardware source
- CVEngine/enrollment.py       — multi-angle capture + quality-weighted fusion
- CredentialProvider/*.cpp/.h  — COM DLL, login intercept, gatekeeper only

## BUG FIX STATUS
| ID | Description | Status |
|---|---|---|
| B-003 | Stranger tracking accumulation attack | FIXED |
| B-004 | BlockInput 250ms gap | FIXED → 16ms |
| B-005/6 | Verifying state permanent hang | FIXED → 5s timeout |
| B-007 | BlockInput OOM resilience | FIXED → Service also blocks |
| B-008 | Enrollment silent lockout on DPAPI fail | FIXED → verify before save |
| B-009 | HostileLock no cooldown | FIXED → 30s |
| B-010 | SocialLock → Unlocked (wrong) | FIXED → BootScan |
| B-015 | CP JSON injection | IMPROVED → safer field matching |
| B-016 | Session 0 inactivity blindness | DOCUMENTED + UserIdleMsg/UserActivityMsg added |
| B-019/20 | Virtual camera CLSID (4 entries) | FIXED → 8 entries + MF source check |
| B-021 | Liveness mean() spoofable | FIXED → min() |
| B-024 | Password stored in Credential Manager | FIXED → CP is gatekeeper only |
| B-029 | ONNX session nulled on error | FIXED → retry counter, session preserved |
| B-030 | DesktopWatchdog started twice | FIXED |
| B-031 | async void OnStateChanged swallows exceptions | FIXED → try/catch |
| B-035 | GetUserSid returns SYSTEM SID | FIXED → reads HKLM registry |

## CV ENGINE
- Model: InsightFace buffalo_l (ArcFace R100), threshold 0.78 (FAR ~1:10M)
- Liveness: 9 layers using min() rolling window (not mean)
- CLAHE: applied before detection in low-light (<100 mean luminance)
- Anti-spoof Layer 7: MiniFASNetV2 ONNX (session preserved on errors)
- AdaFace: optional (adaface_r100.onnx if present in models/)
- Enrollment: quality-weighted fusion (detection score + sharpness + illumination)

## ARCHITECTURAL DEBTS (v2 scope)
1. InactivityWatcher in Session 0 is blind — Overlay sends UserIdleMsg instead (workaround)
2. CP is gatekeeper only — shows password hint after face auth (v1). V2: Virtual Smart Card
3. DPAPI-NG LOCAL=machine (no TPM PCR binding yet)
4. Session 0 → Session 1 full inactivity migration pending
5. AppContainer for CVEngine (pending)
6. PPL protection needs Microsoft certificate

## HOW TO BRIEF CLAUDE IN NEW SESSION
Paste this file. Then say which file to fetch:
  https://raw.githubusercontent.com/onlykushalll/MajestyGuard/main/[filepath]
Example: "Fetch src/MajestyGuard.Service/Worker.cs and fix X"
