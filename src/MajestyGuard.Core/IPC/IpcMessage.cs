// MajestyGuard.Core/IPC/IpcMessage.cs
// All messages exchanged between Service, CVEngine, Overlay, and CredentialProvider.
// Serialized as JSON over Named Pipes.
// CODEX: Do not add network serialization. Local pipes only.

using System;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace MajestyGuard.Core.IPC
{
    // ─────────────────────────────────────────────────────────────────
    // BASE MESSAGE
    // ─────────────────────────────────────────────────────────────────
    public abstract class IpcMessage
    {
        public string MessageType { get; init; }
        public DateTime Timestamp  { get; init; } = DateTime.UtcNow;

        protected IpcMessage(string type) => MessageType = type;

        public string Serialize() =>
            JsonSerializer.Serialize(this, GetType(), _opts);

        public static IpcMessage? Deserialize(string json)
        {
            if (string.IsNullOrWhiteSpace(json)) return null;

            try
            {
                using var doc = JsonDocument.Parse(json);
                if (!doc.RootElement.TryGetProperty("MessageType", out var typeProp))
                    return null;
                var type = typeProp.GetString();

                return type switch
                {
                    "DetectionResult"       => JsonSerializer.Deserialize<DetectionResultMsg>(json, _opts),
                    "StateChange"           => JsonSerializer.Deserialize<StateChangeMsg>(json, _opts),
                    "AuthDecision"          => JsonSerializer.Deserialize<AuthDecisionMsg>(json, _opts),
                    "OverlayCommand"        => JsonSerializer.Deserialize<OverlayCommandMsg>(json, _opts),
                    "Heartbeat"             => JsonSerializer.Deserialize<HeartbeatMsg>(json, _opts),
                    "EnrollFrame"           => JsonSerializer.Deserialize<EnrollFrameMsg>(json, _opts),
                    "UserIdleDetected"      => JsonSerializer.Deserialize<UserIdleMsg>(json, _opts),
                    "UserActivityDetected"  => JsonSerializer.Deserialize<UserActivityMsg>(json, _opts),
                    "ManualFallbackRequest" => JsonSerializer.Deserialize<ManualFallbackRequestMsg>(json, _opts),
                    _ => null
                };
            }
            catch (JsonException)
            {
                return null;
            }
        }

        private static readonly JsonSerializerOptions _opts = new()
        {
            WriteIndented = false,
            Converters = { new JsonStringEnumConverter() }
        };
    }

    // ─────────────────────────────────────────────────────────────────
    // CV ENGINE → SERVICE
    // Sent after every processed frame
    // ─────────────────────────────────────────────────────────────────
    public class DetectionResultMsg : IpcMessage
    {
        public DetectionResultMsg() : base("DetectionResult") { }

        /// <summary>Number of faces detected in the frame (raw count, no recognition).</summary>
        public int FaceCount { get; init; }

        /// <summary>True if the primary enrolled user was recognized in the frame.</summary>
        public bool PrimaryUserPresent { get; init; }

        /// <summary>Recognition confidence score (0.0–1.0). Only valid if PrimaryUserPresent.</summary>
        public double RecognitionScore { get; init; }

        /// <summary>Liveness score (0.0–1.0). Scores below threshold = spoof attempt.</summary>
        public double LivenessScore { get; init; }

        /// <summary>True if liveness check passed. False = photo/video attack detected.</summary>
        public bool LivenessPassed { get; init; }

        /// <summary>True if CV engine suspects a virtual/software camera (replay attack).</summary>
        public bool VirtualCameraDetected { get; init; }

        /// <summary>True if camera feed appears completely dark or physically obstructed.</summary>
        public bool CameraObstructed { get; init; }

        /// <summary>Milliseconds taken to process this frame.</summary>
        public double InferenceMs { get; init; }
    }

    // ─────────────────────────────────────────────────────────────────
    // SERVICE → ALL (broadcast on every state change)
    // ─────────────────────────────────────────────────────────────────
    public class StateChangeMsg : IpcMessage
    {
        public StateChangeMsg() : base("StateChange") { }

        public GuardState Previous { get; init; }
        public GuardState Current  { get; init; }
        public TransitionTrigger Trigger { get; init; }
    }

    // ─────────────────────────────────────────────────────────────────
    // SERVICE → CREDENTIAL PROVIDER
    // Sent during BootScan to signal auth outcome
    // ─────────────────────────────────────────────────────────────────
    public class AuthDecisionMsg : IpcMessage
    {
        public AuthDecisionMsg() : base("AuthDecision") { }

        public bool Granted { get; init; }
        public string Reason { get; init; } = string.Empty;  // "FaceMatch" | "Timeout" | "Spoofing"
    }

    // ─────────────────────────────────────────────────────────────────
    // SERVICE → OVERLAY
    // Drives the Dynamic Island UI state and animation
    // ─────────────────────────────────────────────────────────────────
    public class OverlayCommandMsg : IpcMessage
    {
        public OverlayCommandMsg() : base("OverlayCommand") { }

        public OverlayDisplayState DisplayState { get; init; }

        /// <summary>Optional status text override for the pill label.</summary>
        public string? StatusText { get; init; }

        /// <summary>0.0–1.0 blur amount for background. Overlay applies this.</summary>
        public double BlurAmount { get; init; }
    }

    public enum OverlayDisplayState
    {
        Hidden,          // Fully invisible — UNLOCKED normal operation
        Searching,       // Pill visible, pulsing camera icon
        Verifying,       // Pill expanded, face-scan arc animation
        Unlocked,        // Green pulse then collapse
        SocialLock,      // Full-width amber bar
        HostileLock,     // Full opaque black screen + pill
        InactivityLock,  // Same as HostileLock visually
    }

    // ─────────────────────────────────────────────────────────────────
    // BIDIRECTIONAL — process health check
    // ─────────────────────────────────────────────────────────────────
    public class HeartbeatMsg : IpcMessage
    {
        public HeartbeatMsg() : base("Heartbeat") { }
        public string ProcessName { get; init; } = string.Empty;
        public double CpuPercent  { get; init; }
        public long   RamBytes    { get; init; }
    }

    // ─────────────────────────────────────────────────────────────────
    // SERVICE → CV ENGINE (enrollment only)
    // Tells CV engine to capture and store a face embedding
    // ─────────────────────────────────────────────────────────────────
    public class EnrollFrameMsg : IpcMessage
    {
        public EnrollFrameMsg() : base("EnrollFrame") { }

        public EnrollmentAngle Angle { get; init; }
    }

    public enum EnrollmentAngle
    {
        Front,
        SlightLeft,
        SlightRight,
        LookUp,
        LookDown,
        WithGlasses,    // Optional — prompt user
    }

    // FIX-016: Overlay (Session 1) reports idle/activity to Service (Session 0).
    // GetLastInputInfo from a SYSTEM service is blind to user-session input.
    public class UserIdleMsg : IpcMessage
    {
        public UserIdleMsg() : base("UserIdleDetected") { }
        public ulong IdleMs { get; init; }
    }

    public class UserActivityMsg : IpcMessage
    {
        public UserActivityMsg() : base("UserActivityDetected") { }
    }

    // ─────────────────────────────────────────────────────────────────
    // CREDENTIAL PROVIDER → SERVICE
    // Sent when user clicks "Enter password instead"
    // ─────────────────────────────────────────────────────────────────
    public class ManualFallbackRequestMsg : IpcMessage
    {
        public ManualFallbackRequestMsg() : base("ManualFallbackRequest") { }
    }
}
