# Security Policy

MajestyGuard is an experimental Windows desktop security project. The user-space overlay is not a Windows Secure Desktop boundary, and no release should be treated as a replacement for Windows sign-in or device security.

## Supported Versions

There are currently no production-supported releases. Security fixes are developed on the default branch; local builds and release candidates may be incomplete.

## Report a Vulnerability Privately

Please use GitHub's **Report a vulnerability** option on the repository's Security tab when it is available. If private reporting is unavailable, open a minimal public issue asking for a private reporting route; do not include exploit steps, logs, credentials, biometric files, or other sensitive details in that issue.

Repository: <https://github.com/onlykushalll/MajestyGuard>

Please include the affected commit or release, Windows/Python version where relevant, impact, and a minimal reproduction that does not contain personal data. Allow time for investigation before publicly disclosing details. There is no guaranteed response or fix timeline.

## Security Boundaries

- Face images, embeddings, and liveness signals are intended to remain on-device; see [PRIVACY.md](PRIVACY.md).
- The project must not store Windows passwords or PINs, silently weaken Windows protections, or make machine-level changes without explicit user action.
- The soft-lock overlay can be bypassed by Windows security paths, other privileged processes, or a sufficiently capable same-user process. Use **Win+L** when leaving the device unattended.
- Model hashes help detect unexpected changes but do not prove model licensing, provenance, or resistance to tampering by a process with the user's privileges.
- Keep enrollment files, logs, signing keys, and local release artifacts out of public reports and commits.

## Safe Testing

Automated tests must not invoke real workstation locking or alter persistent Windows security settings. Live camera and input-hook tests require an explicit recovery plan and must not be presented as passed unless they were actually observed on the target device.
