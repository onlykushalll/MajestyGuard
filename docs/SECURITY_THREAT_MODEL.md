# Security Threat Model

**Scope reviewed:** November 29, 2026. This is a high-level description of the active Python/PyQt6 desktop product, not a certification or proof of resistance. Source code and live Windows behavior may differ; re-review this document when security-sensitive code changes.

## Intended Protection

MajestyGuard is intended to add a local presence check and user-space soft-lock layer to an already signed-in Windows session. The daemon owns camera processing, recognition/liveness decisions, and security state. The Control Center supervises the daemon, while the Dynamic Island and overlay render status and send limited requests.

The overlay is **not** Windows Secure Desktop and cannot guarantee protection against another process, a privileged user, or every Windows input and accessibility path. Touchpad gestures, OS-level security screens, system dialogs, crashes, and process termination require separate live validation. Keep Windows sign-in active and use **Win+L** when leaving the PC unattended.

## Assets and Local Data

- **Camera frames:** consumed locally for inference; the active app does not intentionally save camera images or video or transmit them to project maintainers.
- **Enrollment gallery:** stored under the current Windows user profile and protected with Windows DPAPI in the active implementation. This does not protect against malware running as that user, memory inspection, or an unlocked account.
- **Enrollment metadata, settings, and diagnostics:** local files with protection distinct from the encrypted gallery. Logs can include event timing, state, camera/runtime status, errors, and local paths; review before sharing.
- **Model files:** local build inputs or packaged files. Reference hashes detect unexpected content changes only when the reference itself is trusted; they do not prove origin, licensing, or robustness.

## Main Threats and Limits

| Threat | Intended mitigation | Remaining limit |
|---|---|---|
| Casual walk-up use after an inactivity check | Recognition/liveness policy and a full-screen overlay | User-space UI is bypassable; no Secure Desktop guarantee |
| Another person or presentation attack | Face matching and multiple liveness signals | Physical stranger and spoof coverage is not established by unit tests; false accepts remain possible |
| Malformed, stale, or unauthorized local IPC | Named-pipe ACLs and client validation in the daemon | Same-user malware or an administrator can still interfere with a user-session app |
| Local disk access to enrollment | Windows DPAPI for the gallery | Metadata/logs differ; DPAPI does not defend against same-user malware or a running-process memory dump |
| Camera/device/runtime failure | Status reporting and recovery through Windows | Failure may prevent verification; preserve a normal Windows sign-in method |
| Installer or model tampering | Package/model integrity checks and code-signing roadmap | The project has no publicly trusted publisher certificate; a local self-issued signature is not public trust |

## Safe Operation

- Do not disable Windows security protections to install MajestyGuard.
- Do not treat the overlay as the only security control or leave the device unattended without using **Win+L**.
- Do not test on another person without informed agreement. Do not store or publish face images or enrollment data.
- Keep recovery available before testing camera, input-hook, or lock paths.
- Treat automated tests as code-level evidence only. Physical camera, touchpad, sleep/resume, and installer lifecycle claims require explicit device or VM observations.

## Implementation References

The active pipe names are declared in `ui/states.py` and `daemon/cmd_server.py` (`MajestyGuard_UI` and `MajestyGuard_CMD`). Review access-control and message handling in `daemon/` and `ui/` whenever those files change. This document intentionally avoids treating implementation comments or tests as proof of the full Windows security boundary.
