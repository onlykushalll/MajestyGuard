// MajestyGuard.Service/InactivityWatcher.cs
// Polls GetLastInputInfo to detect user inactivity.
// Fires InactivityThresholdHit trigger when no keyboard/mouse
// input has been detected for config.InactivityTimeoutSeconds.
//
// CODEX NOTES:
//   - GetLastInputInfo returns a tick count (NOT a DateTime).
//     Use GetTickCount64() for delta. Do NOT use DateTime.Now.
//   - This runs on the interactive desktop session.
//     When running as SYSTEM, we must query the ACTIVE session.
//     CODEX: Implement cross-session input detection if needed.
//   - Do NOT trigger InactivityLock if a fullscreen game/video is
//     running (check foreground window + DirectX exclusive mode).

using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using MajestyGuard.Core;
using MajestyGuard.Core.Models;

namespace MajestyGuard.Service
{
    public class InactivityWatcher
    {
        private readonly ILogger<InactivityWatcher> _logger;
        private readonly StateMachine _stateMachine;
        private readonly AppConfig _config;

        // P/Invoke declarations
        [DllImport("user32.dll")]
        private static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);

        [DllImport("kernel32.dll")]
        private static extern ulong GetTickCount64();

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

        [DllImport("user32.dll")]
        private static extern nint GetWindowLongPtr(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll")]
        private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint dwFlags);

        [DllImport("user32.dll", CharSet = CharSet.Auto)]
        private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFO lpmi);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [StructLayout(LayoutKind.Sequential)]
        private struct LASTINPUTINFO
        {
            public uint cbSize;
            public uint dwTime;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT
        {
            public int Left, Top, Right, Bottom;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
        private struct MONITORINFO
        {
            public int cbSize;
            public RECT rcMonitor;
            public RECT rcWork;
            public uint dwFlags;
        }

        private const int GWL_STYLE = -16;
        private static readonly nint WS_POPUP = unchecked((nint)0x80000000L);
        private const uint MONITOR_DEFAULTTOPRIMARY = 1;

        // Track whether we've already fired the trigger to avoid spam
        private bool _lockTriggered;

        public InactivityWatcher(
            ILogger<InactivityWatcher> logger,
            StateMachine stateMachine,
            AppConfig config)
        {
            _logger       = logger;
            _stateMachine = stateMachine;
            _config       = config;
        }

        public async Task RunAsync(CancellationToken ct)
        {
            _logger.LogInformation(
                "InactivityWatcher started. Timeout: {Sec}s, Poll: {Poll}ms",
                _config.InactivityTimeoutSeconds, _config.InactivityPollIntervalMs);

            while (!ct.IsCancellationRequested)
            {
                try
                {
                    var idleMs = GetIdleTimeMs();

                    if (IsInLockableState())
                    {
                        var timeoutMs = (ulong)_config.InactivityTimeoutSeconds * 1000UL;

                        if (!_lockTriggered && idleMs >= timeoutMs && !IsFullscreenAppActive())
                        {
                            _logger.LogInformation(
                                "Inactivity threshold hit ({IdleMs}ms >= {TimeoutMs}ms)",
                                idleMs, timeoutMs);

                            _stateMachine.RequestTransition(
                                TransitionTrigger.InactivityThresholdHit);
                            _lockTriggered = true;
                        }
                        else if (_lockTriggered && idleMs < 1000)
                        {
                            // User moved mouse or pressed key — demand re-verification
                            _logger.LogDebug("User input detected after inactivity lock");
                            _stateMachine.RequestTransition(
                                TransitionTrigger.UserInputDetected);
                            _lockTriggered = false;
                        }

                        // Log approaching threshold as warning
                        if (!_lockTriggered && idleMs >= timeoutMs - 20_000UL)
                        {
                            _logger.LogDebug(
                                "Approaching inactivity lock: {IdleSec}s idle",
                                idleMs / 1000);
                        }
                    }
                    else
                    {
                        // Not in an unlocked state — reset lock flag
                        _lockTriggered = false;
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "InactivityWatcher loop error");
                }

                await Task.Delay(_config.InactivityPollIntervalMs, ct);
            }
        }

        // ─────────────────────────────────────────────────────────────
        // Returns milliseconds since last keyboard or mouse input
        // ─────────────────────────────────────────────────────────────
        public static ulong GetIdleTimeMs()
        {
            var info = new LASTINPUTINFO { cbSize = (uint)Marshal.SizeOf<LASTINPUTINFO>() };

            if (!GetLastInputInfo(ref info))
                return 0;

            // GetTickCount64() is the running system clock in ms.
            // info.dwTime is a 32-bit tick count — it wraps every ~49.7 days.
            // We cast to ulong and handle potential wrap-around.
            var now      = GetTickCount64();
            var lastTick = (ulong)info.dwTime;

            // Handle 32-bit wrap-around (very rare but must be correct)
            if (now < lastTick)
                now += 0x100000000UL;

            return now - lastTick;
        }

        // ─────────────────────────────────────────────────────────────
        // Only monitor inactivity when in an appropriate state
        // ─────────────────────────────────────────────────────────────
        private bool IsInLockableState()
        {
            var state = _stateMachine.Current;
            return state == GuardState.Unlocked || state == GuardState.InactivityLock;
        }

        // ─────────────────────────────────────────────────────────────
        // FULLSCREEN DETECTION
        // CODEX: Implement this to avoid locking during games/videos.
        //
        // Strategy:
        //   1. GetForegroundWindow()
        //   2. GetWindowRect() — compare to monitor bounds
        //   3. Check if window has WS_POPUP style (typical for fullscreen)
        //   4. Optionally check for IDXGISwapChain in exclusive mode
        //      via DXGI factory enumeration (complex — optional for v1)
        // ─────────────────────────────────────────────────────────────
        private static bool IsFullscreenAppActive()
        {
            var hwnd = GetForegroundWindow();
            if (hwnd == IntPtr.Zero) return false;

            var style = GetWindowLongPtr(hwnd, GWL_STYLE);
            bool isPopup = (style & WS_POPUP) != 0;

            if (!GetWindowRect(hwnd, out RECT wndRect)) return false;

            var hMon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTOPRIMARY);
            var monInfo = new MONITORINFO { cbSize = Marshal.SizeOf<MONITORINFO>() };
            if (!GetMonitorInfo(hMon, ref monInfo)) return false;

            var mon = monInfo.rcMonitor;
            bool coversMonitor = wndRect.Left <= mon.Left && wndRect.Top <= mon.Top
                              && wndRect.Right >= mon.Right && wndRect.Bottom >= mon.Bottom;

            if (!coversMonitor || !isPopup) return false;

            // Exclude known system UI processes
            GetWindowThreadProcessId(hwnd, out uint pid);
            try
            {
                var proc = Process.GetProcessById((int)pid);
                var name = proc.ProcessName;
                if (name is "GameBar" or "XboxApp" or "ShellExperienceHost" or "SearchUI")
                    return false;
            }
            catch { /* Process already exited */ }

            return true;
        }
    }
}
