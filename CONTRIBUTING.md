# Contributing

Thank you for considering a contribution. MajestyGuard handles camera input, biometric templates, and desktop input protection, so changes should be small, testable, and explicit about their security limits.

## Before You Start

- Open an issue to discuss substantial changes, especially changes to recognition, liveness, enrollment, IPC, overlay input, installation, or Windows lock behavior.
- Do not include face images, embeddings, local logs, personal paths, credentials, signing keys, or model files in commits or issue attachments.
- The repository license is proprietary and reserves rights. Ask the maintainer to confirm the licensing terms for a proposed contribution before submitting code; do not assume that public access or a pull request grants reuse rights.
- Model weights and dependency artifacts may have separate licenses. Do not add them without verifying redistribution permission.

## Development Setup

Use Windows 11 and Python 3.11. Create an isolated environment and install dependencies from the hash-pinned lock file:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock.txt
python -m pip install -r packaging\requirements-build.txt
```

Run focused tests while iterating, then the full supported suite:

```powershell
python -m pytest tests\app\test_control_center.py -q
python -m pytest tests daemon -q --tb=short
```

Some Windows camera, input-hook, installer, and lock behavior cannot be established by offscreen or unit tests. Do not enable real Windows locking or install startup tasks on a daily-use PC during automated tests. Document any live-device steps and their recovery route.

## Change Expectations

- Keep camera ownership and security decisions in the daemon; UI code must not grant access on its own.
- Preserve explicit fallback to Windows sign-in. Never describe the soft-lock as Secure Desktop.
- Avoid network access for biometric data, telemetry, or remote decisions.
- Add focused regression tests for behavior changes. Do not lower recognition or liveness thresholds to make tests pass.
- Keep generated installers, bundles, test captures, local state, and signing material out of commits.
- Describe observed behavior accurately; distinguish automated checks from physical-device validation.

## Pull Requests

Summarize the user-visible and security impact, list tests actually run, and identify untested Windows-specific behavior. Include only source, tests, and documentation needed for the change.
