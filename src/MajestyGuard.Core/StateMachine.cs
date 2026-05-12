// MajestyGuard.Core/StateMachine.cs
// THE HEART OF MAJESTY GUARD
// ALL state transitions flow through this class.
// No component should change state directly — always call RequestTransition().

using System;
using System.Threading;
using Microsoft.Extensions.Logging;
using MajestyGuard.Core.Models;

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

        /// <summary>
        /// Fired OUTSIDE the state machine lock after every transition.
        /// WARNING: Handlers MUST NOT call RequestTransition() synchronously —
        /// Monitor.Enter is not reentrant and will deadlock. Use async handlers
        /// with await Task.Yield() or a dispatch queue before calling back.
        /// </summary>
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
            GuardState previous;
            GuardState next;

            lock (_lock)
            {
                previous = _current;
                var resolved = ResolveNextState(trigger, context);

                if (resolved == null)
                {
                    _logger.LogDebug(
                        "Trigger {Trigger} ignored in state {State}", trigger, _current);
                    return false;
                }

                next = resolved.Value;
                _current = next;
                _logger.LogInformation(
                    "State: {Prev} → {Next} (trigger: {Trigger})", previous, _current, trigger);
            }

            // Fire event OUTSIDE lock — handlers can safely call RequestTransition
            OnStateChanged(previous, next, trigger);
            return true;
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
                    => EnterHostileLock(),

                // ── FROM VERIFYING ────────────────────────────────────────────
                (GuardState.Verifying, TransitionTrigger.FaceRecognized)
                    => HandleAuthSuccess(),

                (GuardState.Verifying, TransitionTrigger.FaceUnrecognized)
                    => HandleAuthFailure(),

                (GuardState.Verifying, TransitionTrigger.LivenessCheckFailed)
                    => EnterHostileLock(),

                (GuardState.Verifying, TransitionTrigger.NoFaceDetected)
                    => GuardState.BootScan,

                (GuardState.Verifying, TransitionTrigger.CameraObstructed)
                    => EnterHostileLock(),

                // ── FROM UNLOCKED ─────────────────────────────────────────────
                (GuardState.Unlocked, TransitionTrigger.StrangerDetected)
                    => HandleStrangerDetected(),

                (GuardState.Unlocked, TransitionTrigger.InactivityThresholdHit)
                    => GuardState.InactivityLock,

                (GuardState.Unlocked, TransitionTrigger.NoFaceDetected)
                    => GuardState.InactivityLock,

                (GuardState.Unlocked, TransitionTrigger.CameraObstructed)
                    => EnterHostileLock(),

                (GuardState.Unlocked, TransitionTrigger.LoginScreenDetected)
                    => GuardState.BootScan,

                // ── FROM INACTIVITY_LOCK ──────────────────────────────────────
                (GuardState.InactivityLock, TransitionTrigger.FaceDetected)
                    => GuardState.Verifying,

                (GuardState.InactivityLock, TransitionTrigger.UserInputDetected)
                    => GuardState.Verifying,

                (GuardState.InactivityLock, TransitionTrigger.ManualFallback)
                    => GuardState.BootScan,  // Force re-verification — not direct Unlocked

                (GuardState.InactivityLock, TransitionTrigger.LoginScreenDetected)
                    => GuardState.BootScan,

                (GuardState.InactivityLock, TransitionTrigger.CameraObstructed)
                    => EnterHostileLock(),

                // ── FROM SOCIAL_LOCK ──────────────────────────────────────────
                (GuardState.SocialLock, TransitionTrigger.StrangerLeft)
                    => ClearStrangerAndGoBootScan(),

                (GuardState.SocialLock, TransitionTrigger.CameraObstructed)
                    => EnterHostileLock(),

                (GuardState.SocialLock, TransitionTrigger.NoFaceDetected)
                    => GuardState.InactivityLock,

                (GuardState.SocialLock, TransitionTrigger.LoginScreenDetected)
                    => GuardState.BootScan,

                // ── FROM HOSTILE_LOCK ─────────────────────────────────────────
                (GuardState.HostileLock, TransitionTrigger.FaceDetected)
                    => GuardState.Verifying,

                (GuardState.HostileLock, TransitionTrigger.ManualFallback)
                    => HandleHostileFallback(),

                (GuardState.HostileLock, TransitionTrigger.LoginScreenDetected)
                    => GuardState.BootScan,

                // ── UNHANDLED (invalid transitions) ──────────────────────────
                _ => null
            };
        }

        // ─────────────────────────────────────────────────────────────
        // GUARD CONDITIONS
        // ─────────────────────────────────────────────────────────────

        // ── B-009: HostileLock cooldown ─────────────────────────────────────
        private DateTime? _hostileLockEntryTime;

        private GuardState HandleHostileFallback()
        {
            if (_hostileLockEntryTime.HasValue &&
                (DateTime.UtcNow - _hostileLockEntryTime.Value).TotalSeconds < 30)
            {
                _logger.LogWarning("ManualFallback blocked — cooldown active ({Sec:F0}s remaining)",
                    30 - (DateTime.UtcNow - _hostileLockEntryTime.Value).TotalSeconds);
                return GuardState.HostileLock;
            }
            _hostileLockEntryTime = null;
            return GuardState.Unlocked;
        }

        private GuardState EnterHostileLock()
        {
            _hostileLockEntryTime = DateTime.UtcNow;
            return GuardState.HostileLock;
        }

        private GuardState HandleAuthSuccess()
        {
            _authFailureCount = 0;
            _hostileLockEntryTime = null;  // Clear cooldown on successful auth
            return GuardState.Unlocked;
        }

        private GuardState HandleAuthFailure()
        {
            _authFailureCount++;
            _logger.LogWarning("Auth failure {Count}/{Max}", _authFailureCount, _maxAuthFailures);

            if (_authFailureCount >= _maxAuthFailures)
            {
                _authFailureCount = 0;
                return EnterHostileLock();
            }
            return GuardState.BootScan;  // Retry
        }

        // B-003 FIX: Reset stranger tracking when no stranger is in frame.
        // Call from Worker when FaceCount==1. Prevents accumulated flicker time.
        public void ResetStrangerTracking()
        {
            lock (_lock)
            {
                _strangerFirstSeen = null;
                _strangerLastSeen = null;
            }
        }

        private GuardState ClearStrangerAndGoBootScan()
        {
            _strangerFirstSeen = null;
            _strangerLastSeen = null;
            return GuardState.BootScan;
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
