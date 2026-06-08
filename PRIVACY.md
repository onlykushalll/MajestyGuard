# Privacy Policy

MajestyGuard is designed as a local-first Windows security project. Its privacy goal is to protect the user's desktop session without sending biometric data to external services.

## Summary

- Camera frames are processed on the local machine.
- MajestyGuard does not intentionally upload camera frames, face embeddings, or biometric templates to cloud services.
- Enrollment artifacts are intended to stay on the user's device and be protected with Windows user-profile security such as DPAPI.
- Runtime logs should avoid storing raw camera frames or biometric templates.

## Data Processed Locally

MajestyGuard may process the following data on the user's own Windows device:

- webcam frames used for face detection, recognition, and liveness checks
- face embeddings generated during enrollment
- local confidence scores and liveness signals
- local state-machine events such as scanning, active, locked, or suppressed-lock states
- configuration required to run the local service, overlay, or CV engine

## Data Not Intentionally Collected Remotely

MajestyGuard does not intentionally send the following to project maintainers or third-party services:

- webcam images or video
- biometric templates or face embeddings
- passwords, PINs, or Windows credentials
- browsing history
- private documents

## Logs

Development builds may write diagnostic logs for debugging. Logs should be reviewed before being shared publicly because they may include local paths, timing information, machine state, or other operational details. Raw biometric data and camera frames should not be logged.

## User Control

MajestyGuard's machine-level features are intended to be opt-in. Installation and uninstallation scripts should clearly describe system changes and provide a rollback path.

## Third-Party Components

MajestyGuard depends on third-party open-source components such as .NET, Python packages, ONNX/runtime tooling, and Windows SDK components. Those components may have their own licenses and documentation.

## Contact

Use the GitHub repository issue tracker for project questions and privacy-related concerns:

https://github.com/onlykushalll/MajestyGuard/issues
