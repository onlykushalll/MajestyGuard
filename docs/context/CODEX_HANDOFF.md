# MAJESTY GUARD — CODEX HANDOFF DOCUMENT
## READ THIS ENTIRE FILE BEFORE TOUCHING ANY CODE

---

## WHAT THIS PROJECT IS

"Majesty Guard" is a Windows 11 profile-specific biometric security layer.
It intercepts the login screen, does face recognition, monitors presence
continuously, and locks/restricts the system based on who is in front of
the camera. Think Apple FaceID + Dynamic Island, but on Windows desktop.

**This is NOT a toy project. Every architectural decision in these files
was made deliberately. Do not refactor the structure unless asked.**

---

## PROJECT STRUCTURE

```
MajestyGuard/
├── CODEX_HANDOFF.md              ← YOU ARE HERE
├── MajestyGuard.sln
└── src/
    ├── MajestyGuard.Core/        ← Shared models, state machine, IPC contracts
    │   ├── StateMachine.cs       ← THE HEART. All states live here.
    │   ├── Models/
    │   │   ├── FaceEmbedding.cs  ← Biometric data model
    │   │   └── AppConfig.cs      ← All configurable values
    │   ├── IPC/
    │   │   ├── IpcMessage.cs     ← Message types between all processes
    │   │   └── PipeServer.cs     ← Named pipe IPC backbone
    │   └── Security/
    │       └── EmbeddingStore.cs ← DPAPI-encrypted storage for face data
    │
    ├── MajestyGuard.Service/     ← Windows Service (runs as SYSTEM)
    │   ├── Worker.cs             ← Entry point, orchestrates everything
    │   ├── PresenceMonitor.cs    ← Continuous face detection loop
    │   ├── InactivityWatcher.cs  ← GetLastInputInfo polling
    │   ├── SocialLockEngine.cs   ← Multi-face detection + Safe Mode trigger
    │   └── ProcessRestrictor.cs  ← Suspend/resume target processes
    │
    ├── MajestyGuard.Overlay/     ← WinUI 3 app (topmost overlay window)
    │   ├── App.xaml.cs
    │   ├── DynamicIslandWindow.xaml      ← The pill UI
    │   └── DynamicIslandWindow.xaml.cs
    │
    ├── MajestyGuard.CVEngine/    ← Python face recognition (ONNX/InsightFace)
    │   ├── face_engine.py        ← Detection + recognition + embedding
    │   ├── liveness_detector.py  ← Anti-spoofing (photo attack prevention)
    │   ├── enrollment.py         ← One-time face enrollment flow
    │   └── cv_server.py          ← Named pipe server exposing CV to C#
    │
    └── MajestyGuard.CredentialProvider/  ← C++ COM DLL (login intercept)
        ├── MajestyCredentialProvider.h
        └── MajestyCredentialProvider.cpp
```

---

## THE STATE MACHINE (CRITICAL — READ THIS)

All application behavior is driven by a single state machine defined in
`Core/StateMachine.cs`. States are:

```
DORMANT          → App not running (wrong user profile)
BOOT_SCAN        → Login screen active, camera scanning
VERIFYING        → Face detected, running recognition
UNLOCKED         → Primary user verified, normal desktop
INACTIVITY_LOCK  → No input for N seconds → screen lock overlay
SOCIAL_LOCK      → User + stranger detected → Safe Mode
HOSTILE_LOCK     → No face / camera obscured → full opaque lock
```

**RULE: Nothing transitions state directly. All transitions go through
`StateMachine.RequestTransition()`. If you add a feature that changes
state, use the state machine. No exceptions.**

---

## IPC ARCHITECTURE

Four processes communicate via Named Pipes:

```
[CV Engine (Python)]  ←→  [Windows Service (C#)]  ←→  [Overlay (WinUI 3)]
                                    ↕
                      [Credential Provider (C++)]
```

The Windows Service is the MASTER. It:
- Receives detection results from CV Engine
- Sends state changes to the Overlay
- Commands process restriction directly
- Sends auth results to the Credential Provider

