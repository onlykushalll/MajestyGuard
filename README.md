# MajestyGuard

MajestyGuard is an experimental Windows security project that brings a local-first, Face ID-inspired presence layer to the desktop.

It combines webcam-based face recognition, passive liveness checks, a Windows service, a Dynamic Island-style overlay, and a Credential Provider research path. The goal is simple: protect the user's Windows session when the owner is away, while keeping biometric processing on the device.

> Status: active research prototype. MajestyGuard is not yet a production login replacement. Service and lock-screen integration require trusted code signing and careful deployment review.

## Why This Exists

Windows has strong authentication, but it does not provide an Apple/Samsung-style local presence experience for ordinary desktop workflows. MajestyGuard explores that gap:

- recognize the enrolled owner locally
- detect presence changes while the desktop is active
- apply protective lock or restricted states when the owner leaves
- keep camera frames and biometric templates off the cloud
- provide a clear, polished overlay instead of a noisy background utility

## Core Principles

- **Local-first biometrics:** Camera frames are processed on-device.
- **No cloud face processing:** MajestyGuard does not upload camera frames or biometric templates.
- **User-controlled deployment:** Service install, Credential Provider registration, and lock-screen work are opt-in.
- **No security weakening:** Smart App Control, Windows security settings, and OS policy protections should stay intact.
- **Recoverable operations:** Installer behavior must be paired with uninstall/recovery paths.
- **Honest status:** Experimental features are labeled as such until signed, verified, and documented.

## Architecture

```text
MajestyGuard/
  src/
    MajestyGuard.Core/                Shared state machine, IPC contracts, security models
    MajestyGuard.Service/             Windows service orchestration layer
    MajestyGuard.Overlay/             WinUI desktop overlay
    MajestyGuard.CVEngine/            Python face recognition and liveness engine
    MajestyGuard.CredentialProvider/  Windows Credential Provider research path
    MajestyGuard.Tests/               .NET test suite
  tests/                              Installer and integration safety tests
  tools/                              Diagnostics, stubs, and release helpers
  docs/                               Architecture, signing, and operational notes
```

The service is intended to be the coordinator. It receives CV results, moves the state machine, tells the overlay what to show, and eventually bridges to lock-screen components when trusted signing is available.

## Current Capabilities

- C#/.NET core state machine and IPC contracts
- Windows service prototype with service-only validation flow
- WinUI overlay and Dynamic Island-style status surface
- Python CV engine with face recognition, liveness, attention, depth, and virtual camera checks
- DPAPI-backed embedding storage work
- Installer/uninstaller scripts with safety checks
- Test coverage for service behavior, installer safety, IPC schema, and state transitions

## Current Limitations

- Trusted production signing is not complete yet.
- Credential Provider registration is not enabled by default.
- Smart App Control can block self-signed binaries; production builds need a trusted signing path.
- Live camera validation requires supervised local testing.
- This repository is being prepared for public open-source review and signing eligibility.

## Privacy

MajestyGuard is designed to avoid remote biometric processing. See [PRIVACY.md](PRIVACY.md) for the current privacy position.

## Code Signing Policy

MajestyGuard intends to use a public, auditable signing process for release artifacts. See [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md).

## Build

Requirements:

- Windows 11
- .NET SDK 8
- Visual Studio Build Tools with C++ workload for Credential Provider work
- Python 3.11 for CV engine work

Basic .NET validation:

```powershell
dotnet test .\src\MajestyGuard.Tests\MajestyGuard.Tests.csproj
```

Package staging:

```powershell
powershell -ExecutionPolicy Bypass -File .\Build.ps1
```

Administrative install tests should only be run on a machine where you have a recovery plan. Do not register the Credential Provider on a daily-use system unless you understand the rollback process.

## Safety Notes

- Do not commit biometric enrollment data, model files, local logs, or user config.
- Do not lower recognition/liveness thresholds to force tests to pass.
- Do not bypass Windows security policy for production validation.
- Do not enable real locking behavior during development without explicit safety flags and a recovery path.

## Roadmap

- Publish a clean open-source release candidate.
- Complete SignPath Foundation / trusted signing readiness.
- Add reproducible GitHub Actions release workflow.
- Finish signed service and Credential Provider validation.
- Harden installer rollback and recovery documentation.
- Prepare a polished Windows-first onboarding and enrollment flow.

## License

MajestyGuard is released under the MIT License. See [LICENSE](LICENSE).
