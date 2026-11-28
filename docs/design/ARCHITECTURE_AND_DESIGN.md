# Architecture and Design

This document describes the active MajestyGuard application. Source code is authoritative where details change; this page is a high-level map, not a guarantee that every Windows-specific behavior has been live-tested.

## Product Boundary

MajestyGuard runs in the current user's already signed-in Windows session. Its soft-lock overlay is a user-space deterrent and input-capture layer, not Windows Secure Desktop, a Windows sign-in provider, or a substitute for Windows authentication. See the [threat model](../SECURITY_THREAT_MODEL.md) for limitations.

## Runtime Components

```text
Control Center (app/)
    starts/stops and monitors the runtime; owns user settings
             |
             | bounded local command channel
             v
Recognition daemon (daemon/)
    owns camera, model loading, enrollment data access,
    face/liveness decisions, and security state transitions
             |
             | local state channel
             v
PyQt UI (ui/)
    renders the Dynamic Island and soft-lock overlay;
    sends limited requests but does not authorize recognition
```

The active IPC names are declared in source (currently `MajestyGuard_CMD` and
`MajestyGuard_UI`). Message formats and pipe access controls must be reviewed
against `daemon/` and `ui/` when modified; this overview is not a protocol
specification.

## Data and Trust Boundaries

- Camera frames are consumed by the daemon for local processing. The active
  application does not intentionally upload biometric data or diagnostics.
- Enrollment storage, settings, and diagnostic logs are local and have
  different protection properties. The gallery uses Windows DPAPI; associated
  metadata and logs should not be assumed encrypted.
- The UI can request limited actions, but access decisions belong to the
  daemon. IPC validation reduces risk but does not protect against all malware
  already running with the user's privileges.
- Model hashes can detect unexpected file changes against a trusted reference;
  they do not prove a model's licensing or make the user profile tamper-proof.

## Packaging

`packaging/` builds adjacent per-user application, daemon, and UI bundles and
an Inno Setup installer. Model directories are local build inputs and are not
tracked in the source repository. The installer currently uses supplied model
files; automatic web downloads are future work. Review the
[install guide](../guides/INSTALL_AND_UPDATE.md) and
[signing/release policy](../signing/CODE_SIGNING_AND_RELEASES.md) before
distributing a build.

## Historical Components

`src/` and `companion/` contain retired or experimental .NET, service, and
Credential Provider work. They are not part of the active supported install
path and should not be described as currently functional or production-ready.