Named pipe names are defined in `Core/IPC/IpcMessage.cs`:
- `\\.\pipe\MajestyGuard_CV`       (Service ↔ CVEngine)
- `\\.\pipe\MajestyGuard_Overlay`  (Service → Overlay)
- `\\.\pipe\MajestyGuard_CredProv` (Service ↔ CredentialProvider)

---

## LANGUAGE STACK (DO NOT CHANGE)

| Component            | Language      | Framework         | Why |
|----------------------|---------------|-------------------|-----|
| CredentialProvider   | C++17         | ATL/COM           | Only option for Winlogon |
| Windows Service      | C# .NET 8     | Worker Service    | System hooks, process mgmt |
| Overlay UI           | C# .NET 8     | WinUI 3           | GPU-composited transparency |
| CV Engine            | Python 3.11   | InsightFace/ONNX  | Best face recognition OSS |
| IPC                  | Named Pipes   | —                 | Works across sessions |

---

## SECURITY RULES (NON-NEGOTIABLE)

1. Face embeddings are stored encrypted via Windows DPAPI, keyed to user SID
2. Camera frames are NEVER written to disk — zero after inference
3. All CV inference is 100% local — no network calls from CVEngine
4. The Overlay process runs with HWND_TOPMOST + cannot be closed by user
5. Liveness detection MUST run before any recognition attempt
6. Profile isolation: Service checks current SID on startup; exits if not enrolled

---

## PERFORMANCE BUDGET

- Idle CPU (monitoring loop at 1 FPS): ≤ 3%
- Active verification (10 FPS): ≤ 15% CPU
- RAM total (all processes): ≤ 180MB
- Face recognition latency: ≤ 200ms per frame
- Camera init at login: ≤ 1.5 seconds

---

## WHAT TO BUILD NEXT (ORDERED)

The scaffold files give you the skeleton. Codex should implement in this order:

1. **Core/StateMachine.cs** — Complete the transition logic (guards are stubbed)
2. **CVEngine/cv_server.py** — Make the pipe server actually receive frames
3. **CVEngine/face_engine.py** — Implement InsightFace detection + embedding
4. **CVEngine/liveness_detector.py** — Implement LBP texture anti-spoofing
5. **Service/PresenceMonitor.cs** — Connect to CV pipe, feed detection results to state machine
6. **Service/InactivityWatcher.cs** — Implement GetLastInputInfo polling loop
7. **Overlay/DynamicIslandWindow** — Implement blur + pill animations per state
8. **Service/SocialLockEngine.cs** — Implement multi-face count + hysteresis
9. **Service/ProcessRestrictor.cs** — Implement NtSuspendProcess targeting
10. **CredentialProvider/** — Implement ICredentialProvider COM interfaces
11. **Core/Security/EmbeddingStore.cs** — Implement DPAPI encrypt/decrypt
12. **CVEngine/enrollment.py** — Multi-angle capture + embedding generation

---

## GOTCHAS CODEX MUST KNOW

- WinUI 3 overlays cannot render on the Secure Desktop (Session 0).
  The Credential Provider hosts its own minimal Win32 UI for login.
  The WinUI overlay is only for the POST-login states.

- UWP process suspension (WhatsApp etc.) requires different APIs than
  Win32. Use `PackageDebugSettings::SuspendApplication()` for UWP.

- GetLastInputInfo returns SYSTEM ticks, not wall clock. Use
  `GetTickCount64()` for the delta calculation, not DateTime.Now.

- InsightFace buffalo_l model requires ~300MB on first download.
  Add a setup script that pre-downloads it. Do NOT download at runtime.

- The Named Pipe for CV must use `PipeOptions.Asynchronous` on both ends
  or you will block the monitoring loop.

- DPAPI ProtectedData.Protect() uses the current user's credentials.
  Call it from a process running AS that user, not SYSTEM.
  The enrollment process must run in user context, not service context.
