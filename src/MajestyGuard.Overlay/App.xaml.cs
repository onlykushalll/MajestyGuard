using System;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.UI.Xaml;
using Microsoft.Extensions.Logging;
using MajestyGuard.Core.Models;

namespace MajestyGuard.Overlay
{
    public partial class App : Application
    {
        private static Mutex? _singleInstanceMutex;
        private DynamicIslandWindow? _mainWindow;

        private readonly ILogger<App> _logger;
        private readonly AppConfig _config;

        // P/Invoke for process mitigations
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetProcessMitigationPolicy(
            int MitigationPolicy, IntPtr lpBuffer, int dwLength);

        // Policy types
        private const int ProcessDEPPolicy = 0;
        private const int ProcessASLRPolicy = 1;
        private const int ProcessSignaturePolicy = 8;

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_MITIGATION_DEP_POLICY
        {
            public uint Flags;
            public byte Permanent;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_MITIGATION_ASLR_POLICY
        {
            public uint Flags;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_MITIGATION_BINARY_SIGNATURE_POLICY
        {
            public uint Flags;
        }

        public App()
        {
            InitializeComponent();
            _config = AppConfig.Load();

            using var logFactory = Microsoft.Extensions.Logging.LoggerFactory.Create(b =>
                b.AddConsole().SetMinimumLevel(LogLevel.Debug));
            _logger = logFactory.CreateLogger<App>();
        }

        protected override void OnLaunched(LaunchActivatedEventArgs args)
        {
            const string MUTEX_NAME = "Global\\MajestyGuard.Overlay.SingleInstance";
            _singleInstanceMutex = new Mutex(initiallyOwned: true, MUTEX_NAME, out bool createdNew);

            if (!createdNew)
            {
                _logger.LogWarning("Overlay already running. Exiting duplicate instance.");
                Exit();
                return;
            }

            ApplyProcessMitigations();

            // ── ENROLLMENT CHECK ──────────────────────────────────────
            // If this profile has no face enrollment, show the setup wizard first.
            // After enrollment completes, the wizard closes and the main overlay starts.
            var store = new MajestyGuard.Core.Security.EmbeddingStore(_config.EmbeddingStorePath);
            if (!store.HasEnrollment() || string.IsNullOrEmpty(_config.EnrolledUserSid))
            {
                _logger.LogInformation("No enrollment found — launching setup wizard");

                var enrollLogger = Microsoft.Extensions.Logging.LoggerFactory
                    .Create(b => b.AddConsole())
                    .CreateLogger<EnrollmentWindow>();

                var enrollWindow = new EnrollmentWindow(enrollLogger, _config);
                enrollWindow.Closed += (_, _) =>
                {
                    // After wizard closes, launch main overlay if enrollment succeeded
                    if (!string.IsNullOrEmpty(_config.EnrolledUserSid))
                    {
                        DispatcherQueue.GetForCurrentThread()?.TryEnqueue(() =>
                        {
                            _mainWindow = new DynamicIslandWindow(
                                _logger as ILogger<DynamicIslandWindow>
                                ?? Microsoft.Extensions.Logging.LoggerFactory
                                    .Create(b => b.AddConsole())
                                    .CreateLogger<DynamicIslandWindow>(),
                                _config);
                            _mainWindow.Activate();
                        });
                    }
                };
                enrollWindow.Activate();
                return;
            }

            // ── NORMAL STARTUP: already enrolled ─────────────────────
                ?? Microsoft.Extensions.Logging.LoggerFactory
                    .Create(b => b.AddConsole())
                    .CreateLogger<DynamicIslandWindow>(),
                _config);

            _mainWindow.Activate();
            _logger.LogInformation("MajestyGuard Overlay started");
        }

        private void ApplyProcessMitigations()
        {
#if !DEBUG
            try
            {
                // Enable DEP
                var depPolicy = new PROCESS_MITIGATION_DEP_POLICY
                {
                    Flags = 0x01, // PROCESS_DEP_ENABLE
                    Permanent = 1,
                };
                var depSize = Marshal.SizeOf(depPolicy);
                var depPtr = Marshal.AllocHGlobal(depSize);
                Marshal.StructureToPtr(depPolicy, depPtr, false);
                SetProcessMitigationPolicy(ProcessDEPPolicy, depPtr, depSize);
                Marshal.FreeHGlobal(depPtr);

                // Enable forced ASLR
                var aslrPolicy = new PROCESS_MITIGATION_ASLR_POLICY
                {
                    Flags = 0x02, // EnableForceRelocateImages
                };
                var aslrSize = Marshal.SizeOf(aslrPolicy);
                var aslrPtr = Marshal.AllocHGlobal(aslrSize);
                Marshal.StructureToPtr(aslrPolicy, aslrPtr, false);
                SetProcessMitigationPolicy(ProcessASLRPolicy, aslrPtr, aslrSize);
                Marshal.FreeHGlobal(aslrPtr);

                // Block non-Microsoft signed DLLs
                var sigPolicy = new PROCESS_MITIGATION_BINARY_SIGNATURE_POLICY
                {
                    Flags = 0x01, // MicrosoftSignedOnly
                };
                var sigSize = Marshal.SizeOf(sigPolicy);
                var sigPtr = Marshal.AllocHGlobal(sigSize);
                Marshal.StructureToPtr(sigPolicy, sigPtr, false);
                SetProcessMitigationPolicy(ProcessSignaturePolicy, sigPtr, sigSize);
                Marshal.FreeHGlobal(sigPtr);
            }
            catch
            {
                // Mitigations are best-effort — don't crash the overlay
            }
#endif
        }
    }
}
