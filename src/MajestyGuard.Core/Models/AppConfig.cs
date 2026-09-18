// MajestyGuard.Core/Models/AppConfig.cs
// Single source of truth for all configuration values.
// Loaded from %APPDATA%\MajestyGuard\config.json at startup.
// CODEX: Add JSON serialization and a Load() / Save() method.

using System;
using System.IO;
using System.Text.Json;
using System.Security.Principal;

namespace MajestyGuard.Core.Models
{
    public class AppConfig
    {
        // ── PROFILE ISOLATION ─────────────────────────────────────────
        /// <summary>
        /// The Windows SID of the enrolled user.
        /// Set during enrollment. Service exits immediately if current
        /// user SID does not match this value.
        /// </summary>
        public string EnrolledUserSid { get; set; } = string.Empty;

        // ── INACTIVITY LOCK ───────────────────────────────────────────
        /// <summary>Seconds of no keyboard/mouse before InactivityLock triggers.</summary>
        public int InactivityTimeoutSeconds { get; set; } = 120;

        /// <summary>How often (ms) to poll GetLastInputInfo during idle monitoring.</summary>
        public int InactivityPollIntervalMs { get; set; } = 2000;

        // ── FACE DETECTION LOOP ───────────────────────────────────────
        /// <summary>Frames per second during idle monitoring (low cost).</summary>
        public int MonitoringFps { get; set; } = 1;

        /// <summary>FPS when inactivity threshold is approaching (last 20 seconds).</summary>
        public int EscalatedFps { get; set; } = 10;

        /// <summary>FPS during active verification (login / re-auth).</summary>
        public int VerificationFps { get; set; } = 15;

        // ── SOCIAL LOCK (MULTI-FACE) ──────────────────────────────────
        /// <summary>
        /// Milliseconds a stranger must be visible before SocialLock triggers.
        /// Prevents false positives from someone walking past in background.
        /// </summary>
        public double StrangerPresenceThresholdMs { get; set; } = 500;

        /// <summary>
        /// Seconds stranger must be GONE before SocialLock releases.
        /// </summary>
        public double StrangerHysteresisSeconds { get; set; } = 3.0;

        // ── AUTHENTICATION ────────────────────────────────────────────
        /// <summary>Max consecutive recognition failures before HostileLock.</summary>
        public int MaxAuthFailures { get; set; } = 3;

        /// <summary>Recognition confidence threshold (0.0 - 1.0). Recommend ≥ 0.72.</summary>
        /// Research: ArcFace R100 at 0.78 achieves FAR ~1:10M (IJB-C benchmark).
        /// 0.72 = FAR ~1:100k — too permissive for a lock screen.
        /// Raising to 0.78 increases FRR slightly but is correct for security.
        public double RecognitionThreshold { get; set; } = 0.78;

        /// <summary>Liveness score threshold. Frames below this are rejected as spoofs.</summary>
        public double LivenessThreshold { get; set; } = 0.85;

        // ── CV ENGINE ─────────────────────────────────────────────────
        /// <summary>Path to InsightFace ONNX model directory.</summary>
        public string ModelDirectory { get; set; } =
            Path.Combine(AppContext.BaseDirectory, "models");

        /// <summary>
        /// Optional absolute Python executable path for the CV engine.
        /// Used by the LocalSystem service when PATH does not include the
        /// enrolled user's Python installation.
        /// </summary>
        public string CvPythonPath { get; set; } = string.Empty;

        /// <summary>
        /// Absolute path to the CV engine Python entrypoint.
        /// Services should use ProgramData so OS policy does not block
        /// LocalSystem from reading scripts under Program Files.
        /// </summary>
        public string CvScriptPath { get; set; } =
            Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                "MajestyGuard", "CVEngine", "cv_server.py");

        /// <summary>
        /// Camera device index. 0 = default/first camera.
        /// -1 means auto-detect best camera (prefers IR over RGB).
        /// </summary>
        public int CameraDeviceIndex { get; set; } = 0;

