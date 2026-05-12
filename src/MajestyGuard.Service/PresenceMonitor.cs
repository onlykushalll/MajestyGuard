// MajestyGuard.Service/PresenceMonitor.cs
// Manages the continuous face detection loop.
// Bridges the CV Engine results into state machine triggers.
//
// FPS STRATEGY:
//   DORMANT state         → Camera OFF (zero CPU)
//   UNLOCKED (idle)       → 1 FPS  (minimal footprint)
//   UNLOCKED (30s warning)→ 5 FPS  (escalating)
//   VERIFYING / BOOT_SCAN → 15 FPS (active recognition)
//   INACTIVITY/HOSTILE    → 10 FPS (waiting for face)
//
// CODEX: The FPS command is sent to CVEngine via pipe.
//        Wire the state machine StateChanged event to UpdateFps().

using System;
using System.Threading;
using System.Threading.Tasks;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using MajestyGuard.Core;
using MajestyGuard.Core.IPC;
using MajestyGuard.Core.Models;

namespace MajestyGuard.Service
{
    public class PresenceMonitor
    {
        private readonly ILogger<PresenceMonitor> _logger;
        private readonly StateMachine _stateMachine;
        private readonly AppConfig _config;
        private readonly SocialLockEngine _socialLock;

        // Reference to CV pipe server (set by Worker after pipe is ready)
        public MajestyPipeServer? CvPipeServer { get; set; }

        // Stranger-gone tracking (separate from SocialLockEngine internal state)
        private DateTime? _lastStrangerSeen;
        private bool _strangerTracking;
        private int _lastRequestedFps = -1;

        public PresenceMonitor(
            ILogger<PresenceMonitor> logger,
            StateMachine stateMachine,
            AppConfig config,
            SocialLockEngine socialLock)
        {
            _logger       = logger;
            _stateMachine = stateMachine;
            _config       = config;
            _socialLock   = socialLock;

            // React to state changes to update CV FPS
            _stateMachine.StateChanged += OnStateChanged;
        }

        public async Task RunAsync(CancellationToken ct)
        {
            _logger.LogInformation("PresenceMonitor started");

            // Tell CV engine to start at idle FPS
            await SetCvFpsAsync(_config.MonitoringFps);

            // The actual detection results come in via OnCvMessageAsync in Worker.
            // This loop handles periodic state-driven logic (stranger timeout etc.)
            while (!ct.IsCancellationRequested)
            {
                try
                {
                    await TickAsync();
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "PresenceMonitor tick error");
                }

                await Task.Delay(500, ct);  // Check every 500ms
            }
        }

        private async Task TickAsync()
        {
            var state = _stateMachine.Current;

            // ── Stranger-gone hysteresis check ─────────────────────────
            if (state == GuardState.SocialLock && _lastStrangerSeen.HasValue)
            {
                var absenceDuration = DateTime.UtcNow - _lastStrangerSeen.Value;
                if (absenceDuration.TotalSeconds >= _config.StrangerHysteresisSeconds)
                {
                    _logger.LogInformation(
                        "Stranger absent for {Sec:F1}s — triggering StrangerLeft",
                        absenceDuration.TotalSeconds);
                    _stateMachine.RequestTransition(TransitionTrigger.StrangerLeft);
                    _lastStrangerSeen = null;
                    _strangerTracking = false;
                }
            }
            if (state == GuardState.Unlocked)
            {
                var idleMs = InactivityWatcher.GetIdleTimeMs();
                var timeoutMs = (ulong)_config.InactivityTimeoutSeconds * 1000UL;
                if (timeoutMs > 25_000UL && idleMs > timeoutMs - 25_000UL)
                    await SetCvFpsAsync(_config.EscalatedFps);
                else if (timeoutMs <= 30_000UL || idleMs < timeoutMs - 30_000UL)
                    await SetCvFpsAsync(_config.MonitoringFps);
            }
        }

        /// <summary>Called by Worker when a DetectionResultMsg arrives with FaceCount ≤ 1
        /// while in SocialLock. Marks the moment stranger disappeared.</summary>
        public void ReportStrangerMaybeGone()
        {
            if (!_strangerTracking)
            {
                _lastStrangerSeen = DateTime.UtcNow;
                _strangerTracking = true;
            }
        }

        public void ReportStrangerStillPresent()
        {
            _lastStrangerSeen = null;
            _strangerTracking = false;
        }

        // ─────────────────────────────────────────────────────────────
        // FPS MANAGEMENT
        // ─────────────────────────────────────────────────────────────

        private void OnStateChanged(object? sender, StateChangedEventArgs e)
        {
            var targetFps = e.Current switch
            {
                GuardState.Dormant        => 0,
                GuardState.Unlocked       => _config.MonitoringFps,      // 1 FPS idle
                GuardState.BootScan       => _config.VerificationFps,    // 15 FPS
                GuardState.Verifying      => _config.VerificationFps,    // 15 FPS
                GuardState.InactivityLock => _config.EscalatedFps,       // 10 FPS
                GuardState.HostileLock    => _config.EscalatedFps,       // 10 FPS
                GuardState.SocialLock     => _config.EscalatedFps,       // 10 FPS (watching for stranger departure)
                _                         => _config.MonitoringFps,
            };

            _ = SetCvFpsAsync(targetFps);
        }

        public async Task SetCvFpsAsync(int fps)
        {
            if (CvPipeServer == null) return;

            var target = Math.Clamp(fps, 1, 30);
            if (target == _lastRequestedFps) return;

            var cmd = JsonSerializer.Serialize(new { cmd = "set_fps", fps = target });
            await CvPipeServer.SendRawAsync(cmd);
            _lastRequestedFps = target;
            _logger.LogDebug("CV FPS -> {Fps}", target);
        }

        public async Task SetCvDetSizeAsync(int width, int height)
        {
            if (CvPipeServer == null) return;
            var cmd = JsonSerializer.Serialize(new { cmd = "set_det_size", w = width, h = height });
            await CvPipeServer.SendRawAsync(cmd);
            _logger.LogDebug("CV detector size -> {Width}x{Height}", width, height);
        }
    }
}
