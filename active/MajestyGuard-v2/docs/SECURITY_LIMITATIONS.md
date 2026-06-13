# Security Limitations

## Desktop Soft-Lock Bypass Resistance

The current soft-lock is a user-session overlay. It is valuable because it blocks casual use of the desktop while preserving music, downloads, renders, browser work, and network activity. It is not the same as a kernel-enforced Windows secure desktop.

A technical local user may try to bypass soft-lock by killing the UI process. MajestyGuard mitigates this with an optional daemon watchdog (`MG_OVERLAY_WATCHDOG=1`) that restarts the overlay while in `SOFT_LOCK` or `SOCIAL_LOCK`.

This does not protect against an attacker who can kill both the daemon and overlay or use privileged Windows recovery tools. For that risk level, use the `HOSTILE_LOCK` path, which calls `LockWorkStation()`, or wait for the signed Credential Provider path.

## Protected Windows Key Sequences

User-space code cannot block Ctrl+Alt+Del. That is intentional Windows behavior. MajestyGuard should not try to bypass it.

## Lock-Screen Auto-Unlock

Automatic unlock from the Windows lock screen requires the future signed Credential Provider path. On this Smart App Control machine, the service/CP path remains deferred until trusted signing or a recovery-tested development environment is available.