        /// <summary>Camera capture resolution width.</summary>
        public int CameraWidth { get; set; } = 640;

        /// <summary>Camera capture resolution height.</summary>
        public int CameraHeight { get; set; } = 480;

        // ── PROCESS RESTRICTION (SOCIAL LOCK) ────────────────────────
        /// <summary>
        /// Win32 process names to suspend when SocialLock activates.
        /// Includes both Win32 and UWP package family names.
        /// </summary>
        public string[] RestrictedProcesses { get; set; } =
        [
            "WhatsApp.exe",
            "Instagram.exe",
            "chrome.exe",       // CODEX NOTE: partial — only kill Gmail tabs, not all of Chrome
            "msedge.exe",
        ];

        /// <summary>
        /// UWP package family names to suspend (different API than Win32).
        /// </summary>
        public string[] RestrictedUwpPackages { get; set; } =
        [
            "5319275A.WhatsAppDesktop_cv1g1gvanyjgm",
            "Instagram_8wekyb3d8bbwe",
        ];

        /// <summary>
        /// File system paths to restrict access to during SocialLock.
        /// DACL modification — reversed on SocialLock exit.
        /// </summary>
        public string[] RestrictedPaths { get; set; } =
        [
            Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            Environment.GetFolderPath(Environment.SpecialFolder.MyPictures),
        ];

        // ── IPC ───────────────────────────────────────────────────────
        public string CvPipeName      { get; set; } = "MajestyGuard_CV";
        public string OverlayPipeName { get; set; } = "MajestyGuard_Overlay";
        public string CredProvPipeName{ get; set; } = "MajestyGuard_CredProv";

        /// <summary>Whether the service should launch the overlay child process.</summary>
        public bool EnableOverlayLaunch { get; set; } = true;

        // ── OVERLAY UI ────────────────────────────────────────────────
        /// <summary>Blur amount (0–1) during BOOT_SCAN and VERIFYING states.</summary>
        public double ScanBlurAmount { get; set; } = 0.90;

        /// <summary>Blur amount during SOCIAL_LOCK.</summary>
        public double SocialLockBlurAmount { get; set; } = 0.60;

        // ── STORAGE ───────────────────────────────────────────────────
        public string EmbeddingStorePath { get; set; } =
            Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "MajestyGuard", "biometric.mgd");

        // ─────────────────────────────────────────────────────────────
        // SERIALIZATION
        // ─────────────────────────────────────────────────────────────
        private static readonly string _configPath =
            Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "MajestyGuard", "config.json");

        private static readonly JsonSerializerOptions _jsonOpts = new()
        {
            WriteIndented = true,
        };

        public static AppConfig Load()
        {
            if (!File.Exists(_configPath))
                return new AppConfig();  // Defaults

            var json = File.ReadAllText(_configPath);
            return JsonSerializer.Deserialize<AppConfig>(json, _jsonOpts)
                   ?? new AppConfig();
        }

        public void Save()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_configPath)!);
            File.WriteAllText(_configPath, JsonSerializer.Serialize(this, _jsonOpts));
        }

        /// <summary>
        /// Returns true if the current Windows user may use the enrolled profile.
        /// The LocalSystem service is allowed when an enrolled SID is configured;
        /// interactive processes must still match the enrolled SID exactly.
        /// </summary>
        public bool IsEnrolledProfile()
        {
            var currentSid = WindowsIdentity.GetCurrent().User?.Value;
            return IsEnrolledProfileForSid(currentSid);
        }

        public bool IsEnrolledProfileForSid(string? currentSid)
        {
            if (string.IsNullOrEmpty(EnrolledUserSid)) return false;
            if (string.Equals(currentSid, "S-1-5-18",
                    StringComparison.OrdinalIgnoreCase))
                return true;

            return string.Equals(currentSid, EnrolledUserSid,
                StringComparison.OrdinalIgnoreCase);
        }
    }
}


