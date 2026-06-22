# PROJECT.md — MajestyGuard Codebase Architecture & Reference

MajestyGuard is a Windows-based hybrid security framework integrating low-light computer vision (CV) face recognition, monocular 3-D liveness checks, and a PyQt-based Apple Dynamic Island-styled lock screen overlay.

---

## 📂 Repository Directory Layout

```text
MajestyGuard/
├── .agents/                    # Agent-specific execution plans, heartbeats, and briefing logs (gitignored)
├── .claude/                    # Claude-specific workspace metadata and helper skills (gitignored)
├── .codex/                     # Code-generation hook specifications (gitignored)
├── .github/                    # GitHub configuration workflows and issue templates
│   ├── ISSUE_TEMPLATE/        # Issue reporting templates (bug/feature request)
│   └── workflows/              # GitHub Actions CI/CD workflow configurations
├── companion/                  # Windows Hello Companion App (packaged C# UWP app)
├── daemon/                     # Python Computer Vision daemon and system level testers
│   ├── face_engine.py          # Primary face recognition, tracking, and alignment engine
│   ├── liveness_detector.py    # 12-layer passive biometric liveness verification stack
│   ├── ipc_server.py           # Named Pipe broadcaster (broadcasts states to the UI overlay)
│   ├── cmd_server.py           # Named Pipe receiver (processes commands sent from the UI overlay)
│   └── test_*.py               # Pytest suite files validating daemon IPC, logic, and metrics
├── docs/                       # Specifications, technical plans, and audit logs
│   ├── context/                # Original request context history
│   └── audit_report.md         # Suspicion reports and security vulnerabilities (e.g. C1-C4 DPAPI/pipe issues)
├── setup/                      # Elevated PowerShell installer/uninstaller configuration scripts
├── src/                        # C#/.NET Core Source Files
│   ├── MajestyGuard.Core/      # Shared state definitions, settings, and DPAPI key management
│   ├── MajestyGuard.Service/   # Windows Service background host (coordinates daemon & UI lifecycles)
│   ├── MajestyGuard.Overlay/   # WPF/WinUI Custom Lock Screen Window
│   ├── MajestyGuard.CVEngine/  # Managed assembly wrapper for CV execution
│   ├── MajestyGuard.CredentialProvider/ # C++/COM DLL helper for Windows logon/unlock integration
│   └── MajestyGuard.Tests/     # XUnit unit tests for C# state machines and configuration
├── ui/                         # PyQt6 frontend overlays and widgets
│   ├── main.py                 # PyQt app main launcher and event pipe listener
│   ├── island.py               # Custom painted floating capsule widget (Dynamic Island)
│   ├── soft_lock.py            # Glassmorphism full-screen lock screen overlay
│   └── states.py               # Visual attributes & properties mapping dictionary for UI states
├── LICENSE                     # MIT License
└── requirements.txt           # Python package dependencies
```

---

## 🏗️ Architectural Topology

MajestyGuard operates on a decoupled multi-process architecture:

1. **Service Host (C# - `MajestyGuard.Service`)**: Runs as a background Windows service. It manages the lifecycle of the Python daemon process and the desktop UI overlay process. It listens to session changes (logon, logoff, lock, unlock) and coordinates system actions.
2. **Computer Vision Daemon (Python - `daemon/`)**: Connects to the webcam feed, processes frames locally, computes biometric embeddings using **AdaFace R100**, runs them through a **12-layer liveness stack** (textures, specular reflection, FFT frequencies, temporal blink, Monocular MiDaS depth, rPPG blood flow), and performs template validation.
3. **Pill & Lock Overlay (PyQt6 - `ui/`)**: Renders a floating near-black pill at the top center of the primary screen. When active, it displays real-time biometric tracking cues. If locked, it displays a full-screen frosted glass overlay capturing the desktop and preventing keyboard/mouse input passthrough.
4. **IPC Bridge (Windows Named Pipes)**:
   - `\\.\pipe\MajestyGuard_UI` (Daemon-to-UI): Broadcasts JSON state updates containing current status, confidence, liveness, and quality metrics.
   - `\\.\pipe\MajestyGuard_CMD` (UI-to-Daemon): Transmits trigger actions like `verify_requested` or `emergency_lock`.

---

## 🔄 IPC Interface Contract Specs

### 1. State Update Packet (Broadcasted to UI)
JSON formatted object sent via `\\.\pipe\MajestyGuard_UI`:
```json
{
  "state": "verifying_lock",
  "confidence": 0.88,
  "liveness": 0.94,
  "progress": 0.0,
  "quality": 0.72,
  "face_position": 0.5,
  "detail": "idle_timeout"
}
```

#### Valid States:
- `idle`: System is dormant.
- `scanning`: Active biometric exploration.
- `active`: Verified state (unlock allowed).
- `welcome`: Welcoming the verified user.
- `stranger`: Unknown face detected.
- `locked`: Hard Windows lock handoff.
- `soft_locked`: majestyguard soft desktop lock overlay.
- `locked_passive`: Ambient lock state awaiting interactions.
- `verifying_lock`: Active validation check in progress.
- `social_lock`: Lock triggered by stranger shoulder-surfing presence.
- `hostile_lock`: Security lockdown due to repeated verification failures.
- `verify_failed`: Unsuccessful recognition.

### 2. Command Packet (Sent to Daemon)
JSON formatted object sent via `\\.\pipe\MajestyGuard_CMD`:
```json
{
  "cmd": "verify_requested",
  "source": "island_click"
}
```
Supported commands:
- `verify_requested`: Manual trigger to initiate biometric scanning.
- `emergency_lock`: Instantly triggers `LockWorkStation()` lock state.

---

## 🧪 Verification & Testing

### Python Tests
Execute all unit tests for the computer vision pipeline and local servers:
```powershell
pytest daemon/
```

### C# Tests
Compile the core state machine and execute tests:
```powershell
dotnet test src/MajestyGuard.Tests/MajestyGuard.Tests.csproj
```

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Repository Sanitization & Standards Docs | Untrack AI files, update gitignore, remove assistant comments, setup community docs & templates, update README.md | none | PLANNED |
| 2 | Code Integrity & Test Fixes | Fix NameError in ui/states.py, verify 100% of Python/C# unit tests pass | none | IN_PROGRESS |
| 3 | E2E Test Suite Creation & Verification | Create E2E test cases (Tier 1-4), verify they pass, write TEST_READY.md | M2 | PLANNED |
| 4 | Push to GitHub | Force-push clean tree to github remote on main branch | M1, M2, M3 | PLANNED |
