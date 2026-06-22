# MajestyGuard — Architecture & Design Specification

MajestyGuard is a Windows-based hybrid security framework integrating low-light computer vision (CV) face recognition, monocular 3-D liveness checks, and a PyQt-based Apple Dynamic Island-styled lock screen overlay. It bridges the gap between Windows Hello (one-shot login) and continuous presence verification.

---

## 📂 Repository Directory Layout

The repository utilizes a clean, modular structure:

```text
MajestyGuard/
├── .github/                   # GitHub Actions workflows & issue/PR templates
├── companion/                 # Windows Hello Companion App (UWP C#)
├── daemon/                    # MajestyGuard Core Python Daemon (IPC, policy audit, monitors)
├── ui/                        # Dynamic Island UI & Soft-Lock overlay (PyQt)
├── setup/                     # PowerShell install/uninstall scripts
├── src/                       # Source files for C# modules
│   ├── MajestyGuard.Core/     # Core state machine, security vaults, IPC models
│   ├── MajestyGuard.Service/  # Windows service background host
│   ├── MajestyGuard.Overlay/  # Custom WinUI desktop overlay
│   ├── MajestyGuard.CVEngine/ # CV pipeline and face recognition wrapper
│   └── MajestyGuard.CredentialProvider/ # Credential Provider DLL (C++)
├── tests/                     # Integration and path safety tests
├── tools/                     # Legacy diagnostics & stubs
├── docs/                      # Technical plans, reports & operational manuals
├── LICENSE                    # MIT License
└── requirements.txt           # Python dependency file
```

---

## 🏗️ Architectural Topology

MajestyGuard operates on a decoupled multi-process architecture:

