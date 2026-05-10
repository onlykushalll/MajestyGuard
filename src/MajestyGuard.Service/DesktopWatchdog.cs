// MajestyGuard.Service/DesktopWatchdog.cs
// Detects and neutralises the CreateDesktop / SetThreadDesktop bypass.
//
// ATTACK BEING MITIGATED:
//   An attacker calls CreateDesktop("AttackDesk") then SetThreadDesktop
//   to switch context to it. The overlay stays on the original desktop —
//   it becomes invisible. The attacker gets a blank desktop with full
//   access to launch programs. Zero privilege required.
//
// DETECTION:
//   1. EnumDesktops() — counts desktops on the window station.
//      We expect exactly 1 ("Default") plus optionally "MajestyGuard".
//      Any third desktop = attack detected.
//   2. SetWinEventHook(EVENT_SYSTEM_DESKTOPSWITCH) — fires the moment
//      the active input desktop changes. Faster than polling.
//   3. OpenInputDesktop() — verifies which desktop has keyboard focus.
//      If it's not ours, force switch back.
//
// RESPONSE:
//   - Immediately call SwitchDesktop(hDefaultDesktop) to pull the
//     user back to our protected desktop.
//   - Transition state machine to HostileLock.
//   - Log the attack event.

using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using MajestyGuard.Core;

namespace MajestyGuard.Service
{
    public sealed class DesktopWatchdog : IDisposable
    {
        private readonly ILogger<DesktopWatchdog> _logger;
        private readonly StateMachine _stateMachine;

        // Expected desktop names (lowercase for comparison)
        private static readonly string[] _allowedDesktops = ["default", "majestyguard"];

        // WinEvent hook handle
        private nint _winEventHook;
        private WinEventDelegate? _hookDelegate; // keep alive

        // ── P/Invoke ──────────────────────────────────────────────────

        [DllImport("user32.dll")] private static extern bool   EnumDesktops(nint hwinsta, EnumDesktopDelegate lpEnumFunc, nint lParam);
        [DllImport("user32.dll")] private static extern nint   OpenInputDesktop(uint dwFlags, bool fInherit, uint dwDesiredAccess);
        [DllImport("user32.dll")] private static extern nint   GetProcessWindowStation();
        [DllImport("user32.dll")] private static extern bool   SwitchDesktop(nint hDesktop);
        [DllImport("user32.dll")] private static extern bool   CloseDesktop(nint hDesktop);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern bool GetUserObjectInformation(nint hObj, int nIndex, nint pvInfo, uint nLength, out uint lpnLengthNeeded);
        [DllImport("user32.dll")] private static extern nint   OpenDesktop(string lpszDesktop, uint dwFlags, bool fInherit, uint dwDesiredAccess);
        [DllImport("user32.dll")] private static extern nint   SetWinEventHook(uint eventMin, uint eventMax, nint hmodWinEventProc, WinEventDelegate lpfnWinEventProc, uint idProcess, uint idThread, uint dwFlags);
        [DllImport("user32.dll")] private static extern bool   UnhookWinEvent(nint hWinEventHook);

        private delegate bool   EnumDesktopDelegate(string lpszDesktop, nint lParam);
        private delegate void   WinEventDelegate(nint hWinEventHook, uint eventType, nint hwnd, int idObject, int idChild, uint dwEventThread, uint dwmsEventTime);

        private const uint EVENT_SYSTEM_DESKTOPSWITCH = 0x0020;
        private const uint WINEVENT_OUTOFCONTEXT      = 0x0000;
        private const uint DESKTOP_READOBJECTS        = 0x0001;
        private const uint DESKTOP_SWITCHDESKTOP      = 0x0100;
        private const uint DESKTOP_ALL_ACCESS         = 0x01FF;

        public DesktopWatchdog(ILogger<DesktopWatchdog> logger, StateMachine stateMachine)
        {
            _logger       = logger;
            _stateMachine = stateMachine;
        }

        public async Task RunAsync(CancellationToken ct)
        {
            _logger.LogInformation("DesktopWatchdog started");

            // Install WinEvent hook for instant detection (< 1ms response)
            _hookDelegate = OnWinEvent;
            _winEventHook = SetWinEventHook(
                EVENT_SYSTEM_DESKTOPSWITCH, EVENT_SYSTEM_DESKTOPSWITCH,
                0, _hookDelegate, 0, 0, WINEVENT_OUTOFCONTEXT);

            if (_winEventHook == 0)
                _logger.LogWarning("SetWinEventHook failed — falling back to polling only");

            // Polling loop as backup (every 2 seconds)
            while (!ct.IsCancellationRequested)
            {
                try { CheckDesktops(); }
                catch (Exception ex) { _logger.LogError(ex, "Desktop check error"); }

                await Task.Delay(2000, ct);
            }
        }

        private void OnWinEvent(nint hook, uint eventType, nint hwnd,
            int idObject, int idChild, uint thread, uint time)
        {
            // Desktop switch detected — check immediately
            _logger.LogDebug("WinEvent: desktop switch detected");
            CheckDesktops();
        }

        private void CheckDesktops()
        {
            // Only act during a lock state
            var state = _stateMachine.Current;
            if (state != GuardState.InactivityLock &&
                state != GuardState.HostileLock    &&
                state != GuardState.Verifying      &&
                state != GuardState.BootScan)
                return;

            // Enumerate all desktops on this window station
            var hWinSta = GetProcessWindowStation();
            var desktops = new System.Collections.Generic.List<string>();

            EnumDesktops(hWinSta, (name, _) =>
            {
                desktops.Add(name.ToLowerInvariant());
                return true;
            }, 0);

            // Any desktop that isn't in our allowlist = attack
            foreach (var desk in desktops)
            {
                if (!Array.Exists(_allowedDesktops, d => d == desk))
                {
                    _logger.LogWarning(
                        "SECURITY: Unauthorized desktop '{Desk}' detected — forcing switch back",
                        desk);
                    ForceReturnToDefaultDesktop();
                    _stateMachine.RequestTransition(TransitionTrigger.CameraObstructed); // → HostileLock
                    return;
                }
            }

            // Also verify input focus is on our desktop
            var hInputDesk = OpenInputDesktop(0, false, DESKTOP_READOBJECTS);
            if (hInputDesk == 0) return;

            try
            {
                var namePtr = Marshal.AllocHGlobal(256);
                try
                {
                    GetUserObjectInformation(hInputDesk, 2, namePtr, 256, out _);
                    var inputDeskName = Marshal.PtrToStringUni(namePtr)?.ToLowerInvariant() ?? "";

                    if (!Array.Exists(_allowedDesktops, d => d == inputDeskName))
                    {
                        _logger.LogWarning(
                            "SECURITY: Input focus on unauthorized desktop '{Desk}'", inputDeskName);
                        ForceReturnToDefaultDesktop();
                        _stateMachine.RequestTransition(TransitionTrigger.CameraObstructed);
                    }
                }
                finally { Marshal.FreeHGlobal(namePtr); }
            }
            finally { CloseDesktop(hInputDesk); }
        }

        private void ForceReturnToDefaultDesktop()
        {
            var hDefault = OpenDesktop("Default", 0, false, DESKTOP_SWITCHDESKTOP);
            if (hDefault != 0)
            {
                SwitchDesktop(hDefault);
                CloseDesktop(hDefault);
                _logger.LogInformation("Forced switch back to Default desktop");
            }
        }

        public void Dispose()
        {
            if (_winEventHook != 0)
            {
                UnhookWinEvent(_winEventHook);
                _winEventHook = 0;
            }
        }
    }
}
