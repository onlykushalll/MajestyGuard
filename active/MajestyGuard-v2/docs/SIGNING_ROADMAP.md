# MajestyGuard Signing Roadmap

MajestyGuard is moving on two tracks:

1. User-space daemon now: the Python daemon handles camera recognition, liveness,
   stranger detection, SocialLock behavior, Dynamic Island UI updates, and
   a fullscreen desktop soft-lock overlay after idle/absence or a confirmed
   stranger. It does not store the Windows password and it does not inject input
   into Secure Desktop.
2. Signed Windows integration later: once trusted Authenticode signing is
   available, the Windows Service and Credential Provider can be installed on
   Smart App Control systems and tested as the lock-screen integration path.

## Current Windows Policy Gap

Windows Hello Companion Device Framework (WHCDF) returned DisabledByPolicy on
this machine, so it is not a viable local development path. Smart App Control
also blocks self-signed Credential Provider and managed service binaries. This
is not a face-recognition bug; it is a Windows trust and signing requirement.

For local development, do not turn Smart App Control off. The production-grade
path is to obtain trusted code signing rather than weakening the user's Windows
security posture.

## SignPath Plan

SignPath Foundation signing is the preferred free path for the public
open-source repository. After approval:

- Build release artifacts from a clean CI workflow.
- Sign the Windows Service, Credential Provider DLL, overlay binaries, and any
  native helper binaries with the trusted certificate chain.
- Re-run service and Credential Provider installation on a SAC-enabled machine.
- Validate that the Credential Provider tile appears on the lock screen.
- Validate that the Python daemon, service bridge, and Credential Provider agree
  on recognition/liveness state without storing passwords.

## What Remains Blocked Until Signing

- Credential Provider loading on the lock screen.
- SYSTEM service orchestration on SAC-protected machines.
- Automatic lock-screen login flow driven by a signed provider.

## What Works Before Signing

- User-space daemon startup at logon.
- Camera recognition and 12-layer liveness checks.
- Stranger detection and SocialLock behavior.
- Fullscreen desktop soft-lock shield after idle/absence or stranger evidence.
- Owner face verification clears the desktop shield and resumes monitoring.
- Background apps continue running during ordinary desktop soft-lock.
- Manual PIN unlock remains the fallback for real Windows lock-screen sessions.