1. **Service Host (C# - `MajestyGuard.Service`)**: Runs as a background Windows service (LocalSystem). It manages the lifecycle of the Python daemon process and desktop UI overlay processes. It listens to session state changes (logon, logoff, lock, unlock) and coordinates system actions.
2. **Computer Vision Daemon (Python - `daemon/`)**: Connects to the webcam feed, processes frames locally, computes biometric embeddings using **AdaFace R100**, runs them through a **12-layer liveness stack**, and performs template validation.
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
* `idle`: System is dormant.
* `scanning`: Active biometric exploration.
* `active`: Verified state (unlock allowed).
* `welcome`: Welcoming the verified user.
* `stranger`: Unknown face detected.
* `locked`: Hard Windows lock handoff.
* `soft_locked`: MajestyGuard soft desktop lock overlay.
* `locked_passive`: Ambient lock state awaiting interactions.
* `verifying_lock`: Active validation check in progress.
* `social_lock`: Lock triggered by stranger shoulder-surfing presence.
* `hostile_lock`: Security lockdown due to repeated verification failures.
* `verify_failed`: Unsuccessful recognition.

### 2. Command Packet (Sent to Daemon)
JSON formatted object sent via `\\.\pipe\MajestyGuard_CMD`:
```json
{
  "cmd": "verify_requested",
  "source": "island_click"
}
```
Supported commands:
* `verify_requested`: Manual trigger to initiate biometric scanning.
* `emergency_lock`: Instantly triggers `LockWorkStation()` lock state.

---

## 🎯 Product Vision & Brand Personality

The target product is not a toy overlay and not a simple webcam locker. It is intended to feel like an OS-native security feature.

### Brand Personality
* **Personality**: Vigilant, invisible, trustworthy. The UI feels like a luxury security system — present when needed, gone when not.
* **Design Analogy**: Premium car dashboard indicators — minimal surface area, maximum information density, and instant recognition.
* **Anti-references**:
  - Loud red/yellow UIs with fear-based messaging.
  - Boxy, lifeless, generic Windows system dialogs.
  - Gaming overlay aesthetics (aggressive gradients, neon glow).

### Core Design Principles
1. **Disappear when trusted**: The UI's best state is invisible. Once the owner is verified, the pill shrinks to nothing.
2. **Escalate with restraint**: Threat level maps to visual weight, never to noise. No popups, no sounds.
3. **Earn trust through precision**: Every pixel of the Dynamic Island communicates system state. No decoration.
4. **Respect the desktop**: The overlay is a guest on the screen. Minimal footprint, no disruption.
5. **Fail visibly, recover silently**: Errors get clear visual states. Recovery happens without fanfare.

---

## 🔄 Core State Model & Door-Lock Principle

All application behavior is driven by a single state machine defined in `Core/StateMachine.cs`. States are:
* `DORMANT`: Non-enrolled profile or unavailable configuration. No active CV work and near-zero CPU.
* `BOOT_SCAN`: Login or unlock path is active; camera initializes and scanning begins.
* `VERIFYING`: A face is detected; recognition and liveness checks run; UI shows active scan.
* `UNLOCKED`: Owner verified; normal desktop access; low-frequency presence monitoring.
* `INACTIVITY_LOCK`: No keyboard/mouse input beyond threshold; visual/input lock requires re-verification.
* `SOCIAL_LOCK`: Owner present plus at least one stranger detected; privacy/safe mode activates.
* `HOSTILE_LOCK`: Owner absent, camera obstructed, feed tampered, or verification timeout; stronger lock state.

### The Door-Lock Principle
The lock behaves like locking a room door, not freezing everything inside the room.
* During ordinary lock, Spotify/audio, downloads, renders/builds, and network activity continue running in the background. The user simply cannot interact with them until re-verified.
* Input blocking and visual coverage are acceptable. Process suspension belongs only to `SOCIAL_LOCK` (privacy mode), and must be reversible, journaled, and atomic.

---

## 🧬 Biometrics & Liveness Requirements

Liveness is non-negotiable. A printed photo, phone photo, replay video, or virtual camera feed must not unlock the system. MajestyGuard utilizes a 12-layer liveness stack:

* MiniFASNet / anti-spoof model.
* rPPG CHROM blood-flow signal.
* MiDaS monocular depth signal.
* MediaPipe/attention/gaze signal.
* Texture / LBP-style cues.
* Specular reflection cues.
* Skin-tone and color consistency.
* Moire / screen replay frequency cues.
* Blink/micro-movement or temporal dynamics.
* Face boundary / frame-border spoof detection.
* Histogram/temporal consistency.
* Replay/static-frame detection.

### Performance & Latency
* **Inference Latency**: Target is under 200 ms per inference path.
* **Hysteresis**: The system uses confidence windows and hysteresis, not single-frame decisions, to prevent state oscillation.
* **CPU Budget**: Idle (1 FPS monitoring): $\le 3\%$; Active verification (10 FPS): $\le 15\%$ CPU.

### Enrollment Requirements
* **Multi-angle Capture**: Front, slight left, slight right, and glasses/no-glasses (if applicable).
* **Quality Gates**: Enrollment checks for lighting, sharpness, and alignment.
* **Secure Storage**: Biometric embeddings are encrypted at rest via DPAPI or TPM-backed protection tied to the Windows user profile/SID.

---

## 🎨 Visual Theme, Palette & Typography

The visual design is dark-material and Apple Dynamic Island-inspired, utilizing a near-black pill floating at the screen's top-center.

### Core Materials (Backgrounds)
* `#030303` — Pill body (near-black, standard active/verifying states)
* `#0A0A0A` — Pill body (active scanning)
* `#111111` — Pill body (idle/exit)
* `#020406` — Pill body (lock states, blue-tinted black)
* `#050202` — Pill body (hostile/failure, red-tinted black)

### Accent Colors (State-Driven)
* `#34C759` — Green: Active, verified, or verifying (Apple system green)
* `#FFB340` — Amber: Scanning, locked_passive, or social_lock (warm warning)
* `#64D2FF` — Cyan: Soft_locked, enrolling, or calibrating (neutral lock)
* `#FF453A` — Red: Stranger, hostile_lock, or verify_failed (Apple system red)
* `#343438` — Dark gray: Idle/exit (dormant)
* `#663333` — Muted red: Locked (Windows lock handoff)

### Typography
Typography utilizes the native Segoe UI Variable font family shipped with Windows 11.
* **Pill Labels**: Segoe UI Variable Display, 10px, Medium (500)
* **Welcome Label**: Segoe UI Variable Display, 10px, DemiBold (600)
* **Detail/Subtitle**: Segoe UI Variable Display, 7-8px, Medium
* **Score Chips**: Segoe UI, 6px, Medium
* **Overlay Corner Pill**: Segoe UI Variable Text, 9px, Medium

### Layout & Animations
* **Canvas**: Fixed 500x120px to prevent DWM compositor jitter.
* **Spring Physics**: Unified 60fps timer using stiffness = `0.18`, damping = `0.70`.
* **Reduced Motion**: Setting environment variable `MG_UI_REDUCE_MOTION=1` disables transitions.

---

## 🧩 UI Components
1. **IslandWidget (Pill)**: Contains states: `pill`, `dot_scan`, `verified`, `welcome`, `face_scan`, `enrollment`, `diagnostic`, `shield`, `failure`, `success`.
2. **SoftLockOverlay (Full-Screen Shield)**: Frosted-glass background capture (CPU 4-pass downsample blur), atmospheric gradients, tiled noise texture (192x192 at 11% opacity), corner status pill, and brand signature.

---

## ♿ Accessibility & Inclusion
* **High Contrast Mode**: Supported with WCAG AA minimum contrast ratios for text.
* **Reduced Motion**: Disables animations when `MG_UI_REDUCE_MOTION=1` is set.
* **Color-Blind Safe**: State status is always communicated by size, shape, or text labels, not color alone.

---

## 🏆 Project Milestones
| # | Milestone Name | Scope Summary | Status |
|---|----------------|---------------|--------|
| 1 | Repository Sanitization | Clean up AI/agent references from workspace, ignore local config, update standard docs | **COMPLETED** |
| 2 | Code Integrity & Tests | Fix states NameError, verify 100% of Python/C# unit tests pass | **COMPLETED** |
| 3 | Documentation Consolidation | Merge 13 fragmented documents into 3 structured pillars, update README | **IN PROGRESS** |
| 4 | GitHub Release | Commit, push clean repository tree, setup Action builds | **PLANNED** |
