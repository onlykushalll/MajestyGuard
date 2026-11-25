# Install and Update MajestyGuard

## What the Installer Provides

The installer deploys the active current-user MajestyGuard application to
`%LOCALAPPDATA%\Programs\MajestyGuard` and adds a Start menu shortcut.
It does not enable automatic startup, install a service, modify Windows sign-in,
or activate a real Windows lock automatically.

Open MajestyGuard from Start. If enrollment is missing, select **Enroll face**
and follow the moving dot until the coverage ring fills. A normal update does
not require re-enrollment. **Start over** is separate and, for an existing
profile, requires confirmation of the current Windows account before scanning.
Start protection only after reviewing the camera flow and soft-lock behavior on
your own device.

## Signing and Windows Protection

Until the project has trusted code signing, local release builds are unsigned.
Windows reputation checks or Smart App Control can warn about, or block,
unsigned unknown software. Treat that as a safety boundary: install only a build
you produced or received from a source you trust. This guide does not provide
instructions to bypass Windows protections.

## Daily Use

The Control Center owns the local daemon lifecycle. Its notification-area menu
lets you start or pause protection, change the bounded soft-lock timeout,
re-enroll, and open the local diagnostics directory. Pausing and quitting stop
only MajestyGuard-owned processes; neither action deletes biometric data.

The soft-lock overlay is a user-space UI layer. Background applications can
continue running while it is shown. It is not equivalent to the Windows lock
screen, and Ctrl+Alt+Del remains controlled by Windows.

## Updating

Close or pause MajestyGuard, then run a newer installer. Installation files are
replaced in the app directory while current-user enrollment and settings remain
under `%LOCALAPPDATA%\MajestyGuard`.

## Uninstalling

Standard uninstall removes the installed application and its Start menu shortcut.
Choosing **No** at the optional data-removal prompt preserves enrollment.
Silent uninstall also leaves enrollment intact.

The uninstaller explicitly asks **Remove enrollment and diagnostic data?**.
Choose **Yes** only when you intend to delete `embeddings_v2.npy`,
`biometric.mgd`, settings, and local logs from `%LOCALAPPDATA%\MajestyGuard`.

## Release Gates

Do not describe a build as ready for daily use until all of the following have
been recorded on the intended device:

1. Automated source and packaging tests pass.
2. Clean install, update, normal uninstall, and reinstall behave as documented.
3. First-run enrollment and a normal daily soft-lock/verify/unlock loop work.
4. A real stranger attempt stays blocked.
5. A printed-photo spoof attempt stays blocked.
6. A video replay spoof attempt stays blocked.
7. The Dynamic Island and overlay render correctly at the target display scale.

These physical checks cannot be replaced by automated tests because camera,
lighting, placement, and real presentation attacks are device-specific.
