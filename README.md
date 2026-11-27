# MajestyGuard

MajestyGuard is a local, current-user Windows presence-verification app. It
combines webcam recognition, layered liveness checks, a PyQt Dynamic Island,
and a full-screen soft-lock overlay for an already signed-in desktop session.

It is an experimental personal-security project. The active product is the
Python/PyQt application described here, not the repository's retired service
or login experiments.

## Product Boundary

MajestyGuard runs after Windows sign-in. It can show a soft-lock overlay and
ask for local face verification before returning desktop input, but it is not a
Windows sign-in replacement and it is not a Secure Desktop equivalent. Windows
credentials, the real lock screen, and OS-level security remain Windows'
responsibility.

The active runtime has three local processes:

1. **Control Center**: notification-area app for starting, pausing,
   re-enrolling, reviewing diagnostics, and setting the soft-lock timeout.
2. **Daemon**: sole owner of the camera, recognition, liveness, policy, and
   security-state transitions.
3. **Dynamic Island / overlay**: PyQt display client that renders daemon state
   and only sends the existing limited commands.

No cloud account, telemetry service, or remote decision service is required for
the active runtime.

## Repository Architecture

```text
MajestyGuard/
├── app/                       # PyQt6 Obsidian Glass Control Center & process supervisor
├── daemon/                    # Core Python Biometric Daemon (ArcFace/AdaFace, 12-layer liveness, IPC)
├── ui/                        # PyQt6 Dynamic Island, Soft-Lock Shield & 3D Face ID Enrollment Studio
├── packaging/                 # PyInstaller bundle builder & Inno Setup per-user installer (.iss)
├── models/                    # Local ONNX liveness/detection models & SHA-256 reference manifest
├── models_insightface/        # Local InsightFace buffalo_l recognition models
├── scripts/                   # Bounded manual launchers (run_daemon.bat, run_ui.bat)
├── setup/                     # User startup tasks, signing helpers & machine-state security cleanup
├── tests/                     # 440+ unit, contract, render, privacy & soak tests (app, daemon, ui, packaging)
├── docs/                      # Architecture specs, threat model, signing roadmap & consolidated audit ledger
├── src/ & companion/          # Retired v1 C#/.NET Windows Service & Credential Provider stack (inert/historical)
└── archive/                   # Historical audits, session handoffs & retired v1 duplicates (gitignored)
```

## Development Setup

Use Python 3.11. Create an isolated environment, then install the pinned local
runtime dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -r packaging\requirements-build.txt
```

For hash-checked downloads, replace the runtime install line with
`python -m pip install --require-hashes -r requirements.lock.txt`. Update the
lock with `python tools/gen_requirements_lock.py` when runtime pins change.

Run the automated suite from the repository root:

```powershell
python -m pytest tests daemon -q --tb=short
dotnet test .\src\MajestyGuard.Tests\MajestyGuard.Tests.csproj
```

For development-only operation, use the existing launch scripts after a
successful enrollment:

```powershell
scripts\run_daemon.bat
scripts\run_ui.bat
```

## Build a Local Bundle

The release build is generated with PyInstaller; it does not copy a development
virtual environment. The vision model directories are intentionally supplied as
local build inputs because they are large binary assets and are not tracked in
Git.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_bundle.ps1 `
  -PythonPath .\.venv\Scripts\python.exe `
  -ModelDir C:\path\to\models `
  -InsightFaceModelDir C:\path\to\models_insightface
```

The resulting bundle is written to `dist\MajestyGuard`. Add
`-CreateInstaller` only after Inno Setup 6 is installed. See
[the install and update guide](docs/guides/INSTALL_AND_UPDATE.md) for the
installer boundary and release gates. The bundle keeps the Control Center,
overlay, and daemon in adjacent one-directory packages; keep the complete
`MajestyGuard` bundle together rather than moving individual executables.

## Documentation

- [Desktop app and installer design](docs/superpowers/specs/2026-09-19-desktop-app-and-installer-design.md)
- [Control Center runtime plan](docs/superpowers/plans/2026-09-19-control-center-runtime-plan.md)
- [Packaging and release plan](docs/superpowers/plans/2026-09-19-packaging-and-release-plan.md)
- [Install and update guide](docs/guides/INSTALL_AND_UPDATE.md)
- [Consolidated audit & verification ledger](docs/AUDIT_AND_VERIFICATION_LEDGER.md)

## License

See [LICENSE.md](LICENSE.md) for the repository terms. Third-party model weights have separate terms; the included model manifest does not grant permission to redistribute weights.
