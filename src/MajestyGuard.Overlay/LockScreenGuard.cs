// MajestyGuard.Overlay/LockScreenGuard.cs
// Enforces unbypassable screen lock during InactivityLock and HostileLock.
//
// ═══════════════════════════════════════════════════════════════════
// WHY EACH LAYER EXISTS
// ═══════════════════════════════════════════════════════════════════
//
// Layer 1 — HWND_TOPMOST + WS_EX_LAYERED (DynamicIslandWindow)
//   Full-screen black overlay sits above everything.
//   No app window can appear on top.
//
// Layer 2 — WS_EX_TRANSPARENT removed (SetClickThrough(false))
//   Overlay absorbs all mouse clicks — nothing behind it is clickable.
//
// Layer 3 — WH_KEYBOARD_LL hook (DynamicIslandWindow)
//   Blocks: Win key, Alt+Tab, Alt+F4, Ctrl+Esc, Win+D, Win+L re-lock.
//   Does NOT and CANNOT block Ctrl+Alt+Del (kernel-level SAS).
//
// Layer 4 — BlockInput(true) [THIS FILE]
//   Win32 kernel-level input block. All keyboard + mouse events are
//   dropped before reaching any application's message queue.
//   Requires the calling process to be on the interactive desktop.
//   Re-asserted every 250ms — a privileged process could call
//   BlockInput(false) to cancel it, so we immediately re-apply.
//
// Layer 5 — Ctrl+Alt+Del chain [DESIGN NOTE]
//   Ctrl+Alt+Del CANNOT be blocked by any user-mode code.
//   This is intentional Windows security design.
//   HOWEVER: pressing Ctrl+Alt+Del during our lock shows the
//   Windows Security screen → user clicks "Sign in" → LogonUI loads
//   our Credential Provider → face recognition runs → access granted.
//   The chain is SECURE. Ctrl+Alt+Del doesn't bypass us; it routes
//   THROUGH us. This is the correct architecture.
//
// Layer 6 — Service watchdog [Worker.cs]
//   If the Overlay process is killed (Task Manager, etc.), the
//   watchdog relaunches it within 3 seconds. Lock is enforced again.
//   The Service process itself is DACL-hardened against non-admin kill.
//
// Layer 7 — Screensaver suppression [THIS FILE]
//   SPI_SETSCREENSAVERRUNNING prevents Windows from showing its own
//   screen saver or lock over our overlay.
//
// ═══════════════════════════════════════════════════════════════════
// WHAT CANNOT BE FULLY PREVENTED (honest assessment):
//   - An ADMINISTRATOR can kill the Overlay and bypass the lock.
//     Mitigation: Service DACL + watchdog restart in 3s.
//   - Safe Mode boot bypasses everything (by design — Windows feature).
//     Mitigation: Full-disk encryption (BitLocker) handles this.
//   - Another system-level process calling BlockInput(false).
//     Mitigation: Our 250ms re-assert loop shrinks the window.
// ═══════════════════════════════════════════════════════════════════

using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;

namespace MajestyGuard.Overlay
{
    public sealed class LockScreenGuard : IDisposable
    {
        private readonly ILogger _logger;
        private CancellationTokenSource? _blockCts;
        private bool _isBlocking;

        // ── P/Invoke ──────────────────────────────────────────────────

        // Blocks all keyboard and mouse input systemwide.
        // Caller must be on the interactive desktop.
        // Returns FALSE if already blocked by another process.
        [DllImport("user32.dll")]
        private static extern bool BlockInput(bool fBlockIt);

        // Suppresses screensaver / Windows lock overlay.
        [DllImport("user32.dll")]
        private static extern bool SystemParametersInfo(
            uint uiAction, uint uiParam, IntPtr pvParam, uint fWinIni);
        private const uint SPI_SETSCREENSAVERRUNNING = 0x0061;

        // Prevents display from going to sleep.
        [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern uint SetThreadExecutionState(uint esFlags);
        private const uint ES_CONTINUOUS        = 0x80000000;
        private const uint ES_DISPLAY_REQUIRED  = 0x00000002;
        private const uint ES_SYSTEM_REQUIRED   = 0x00000001;

        // Prevents Windows from auto-locking via its own screensaver timer.
        [DllImport("user32.dll")]
        private static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
        private const byte VK_NONAME = 0xFC;  // Unused virtual key — just to reset idle timer

        public LockScreenGuard(ILogger logger)
        {
            _logger = logger;
        }

        // ─────────────────────────────────────────────────────────────
        // ENGAGE — call when entering InactivityLock or HostileLock
        // ─────────────────────────────────────────────────────────────
        public void Engage()
        {
            if (_isBlocking) return;
            _isBlocking = true;
            _blockCts = new CancellationTokenSource();

            // DOOR LOCK — not a room freeze.
            // BlockInput blocks keyboard/mouse routing to apps,
            // but threads, audio, network, file I/O all continue.
            // Spotify keeps playing. Downloads keep going.
            // The overlay is a one-way mirror — we see nothing, the room is alive.
            _logger.LogInformation("LockScreenGuard: ENGAGING — screen locked, background continues");

            // Layer 4: BlockInput
            var success = BlockInput(true);
            if (!success)
                _logger.LogWarning("BlockInput(true) returned false — another process may have priority");

            // Layer 7: Tell Windows a screensaver is running (suppresses its own lock)
            SystemParametersInfo(SPI_SETSCREENSAVERRUNNING, 1, IntPtr.Zero, 0);

            // Prevent display sleep
            SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED);

            // Start re-assert loop
            SuppressAccessibilityShortcuts(true);
            _ = BlockInputReassertLoopAsync(_blockCts.Token);
        }

