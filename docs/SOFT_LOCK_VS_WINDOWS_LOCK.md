# Soft Lock vs Windows Lock Screen vs Credential Provider

## Soft Lock (Current Implementation)

MajestyGuard's primary desktop lock mode is a fullscreen PyQt overlay. It blocks local interaction while background processes continue normally. The user presses Space, clicks the overlay, or taps the Dynamic Island to trigger face verification. If the owner passes, the overlay clears. If a stranger is detected, access stays blocked.

Background behavior during soft lock:

- Music/audio: continues
- Downloads: continues
- Renders/builds: continues
- Browser tasks: continues
- Network: continues

Only SocialLock may restrict selected apps, and only when a stranger is actively detected at the machine. Inactivity lock is a door lock, not a room freeze.

Current limitation: a technical local user may try to kill the overlay process. Mitigation: the daemon has a lightweight overlay watchdog path that can restart the UI while locked when `MG_OVERLAY_WATCHDOG=1`. This is bypass resistance, not kernel-grade security.

## Windows Lock Screen

`LockWorkStation()` is reserved for `HOSTILE_LOCK`: spoof/tamper evidence, emergency lock, or future extended no-owner timeout policy. It is not called from normal inactivity lock and not called directly from SocialLock by default.

Windows lock is the stronger fallback. It requires PIN/password/Windows Hello to return, and background apps continue as they normally do under Windows lock.

## Credential Provider

The Credential Provider path is the future signed Windows lock-screen integration. Once trusted Authenticode signing is available, it can provide a lock-screen tile and eventual auto-unlock behavior through the Windows logon stack.

On the current Smart App Control machine, self-signed or unsigned service/CP binaries are not a reliable production route. Keep this path deferred until trusted signing, a VM, or a recovery-tested dev machine is available.
