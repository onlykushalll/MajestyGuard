# MajestyGuard

MajestyGuard is an experimental, local-first Windows 11 app for presence checks during an already signed-in desktop session. It combines on-device face recognition and liveness checks with a PyQt6 Control Center, status island, and soft-lock overlay.

> **Security boundary:** The overlay is a user-space layer, not Windows Secure Desktop, a Windows sign-in provider, or a replacement for your password, PIN, Windows lock screen, or device encryption. Keep Windows security enabled and use **Win+L** when leaving the PC unattended.

## What It Does

- Runs the camera, face matching, liveness checks, and lock decisions locally.
- Uses a separate daemon as the authority for recognition and security-state transitions; the UI displays state and issues limited commands.
- Can show a full-screen soft-lock overlay while background applications continue running.
- Stores enrollment and diagnostics on the local Windows profile. See [Privacy](PRIVACY.md) for the data details and limitations.
- Does not require a cloud account or intentionally send biometric data to project maintainers.

It is not a production-certified security product. Recognition, presentation-attack resistance, recovery behavior, installer upgrades, and device-specific workflows require further live validation. Do not rely on MajestyGuard as your only protection.

## Current Architecture

```text
app/          PyQt6 Control Center, settings, and process supervisor
daemon/       Camera ownership, recognition/liveness, policy, and IPC
ui/           Dynamic Island, soft-lock overlay, and enrollment UI
assets/       Project branding and offline interface artwork
packaging/    PyInstaller bundle and per-user Inno Setup installer
models/       Local model inputs (not distributed through this source repository)
tests/        App, daemon, UI, policy, and packaging tests
docs/         Setup, architecture, security, and verification guidance
src/, companion/  Retired/experimental .NET and Credential Provider work; not the active product
```

The active application is the Python/PyQt6 user-space product. The retired .NET service and Credential Provider experiments are not part of the supported install path.

## Requirements

- Windows 11, a working webcam, and Python 3.11 for source development.
- The model files expected by the selected build. Model weights are not included in this Git repository; obtain them only from sources permitted by their licenses and verify them against the project's reference hashes.
- Visual Studio Build Tools are not required for the active Python application. The legacy C++/C# experiments have separate, unsupported build requirements.

## Development

Create an isolated environment and install the hash-pinned runtime dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock.txt
python -m pip install -r packaging\requirements-build.txt
```

Run the automated suite from the repository root:

```powershell
python -m pytest tests daemon -q --tb=short
```

Build a local bundle with model directories you are authorized to use:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_bundle.ps1 `
  -PythonPath .\.venv\Scripts\python.exe `
  -ModelDir C:\path\to\models `
  -InsightFaceModelDir C:\path\to\models_insightface
```

The build writes to `dist\MajestyGuard`. An installer can be created with `-CreateInstaller` when Inno Setup 6 is installed. The current installer packages local build inputs; it does **not** fetch models from the web. See [Install and Update](docs/guides/INSTALL_AND_UPDATE.md) before installing a build.

## Privacy, Security, and License

- [Privacy policy](PRIVACY.md)
- [Use terms and limitations](TERMS.md)
- [Security reporting](SECURITY.md)
- [Threat model](docs/SECURITY_THREAT_MODEL.md)
- [Install and update guide](docs/guides/INSTALL_AND_UPDATE.md)
- [Verification ledger](docs/AUDIT_AND_VERIFICATION_LEDGER.md)

The repository's source license is stated in [LICENSE.md](LICENSE.md), which currently reserves rights. It is **not** an MIT-licensed or OSI-approved open-source release. Third-party packages and model weights have their own terms; the presence of a hash manifest does not grant redistribution rights.

## Project Status

Development is active. There is no production-supported release. Physical camera, stranger, presentation-attack, sleep/resume, multi-monitor, and full installer upgrade tests are device-specific release gates; automated tests alone do not satisfy them. See the verification ledger and install guide for current status.