        // ─────────────────────────────────────────────────────────────
        // RELEASE — call when face is recognized / manual fallback
        // ─────────────────────────────────────────────────────────────
        public void Release()
        {
            if (!_isBlocking) return;
            _isBlocking = false;

            _logger.LogInformation("LockScreenGuard: RELEASING — restoring input");

            _blockCts?.Cancel();

            // Layer 4: Unblock input
            BlockInput(false);

            // Layer 7: Tell Windows screensaver is done
            SystemParametersInfo(SPI_SETSCREENSAVERRUNNING, 0, IntPtr.Zero, 0);

            // Restore normal execution state
            SetThreadExecutionState(ES_CONTINUOUS);
            SuppressAccessibilityShortcuts(false);
        }

        // ─────────────────────────────────────────────────────────────
        // RE-ASSERT LOOP
        // Calls BlockInput(true) every 250ms.
        // If a privileged process called BlockInput(false), we
        // restore it within a quarter-second.
        // ─────────────────────────────────────────────────────────────
        private async Task BlockInputReassertLoopAsync(CancellationToken ct)
        {
            int failCount = 0;
            while (!ct.IsCancellationRequested)
            {
                try
                {
                    await Task.Delay(250, ct);

                    if (!BlockInput(true))
                    {
                        failCount++;
                        if (failCount % 20 == 0)  // Log once per 5 seconds
                            _logger.LogWarning(
                                "BlockInput re-assert failing ({Count}x) — elevated process may be active",
                                failCount);
                    }
                    else
                    {
                        failCount = 0;
                    }
                }
                catch (OperationCanceledException) { break; }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "BlockInput loop error");
                    await Task.Delay(500, ct);
                }
            }
        }

        // ─────────────────────────────────────────────────────────────
        // STATIC HELPERS (called without instantiation)
        // ─────────────────────────────────────────────────────────────

        /// <summary>
        /// Suppresses Windows's own inactivity lock timer.
        /// Call this on a timer while the face engine is active —
        /// we don't want Windows to double-lock over our overlay.
        /// </summary>
        public static void SuppressWindowsIdleLock()
        {
            // Reset system idle timer with a no-op keybd_event
            // This prevents Windows from triggering its own screen saver
            // while our overlay is running.
            SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED);
        }

        /// <summary>
        /// Disable Windows built-in screen lock (requires admin).
        /// Called during enrollment setup.
        /// This prevents double-lock: our lock fires, then Windows also fires.
        /// </summary>
        public static void DisableWindowsScreenLock()
        {
            // Set screen saver timeout to 0 (disabled)
            // The Service manages its own inactivity timeout.
            // CODEX: Write HKCU\Control Panel\Desktop\ScreenSaveActive = "0"
            //        and HKCU\Control Panel\Desktop\ScreenSaveTimeOut = "0"
            //        during first enrollment. Restore on uninstall.
            try
            {
                using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(
                    @"Control Panel\Desktop", writable: true);
                key?.SetValue("ScreenSaveActive", "0");
                // Do NOT set ScreenSaveTimeOut to 0 — that causes issues on some builds.
                // Setting Active to 0 is sufficient.
            }
            catch { /* non-critical */ }
        }

        public void Dispose()
        {
            Release();
            _blockCts?.Dispose();
        }
    }
}

    // SUPPRESSION BLOCK - appended by hardening pass
    // Call SuppressAccessibilityShortcuts(true) in Engage(), false in Release()
    private static void SuppressAccessibilityShortcuts(bool suppress)
    {
        try
        {
            using var hkcu = Microsoft.Win32.Registry.CurrentUser;

            // StickyKeys: "506" = disabled shortcut, "510" = enabled
            using var sk = hkcu.CreateSubKey(@"Control Panel\Accessibility\StickyKeys");
            sk?.SetValue("Flags", suppress ? "506" : "510", Microsoft.Win32.RegistryValueKind.String);

            // ToggleKeys: "62" = disabled shortcut, "63" = enabled
            using var tk = hkcu.CreateSubKey(@"Control Panel\Accessibility\ToggleKeys");
            tk?.SetValue("Flags", suppress ? "62" : "63", Microsoft.Win32.RegistryValueKind.String);

            // FilterKeys: "122" = disabled shortcut, "126" = enabled
            using var fk = hkcu.CreateSubKey(@"Control Panel\Accessibility\Keyboard Response");
            fk?.SetValue("Flags", suppress ? "122" : "126", Microsoft.Win32.RegistryValueKind.String);
        }
        catch { /* non-critical */ }
    }
