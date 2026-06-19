# MajestyGuard v2 Diagnostics

The duplicate diagnostic scripts have been consolidated. To prevent stale code version issues, the active scripts are now maintained and executed directly from the top-level `/daemon` directory:

- `/daemon/mg_owner_preflight.py` (Camera/face enrollment checklist)
- `/daemon/mg_recog_diag.py` (Face recognition diagnostics)
- `/daemon/mg_layers3.py` (Liveness verification diagnostic)
- `/daemon/mg_onnx_probe.py` (ONNX execution provider verification)
- `/daemon/check_enrollment.py` (Offline enrollment inspection)
- `/daemon/check_enrollment_pose_coverage.py` (Biometric vector math checks)
- `/daemon/mg_policy_audit.py` (Policy threshold safety audits)
- `/daemon/mg_run_summary.py` (Diagnostics reporter)

To run the daemon in evaluation mode with locking disabled:
Execute `run_daemon.bat` from the repository root (ensure `MG_ENABLE_LOCK` is set to `0` in your environment or inside the batch file).
