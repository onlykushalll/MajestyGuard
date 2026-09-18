# MajestyGuard Desktop App and Installer Design

## Status

Approved architecture for implementation. This document defines the active
user-space product path. It does not revive the retired Windows Service,
Credential Provider, WHCDF, SafeBoot, firewall, or hosts-file architecture.

## Product Boundary

MajestyGuard remains a local, user-space presence-verification application for
an already signed-in Windows session. The Windows sign-in screen and Windows
credential security remain Windows' responsibility.

The application consists of:

1. A Control Center that lives in the notification area and owns runtime
   lifecycle.
2. The existing daemon, which owns the camera, recognition, liveness, policy,
   and state transitions.
3. The existing PyQt Dynamic Island and soft-lock overlay, which only render
   state and submit the current limited UI commands.
4. A conventional installer and uninstaller for the active user-space stack.

No new UI component may import CV, liveness, threshold, or overlay-internals
code. The daemon remains the sole authority for biometric decisions.

## Lifecycle and IPC

The Control Center is the supervised parent process for the daemon. It launches
the daemon with an explicit one-time supervisor identity supplied through a
private inherited mechanism rather than a PID text file in LocalAppData. The
daemon continues to launch and own the Dynamic Island process.

The existing named-pipe contracts remain narrow:

- `\\.\pipe\MajestyGuard_UI` carries daemon state to the owned display client.
- `\\.\pipe\MajestyGuard_CMD` accepts only `verify_requested`,
  `emergency_lock`, and `windows_lock_used` from the owned display client or
  authenticated Control Center.

The Control Center does not receive biometric scores, raw frames, embeddings,
or an unlock capability. It can report daemon health using a separate,
minimal status contract and can request safe lifecycle actions. No PID file in
user-writable storage is an authentication mechanism.

## Control Center

The Control Center is a PyQt6 notification-area application. It is deliberately
restrained rather than a second security dashboard.

Its menu and window provide:

- Protection status and a clear running/degraded/not-enrolled state.
- Start, stop, and restart controls for the local daemon.
- Guided re-enrollment entry point.
- Diagnostics summary and a link to local logs.
- Soft-lock timeout configuration with bounded, policy-audited values.
- Pause protection for the current signed-in session.
- Safe quit, which stops children and removes no biometric data.

Recognition and liveness thresholds are not exposed as casual settings. Any
future advanced calibration must be explicit, validated, and separately
designed.

## First Run

The installer must never activate real locking automatically. On first launch,
the Control Center detects whether the current Windows user has enrollment data.
If not, it starts the existing guided enrollment workflow and clearly reports
that protection is unavailable until enrollment succeeds.

For the first packaged daily-use release:

- Soft lock stays opt-in after a successful, user-visible physical test.
- `MG_ENABLE_LOCK` remains disabled by default.
- Camera use is explicit: the daemon owns it only during the selected active
  or verification states defined by the current policy.
- Existing enrollment is preserved across updates and standard uninstall.

## Packaging

Use Inno Setup for a normal Windows installer and uninstaller. It installs into
the current user's local application location and registers a logon startup
entry for the Control Center, not a Windows service.

The packaging pipeline must create a relocatable application bundle. It may not
ship a copied virtual environment that retains hard-coded source paths. The
bundle contains the exact pinned Python runtime and application dependencies,
the daemon, UI, models, scripts, and required assets.

Uninstall must:

1. Stop the Control Center, daemon, and owned UI processes.
2. Remove the current user's startup registration.
3. Remove installed application files and non-biometric runtime data.
4. Preserve enrollment by default.
5. Offer a separate, clearly labeled removal of enrollment and diagnostic logs.

The installer is intentionally unsigned until trusted signing is available.
Documentation must disclose that Windows may warn about unsigned builds. This
does not change the current application's user-space security boundary.

## Documentation and Release Position

The README and current-user documentation will describe the active Python/PyQt
architecture and distinguish it from legacy experiments stored in the
repository. They must not market the product as a Windows login replacement,
Secure Desktop equivalent, or a completed Credential Provider.

The first release gate requires:

- Automated suite passing on the packaged-source tree.
- A manual first-run enrollment check.
- A daily-use soft-lock verification loop.
- A stranger test and physical photo/video spoof test recorded as evidence.
- A clean install, update, and uninstall/reinstall test.

## Error Handling and Safety

The Control Center must fail closed with respect to access decisions: it never
claims protection is active unless its daemon health handshake confirms it. If
the daemon stops unexpectedly, it presents a degraded status and offers a
restart. It must not silently enable a real Windows lock, alter Windows
credentials, or modify network, firewall, SafeBoot, or hosts-file settings.

The existing staged `tray.pid` authorization experiment is not part of this
design. It will be removed or replaced during implementation by a tested,
single-owner supervisor contract.

## Test Strategy

- Unit tests cover lifecycle ownership, argument construction, configuration
  bounds, enrollment detection, and safe failure behavior.
- Contract tests cover the small Control Center/daemon status protocol and
  verify that arbitrary same-user processes cannot self-register as trusted.
- Installer tests validate file boundaries, startup registration, uninstall
  safety, and enrollment preservation without requiring elevation.
- Existing daemon, UI, and liveness tests remain mandatory regression gates.
- Manual camera and physical adversarial tests remain release gates and are
  not replaced by automated tests.
