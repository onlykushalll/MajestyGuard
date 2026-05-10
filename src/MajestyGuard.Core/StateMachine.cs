// MajestyGuard.Core/StateMachine.cs
// THE HEART OF MAJESTY GUARD
// ALL state transitions flow through this class.
// No component should change state directly — always call RequestTransition().

using System;
using System.Threading;
using Microsoft.Extensions.Logging;

namespace MajestyGuard.Core
{
    public enum GuardState
    {
        /// <summary>Service running under a non-enrolled profile. Zero footprint.</summary>
        Dormant,

        /// <summary>Login screen active. Camera initializing. Scanning for face.</summary>
        BootScan,

        /// <summary>Face detected in frame. Running recognition + liveness check.</summary>
        Verifying,

        /// <summary>Primary user verified. Normal desktop. Background monitoring active.</summary>
        Unlocked,

        /// <summary>No peripheral input for N seconds. Screen lock overlay active.</summary>
        InactivityLock,

        /// <summary>Verified user + ≥1 unrecognized face. Safe Mode active.</summary>
        SocialLock,

        /// <summary>No recognized face, camera obscured, or auth failed 3x. Full opaque lock.</summary>
        HostileLock,
    }

    public enum TransitionTrigger
    {
        ProfileValidated,        // Enrolled SID matches current user → DORMANT → BOOT_SCAN
        LoginScreenDetected,     // Winlogon signaled → DORMANT → BOOT_SCAN
        FaceDetected,            // CV engine found a face in frame
        FaceRecognized,          // Recognition matched enrolled embedding
        FaceUnrecognized,        // Face present but not enrolled user
        LivenessCheckFailed,     // Anti-spoofing detected photo/video attack
        AuthFailedMaxRetries,    // 3 consecutive recognition failures
        NoFaceDetected,          // Frame has no faces
        InactivityThresholdHit,  // GetLastInputInfo delta exceeded config value
        StrangerDetected,        // Second face detected alongside primary user
        StrangerLeft,            // Stranger no longer in frame (+ hysteresis passed)
        UserInputDetected,       // Keyboard or mouse movement resumed
        CameraObstructed,        // Camera feed is black / virtual camera detected
        ManualFallback,          // User requested PIN/password manually
        EnrollmentRequired,      // No enrollment found for this profile
    }

    public class StateChangedEventArgs : EventArgs
    {
        public GuardState Previous { get; init; }
        public GuardState Current  { get; init; }
        public TransitionTrigger Trigger { get; init; }
        public DateTime Timestamp { get; init; } = DateTime.UtcNow;
    }

    public class StateMachine
    {
        private GuardState _current = GuardState.Dormant;
        private readonly object _lock = new();
        private readonly ILogger<StateMachine> _logger;

        // Stranger presence tracking for hysteresis
        private DateTime? _strangerFirstSeen;
        private DateTime? _strangerLastSeen;
        private int _authFailureCount;

        // Config values — injected from AppConfig
        private readonly TimeSpan _strangerHysteresis;   // default: 3 seconds
        private readonly int _maxAuthFailures;           // default: 3

        public event EventHandler<StateChangedEventArgs>? StateChanged;

        public GuardState Current
        {
            get { lock (_lock) return _current; }
        }

        public StateMachine(ILogger<StateMachine> logger, AppConfig config)
        {
            _logger = logger;
            _strangerHysteresis = TimeSpan.FromSeconds(config.StrangerHysteresisSeconds);
            _maxAuthFailures    = config.MaxAuthFailures;
        }

        /// <summary>
        /// The ONLY way to change state. All components call this.
        /// Returns true if transition was accepted, false if invalid from current state.
        /// </summary>
        public bool RequestTransition(TransitionTrigger trigger, object? context = null)
        {
            lock (_lock)
            {
                var previous = _current;
                var next = ResolveNextState(trigger, context);

                if (next == null)
                {
                    _logger.LogDebug(
                        "Trigger {Trigger} ignored in state {State}", trigger, _current);
                    return false;
                }

                _current = next.Value;
                _logger.LogInformation(
                    "State: {Prev} → {Next} (trigger: {Trigger})", previous, _current, trigger);

                OnStateChanged(previous, _current, trigger);
                return true;
            }
        }

