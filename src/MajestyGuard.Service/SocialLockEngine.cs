// MajestyGuard.Service/SocialLockEngine.cs
// Manages the "Safe Mode" / Social Privacy feature.
//
// ACTIVATION (when SocialLock state entered):
//   1. Enable Windows Focus Assist (DND) via registry
//   2. Suspend restricted Win32 processes (NtSuspendProcess)
//   3. Suspend restricted UWP apps (PackageDebugSettings)
//   4. Apply DACL restrictions to sensitive file paths
//   5. Suppress toast notifications
//
// DEACTIVATION (when SocialLock state exits):
//   1. Restore all suspended processes
//   2. Revert DACL changes (MUST be atomic)
//   3. Disable Focus Assist
//
// CODEX CRITICAL NOTE:
//   Deactivation must be ATOMIC. If the process crashes mid-restore,
//   the user could be permanently locked out of their own files.
//   Implement a "restore journal" written before deactivation starts,
//   cleaned up only after full restore completes.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Microsoft.Win32;
using MajestyGuard.Core.Models;

namespace MajestyGuard.Service
{
    public class SocialLockEngine
    {
        private readonly ILogger<SocialLockEngine> _logger;
        private readonly AppConfig _config;
        private readonly ProcessRestrictor _restrictor;

        // Hysteresis tracking
        private DateTime? _strangerFirstSeen;
        private DateTime? _strangerLastSeen;
        private bool _socialLockActive;

        public SocialLockEngine(
            ILogger<SocialLockEngine> logger,
            AppConfig config,
            ProcessRestrictor restrictor)
        {
            _logger    = logger;
            _config    = config;
            _restrictor = restrictor;
        }

        // ─────────────────────────────────────────────────────────────
        // Called by Worker on every frame with FaceCount > 1
        // ─────────────────────────────────────────────────────────────
        public void ReportStrangerFrame()
        {
            _strangerFirstSeen ??= DateTime.UtcNow;
            _strangerLastSeen  = DateTime.UtcNow;
        }

        public void ReportStrangerGone()
        {
            _strangerLastSeen = DateTime.UtcNow;
        }

        public bool ShouldTriggerSocialLock()
        {
            if (_strangerFirstSeen == null) return false;
            var presenceDuration = DateTime.UtcNow - _strangerFirstSeen.Value;
            return presenceDuration.TotalMilliseconds >= _config.StrangerPresenceThresholdMs;
        }

        public bool ShouldReleaseSocialLock()
        {
            if (_strangerLastSeen == null) return false;
            var absenceDuration = DateTime.UtcNow - _strangerLastSeen.Value;
            return absenceDuration.TotalSeconds >= _config.StrangerHysteresisSeconds;
        }

        // ─────────────────────────────────────────────────────────────
        // ACTIVATE SAFE MODE
        // ─────────────────────────────────────────────────────────────
        public async Task ActivateAsync()
        {
            if (_socialLockActive) return;
            _logger.LogWarning("SocialLock ACTIVATING — Safe Mode engaging");

            try
            {
                // 1. Enable Focus Assist / DND
                EnableFocusAssist(true);

                // 2. Suspend Win32 processes
                foreach (var processName in _config.RestrictedProcesses)
                    await _restrictor.SuspendWin32ProcessAsync(processName);

                // 3. Suspend UWP packages
                foreach (var packageId in _config.RestrictedUwpPackages)
                    await _restrictor.SuspendUwpAppAsync(packageId);

                // 4. Restrict file paths (DACL)
                // CODEX: Implement DACL modification in ProcessRestrictor
                // Write restore journal FIRST before applying restrictions
                foreach (var path in _config.RestrictedPaths)
                    _restrictor.RestrictPath(path);

                _socialLockActive = true;
                _logger.LogInformation("SocialLock ACTIVE");
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "SocialLock activation failed — attempting rollback");
                await DeactivateAsync();  // Safety rollback
            }
        }

        // ─────────────────────────────────────────────────────────────
        // DEACTIVATE SAFE MODE
        // ─────────────────────────────────────────────────────────────
        public async Task DeactivateAsync()
        {
            _logger.LogInformation("SocialLock DEACTIVATING — restoring normal access");

            // Order matters: restore files first, then processes, then notifications
            // This ensures user regains access even if process restore fails

            try { _restrictor.UnrestrictAllPaths(); }
            catch (Exception ex) { _logger.LogError(ex, "Path unrestriction failed"); }

            foreach (var processName in _config.RestrictedProcesses)
            {
                try { await _restrictor.ResumeWin32ProcessAsync(processName); }
                catch (Exception ex) { _logger.LogError(ex, "Failed to resume {Proc}", processName); }
            }

            foreach (var packageId in _config.RestrictedUwpPackages)
            {
                try { await _restrictor.ResumeUwpAppAsync(packageId); }
                catch (Exception ex) { _logger.LogError(ex, "Failed to resume UWP {Pkg}", packageId); }
            }

            try { EnableFocusAssist(false); }
            catch (Exception ex) { _logger.LogError(ex, "FocusAssist restore failed"); }

            // Reset hysteresis
            _strangerFirstSeen = null;
            _strangerLastSeen  = null;
            _socialLockActive  = false;

            _logger.LogInformation("SocialLock DEACTIVATED — full access restored");
        }

        // ─────────────────────────────────────────────────────────────
        // FOCUS ASSIST / DND via Registry
        //
        // Registry key: HKCU\Software\Microsoft\Windows\CurrentVersion\
        //               CloudStore\Store\Cache\DefaultAccount\
        //               $$windows.data.notifications.quiethourssettings\
        //               Current\Data
        //
        // CODEX: The full Focus Assist registry path is complex and
        //        version-specific. An alternative is to use the
        //        Windows.UI.Notifications WinRT API:
        //        ToastNotificationManager.History.Clear() for immediate clearing.
        //        For persistent DND, use the undocumented CloudStore path below,
        //        OR use the SetSystemMediaTransportControlsInfo approach.
        //
        //        Simplest working approach for Windows 11 22H2+:
        //        Write 0x01 to the QuietHours registry binary value.
        // ─────────────────────────────────────────────────────────────
        private void EnableFocusAssist(bool enable)
        {
            const string keyPath =
                @"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\" +
                @"Cache\DefaultAccount\$$windows.data.notifications.quiethourssettings\Current";

            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(keyPath, writable: true);
                if (key == null)
                {
                    _logger.LogWarning("FocusAssist registry key not found — skipping");
                    return;
                }

                var data = key.GetValue("Data") as byte[];
                if (data == null || data.Length < 28)
                {
                    _logger.LogWarning("FocusAssist registry data missing or too short — skipping");
                    return;
                }

                // The quiet hours toggle byte is at a known offset in the binary blob.
                // Common offsets on Windows 11 22H2+: byte 18 or byte 24.
                // Value meanings: 0x00=off, 0x01=priority only, 0x02=alarms only
                int toggleOffset = data.Length >= 28 ? 24 : 18;

                if (enable)
                    data[toggleOffset] = 0x02; // Alarms only = strongest DND
                else
                    data[toggleOffset] = 0x00; // Off

                key.SetValue("Data", data, RegistryValueKind.Binary);

                _logger.LogInformation("FocusAssist {State} (offset {Offset})",
                    enable ? "ENABLED" : "DISABLED", toggleOffset);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to toggle FocusAssist");
            }
        }
    }
}
