# MajestyGuard v2 Diagnostics Import

These tools were copied from the v2 proving-ground repo as non-conflicting
diagnostics for the main MajestyGuard repository. They do not enable locking,
Credential Provider registration, service install, SafeBoot, test signing, or
registry hardening.

## Model paths

Camera/model diagnostics use the existing v2 model folders by default:

- `C:\tmp\MajestyGuard\active\MajestyGuard-v2\models`
- `C:\tmp\MajestyGuard\active\MajestyGuard-v2\models_insightface`

Set `MG_V2_ROOT` if the v2 proving-ground folder moves.

## Camera tools

The following scripts open the webcam and must only be run after an explicit
camera-test warning:

- `mg_owner_preflight.py`
- `mg_recog_diag.py`
- `mg_layers3.py`
- `mg_onnx_probe.py`
- `run_daemon_v2_safe.bat`

`run_daemon_v2_safe.bat` keeps `MG_ENABLE_LOCK=0`, keeps WHCDF IPC disabled by
default, and runs `mg_policy_audit.py --require-bound` before starting.

## Offline tools

These are safe offline checks:

- `check_enrollment.py`
- `check_enrollment_pose_coverage.py` checks both enrollment metadata and the
  selected embedding matrix shape/count/finite/normalization.
- `mg_policy_audit.py`
- `mg_run_summary.py`
