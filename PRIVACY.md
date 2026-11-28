# Privacy Policy

**Last reviewed:** November 29, 2026

This policy describes the MajestyGuard Windows desktop application in this repository. It is an operational description, not a claim of legal compliance or a promise that every future build will behave identically. Review the policy for each release.

## Data Processed on This PC

When the relevant feature is used, MajestyGuard may process:

- Live webcam frames for face detection, owner matching, and liveness checks.
- Enrollment face embeddings and profile metadata, including selected view/coverage labels.
- Local security events and diagnostic details such as timestamps, state changes, camera status, timing/resource measurements, and error information. Logs may reveal local paths or device/runtime details; review them before sharing.
- Local settings needed to run the application.

Camera frames are processed in memory and are not intentionally saved as image or video files by the active application. Enrollment data and settings are stored on the device under the current Windows user profile. The face gallery is protected using Windows DPAPI in the active implementation; related metadata, settings, and diagnostic logs are separate files and should not be assumed to receive the same encryption. Anyone with access to the signed-in Windows account or a process running as that user may be able to access data available to that account.

MajestyGuard does not store Windows passwords or PINs. Never enter a Windows password into an untrusted prompt or share enrollment data, logs, or diagnostic bundles publicly.

## Network and Third Parties

The active application has no project-operated account, analytics, or telemetry service and does not intentionally transmit camera frames, face embeddings, or local diagnostics to project maintainers. The current installer is built from local model inputs and does not automatically download models. Windows, camera drivers, security software, and separately installed dependencies remain subject to their own behavior and policies.

Future model-download or update features would change this description and must be documented before release. Model hashes verify file integrity against a reference; they do not establish license permission or guarantee model quality.

## Retention and User Controls

Enrollment files, settings, and logs remain on the local device until removed by the user or an uninstall option. Standard uninstall is intended to preserve user data unless the user explicitly chooses its removal. Back up or delete local data only through the documented application/uninstaller controls; do not manually remove files while protection is running.

## Shared Devices and Other People

Use the camera only where you have appropriate permission. Tell people who may be in view that the camera is being used for local presence verification. Do not enroll another person or run a test against them without their informed agreement. Do not publish images, biometric data, or identifying logs.

## Contact

For general questions, use the [project repository](https://github.com/onlykushalll/MajestyGuard). Do not post biometric files, credentials, private logs, or detailed vulnerability information in a public issue. See [SECURITY.md](SECURITY.md) for security reports.