        // ─────────────────────────────────────────────────────────────
        // TRANSITION TABLE
        // Returns the next state or null if the trigger is invalid here.
        // CODEX: Implement the guard conditions marked TODO.
        // ─────────────────────────────────────────────────────────────
        private GuardState? ResolveNextState(TransitionTrigger trigger, object? context)
        {
            return (_current, trigger) switch
            {
                // ── FROM DORMANT ──────────────────────────────────────────────
                (GuardState.Dormant, TransitionTrigger.ProfileValidated)
                    => GuardState.BootScan,

                (GuardState.Dormant, TransitionTrigger.EnrollmentRequired)
                    => GuardState.Dormant,  // Stay dormant; show enrollment prompt separately

                // ── FROM BOOT_SCAN ────────────────────────────────────────────
                (GuardState.BootScan, TransitionTrigger.FaceDetected)
                    => GuardState.Verifying,

                (GuardState.BootScan, TransitionTrigger.ManualFallback)
                    => GuardState.Unlocked,  // User chose password; bypass face

                (GuardState.BootScan, TransitionTrigger.CameraObstructed)
                    => GuardState.HostileLock,

                // ── FROM VERIFYING ────────────────────────────────────────────
                (GuardState.Verifying, TransitionTrigger.FaceRecognized)
                    => HandleAuthSuccess(),

                (GuardState.Verifying, TransitionTrigger.FaceUnrecognized)
                    => HandleAuthFailure(),

                (GuardState.Verifying, TransitionTrigger.LivenessCheckFailed)
                    => GuardState.HostileLock,

                (GuardState.Verifying, TransitionTrigger.NoFaceDetected)
                    => GuardState.BootScan,

                (GuardState.Verifying, TransitionTrigger.CameraObstructed)
                    => GuardState.HostileLock,

                // ── FROM UNLOCKED ─────────────────────────────────────────────
                (GuardState.Unlocked, TransitionTrigger.StrangerDetected)
                    => HandleStrangerDetected(),

                (GuardState.Unlocked, TransitionTrigger.InactivityThresholdHit)
                    => GuardState.InactivityLock,

                (GuardState.Unlocked, TransitionTrigger.NoFaceDetected)
                    => GuardState.InactivityLock,  // User walked away

                (GuardState.Unlocked, TransitionTrigger.CameraObstructed)
                    => GuardState.HostileLock,

                (GuardState.Unlocked, TransitionTrigger.LoginScreenDetected)
                    => GuardState.BootScan,  // Win+L pressed

                // ── FROM INACTIVITY_LOCK ──────────────────────────────────────
                (GuardState.InactivityLock, TransitionTrigger.FaceDetected)
                    => GuardState.Verifying,

                (GuardState.InactivityLock, TransitionTrigger.UserInputDetected)
                    => GuardState.Verifying,    // Input detected → demand re-verification

                (GuardState.InactivityLock, TransitionTrigger.ManualFallback)
                    => GuardState.Unlocked,

                (GuardState.InactivityLock, TransitionTrigger.LoginScreenDetected)
                    => GuardState.BootScan,

                (GuardState.InactivityLock, TransitionTrigger.CameraObstructed)
                    => GuardState.HostileLock,

                // ── FROM SOCIAL_LOCK ──────────────────────────────────────────
                (GuardState.SocialLock, TransitionTrigger.StrangerLeft)
                    => HandleStrangerLeft(),

                (GuardState.SocialLock, TransitionTrigger.CameraObstructed)
                    => GuardState.HostileLock,

                (GuardState.SocialLock, TransitionTrigger.NoFaceDetected)
                    => GuardState.InactivityLock,

                (GuardState.SocialLock, TransitionTrigger.LoginScreenDetected)
                    => GuardState.BootScan,

                // ── FROM HOSTILE_LOCK ─────────────────────────────────────────
                (GuardState.HostileLock, TransitionTrigger.FaceDetected)
                    => GuardState.Verifying,

                (GuardState.HostileLock, TransitionTrigger.ManualFallback)
                    => GuardState.Unlocked,

                (GuardState.HostileLock, TransitionTrigger.LoginScreenDetected)
                    => GuardState.BootScan,

                // ── UNHANDLED (invalid transitions) ──────────────────────────
                _ => null
            };
        }

        // ─────────────────────────────────────────────────────────────
        // GUARD CONDITIONS
        // ─────────────────────────────────────────────────────────────

        private GuardState HandleAuthSuccess()
        {
            _authFailureCount = 0;
            return GuardState.Unlocked;
        }

        private GuardState HandleAuthFailure()
        {
            _authFailureCount++;
            _logger.LogWarning("Auth failure {Count}/{Max}", _authFailureCount, _maxAuthFailures);

            if (_authFailureCount >= _maxAuthFailures)
            {
                _authFailureCount = 0;
                return GuardState.HostileLock;
            }
            return GuardState.BootScan;  // Retry
        }

        private GuardState HandleStrangerDetected()
        {
            _strangerFirstSeen ??= DateTime.UtcNow;
            _strangerLastSeen = null; // Reset absence tracker — stranger is back

            var presenceDuration = DateTime.UtcNow - _strangerFirstSeen.Value;
            if (presenceDuration.TotalMilliseconds >= 500)
            {
                _logger.LogWarning("Stranger detected for {ms}ms — entering SocialLock",
                    presenceDuration.TotalMilliseconds);
                return GuardState.SocialLock;
            }

            return GuardState.Unlocked;  // Too brief — ignore
        }

        private GuardState HandleStrangerLeft()
        {
            // Record the first moment the stranger disappeared, not every call
            _strangerLastSeen ??= DateTime.UtcNow;

            var absenceDuration = DateTime.UtcNow - _strangerLastSeen.Value;
            if (absenceDuration >= _strangerHysteresis)
            {
                _strangerFirstSeen = null;
                _strangerLastSeen  = null;
                _logger.LogInformation("Stranger cleared — restoring Unlocked state");
                return GuardState.Unlocked;
            }

            return GuardState.SocialLock;  // Not clear yet
        }

        private void OnStateChanged(GuardState prev, GuardState next, TransitionTrigger trigger)
        {
            StateChanged?.Invoke(this, new StateChangedEventArgs
            {
                Previous = prev,
                Current  = next,
                Trigger  = trigger,
            });
        }
    }
}
