// MajestyGuard.Service/Worker.cs
// Entry point for the Windows Service.
// Runs as SYSTEM. Orchestrates all sub-components.
//
// STARTUP SEQUENCE:
//   1. Load AppConfig
//   2. Verify current user SID matches enrolled SID → exit if not
//   3. Start IPC pipe servers
//   4. Start CV Engine (Python subprocess)
//   5. Start InactivityWatcher
//   6. Start PresenceMonitor
//   7. Start Overlay (WinUI 3 process)
//   8. Subscribe to StateMachine.StateChanged → dispatch effects
//
// CODEX: The pipe servers and subprocess management are stubbed.
//        Implement the TODO sections.

using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using MajestyGuard.Core;
using MajestyGuard.Core.IPC;
using MajestyGuard.Core.Models;
using MajestyGuard.Core.Security;

namespace MajestyGuard.Service
{
    public class Worker : BackgroundService
    {
        private readonly ILogger<Worker> _logger;
        private readonly AppConfig _config;
        private readonly StateMachine _stateMachine;
        private readonly PresenceMonitor _presenceMonitor;
        private readonly InactivityWatcher _inactivityWatcher;
        private readonly SocialLockEngine _socialLockEngine;
        private readonly ProcessRestrictor _processRestrictor;
        private readonly DesktopWatchdog   _desktopWatchdog;   // B-030: declared once
        // CC-2: Volatile long (Interlocked.Read/Write) instead of DateTime? across threads
        private long _verifyingStartTicks;  // 0 = not verifying. Interlocked-accessed only.

        private volatile bool _embeddingsLoaded;     // HB-1: gate PresenceMonitor until embeddings ready

        // C5: Serialized state-change dispatch — prevents async void races and out-of-order overlay commands
        private readonly Channel<StateChangedEventArgs> _stateChangeQueue =
            Channel.CreateUnbounded<StateChangedEventArgs>();

        // IPC servers
        private MajestyPipeServer? _cvPipe;
        private MajestyPipeServer? _overlayPipe;
        private MajestyPipeServer? _credProvPipe;

        // Child processes
        private Process? _cvEngineProcess;
        private Process? _overlayProcess;

        // Session lock/unlock detection (C4)
        private SessionWatcher? _sessionWatcher;

        // P/Invoke for launching DpapiHelper as logged-in user
        [DllImport("wtsapi32.dll", SetLastError = true)]
        private static extern bool WTSQueryUserToken(uint sessionId, out IntPtr phToken);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentProcess();

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetProcessWorkingSetSize(
            IntPtr hProcess, nint dwMinimumWorkingSetSize, nint dwMaximumWorkingSetSize);

        [DllImport("kernel32.dll")]
        private static extern uint WTSGetActiveConsoleSessionId();

        // Anti-tamper: protect service process from termination by non-admins
        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool SetSecurityInfo(
            IntPtr handle, uint objectType, uint securityInfo,
            IntPtr psidOwner, IntPtr psidGroup,
            IntPtr pDacl, IntPtr pSacl);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(
            string stringSecurityDescriptor, uint stringSDRevision,
            out IntPtr SecurityDescriptor, out uint SecurityDescriptorSize);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool GetSecurityDescriptorDacl(
            IntPtr pSecurityDescriptor, out bool lpbDaclPresent,
            out IntPtr pDacl, out bool lpbDaclDefaulted);

        [DllImport("kernel32.dll")]
        private static extern bool SetProcessMitigationPolicy(
            int MitigationPolicy, ref ulong lpBuffer, int dwLength);

        private const uint SE_KERNEL_OBJECT = 6;
        private const uint DACL_SECURITY_INFORMATION = 0x00000004;

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool CreateProcessAsUserW(
            IntPtr hToken,
            string? lpApplicationName,
            string? lpCommandLine,
            IntPtr lpProcessAttributes,
            IntPtr lpThreadAttributes,
            bool bInheritHandles,
            uint dwCreationFlags,
            IntPtr lpEnvironment,
            string? lpCurrentDirectory,
            ref STARTUPINFOW lpStartupInfo,
            out PROCESS_INFORMATION lpProcessInformation);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CreatePipe(
            out IntPtr hReadPipe, out IntPtr hWritePipe,
            ref SECURITY_ATTRIBUTES lpPipeAttributes, uint nSize);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetHandleInformation(IntPtr hObject, uint dwMask, uint dwFlags);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool ReadFile(
            IntPtr hFile, byte[] lpBuffer, uint nNumberOfBytesToRead,
            out uint lpNumberOfBytesRead, IntPtr lpOverlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr hHandle, uint dwMilliseconds);

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct STARTUPINFOW
        {
            public int cb;
            public string? lpReserved;
            public string? lpDesktop;
            public string? lpTitle;
            public int dwX, dwY, dwXSize, dwYSize;
            public int dwXCountChars, dwYCountChars;
            public int dwFillAttribute;
            public int dwFlags;
            public short wShowWindow;
            public short cbReserved2;
            public IntPtr lpReserved2;
            public IntPtr hStdInput;
            public IntPtr hStdOutput;
            public IntPtr hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct PROCESS_INFORMATION
        {
            public IntPtr hProcess;
            public IntPtr hThread;
            public int dwProcessId;
            public int dwThreadId;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SECURITY_ATTRIBUTES
        {
            public int nLength;
            public IntPtr lpSecurityDescriptor;
            public bool bInheritHandle;
        }

        private const int STARTF_USESTDHANDLES = 0x00000100;
        private const uint CREATE_NO_WINDOW = 0x08000000;
        private const uint HANDLE_FLAG_INHERIT = 0x00000001;

        public Worker(
            ILogger<Worker> logger,
            AppConfig config,
            StateMachine stateMachine,
            PresenceMonitor presenceMonitor,
            InactivityWatcher inactivityWatcher,
            SocialLockEngine socialLockEngine,
            ProcessRestrictor processRestrictor,
            DesktopWatchdog desktopWatchdog)
        {
            _logger            = logger;
            _config            = config;
            _stateMachine      = stateMachine;
            _presenceMonitor   = presenceMonitor;
            _inactivityWatcher = inactivityWatcher;
            _socialLockEngine  = socialLockEngine;
            _processRestrictor = processRestrictor;
            _desktopWatchdog   = desktopWatchdog;
        }

        protected override async Task ExecuteAsync(CancellationToken ct)
        {
            _logger.LogInformation("MajestyGuard Service starting");

            // Harden this process against tampering by non-admin users
            HardenProcess();

            // ── STEP 1: Profile isolation check ──────────────────────
            if (!_config.IsEnrolledProfile())
            {
                _logger.LogInformation(
                    "Current profile is not enrolled. Service entering dormant state.");
                _stateMachine.RequestTransition(TransitionTrigger.EnrollmentRequired);
                return;
            }

            _logger.LogInformation("Enrolled profile confirmed. Initializing systems.");

            // ── STEP 2: Subscribe to state machine ────────────────────
            _stateMachine.StateChanged += OnStateChanged;

            // ── STEP 3: Start IPC servers ─────────────────────────────
            _cvPipe       = new MajestyPipeServer(_config.CvPipeName, _logger, _config.EnrolledUserSid);
            _overlayPipe  = new MajestyPipeServer(_config.OverlayPipeName, _logger, _config.EnrolledUserSid);
            _credProvPipe = new MajestyPipeServer(_config.CredProvPipeName, _logger, _config.EnrolledUserSid);

            _cvPipe.MessageReceived       += OnCvMessageAsync;
            _credProvPipe.MessageReceived += OnCredProvMessageAsync;
            _overlayPipe.MessageReceived  += OnOverlayMessageAsync;  // H-003: wire overlay messages

            // Wire PresenceMonitor to CV pipe for FPS commands
            _presenceMonitor.CvPipeServer = _cvPipe;

            var pipeTasks = Task.WhenAll(
                _cvPipe.StartAsync(ct),
                _overlayPipe.StartAsync(ct),
                _credProvPipe.StartAsync(ct));

            // Brief pause so pipe servers are listening before child processes connect
            await Task.Delay(800, ct);

            // ── STEP 4: Launch CV Engine subprocess ───────────────────
            _cvEngineProcess = LaunchCvEngine();

            // ── STEP 5: Launch Overlay process (after desktop init) ───
            await Task.Delay(2000, ct);
            _overlayProcess = LaunchOverlay();

            // ── STEP 6: Start monitoring loops ────────────────────────
            var monitorTask    = RunPresenceMonitorAsync(ct);  // HB-1: gate until embeddings loaded
            var inactivityTask = _inactivityWatcher.RunAsync(ct);
            var desktopTask    = _desktopWatchdog.RunAsync(ct);  // B-030: single instance
            var watchdogTask   = RunChildWatchdogAsync(ct);
            var dispatchTask   = RunStateChangeDispatchAsync(ct);  // C5: serialized state-change handler

            // ── STEP 7: Start session watcher (Win+L detection) ──────
            _sessionWatcher = new SessionWatcher(
                _logger,
                onSessionLock: () =>
                {
                    _logger.LogInformation("Session lock detected — transitioning to BootScan");
                    _stateMachine.RequestTransition(TransitionTrigger.LoginScreenDetected);
                },
                onSessionUnlock: () =>
                {
                    _logger.LogInformation("Session unlock detected — requesting verification");
                    if (_stateMachine.Current is GuardState.BootScan or GuardState.Dormant)
                        _stateMachine.RequestTransition(TransitionTrigger.FaceDetected);
                },
                onSuspend: () =>
                {
                    _logger.LogInformation("System suspending — pausing CV engine");
                    _cvPipe?.SendRawAsync("{\"cmd\":\"pause\"}").ConfigureAwait(false);
                },
                onResume: () =>
                {
                    _logger.LogInformation("System resuming — restarting CV engine and entering BootScan");
                    _cvPipe?.SendRawAsync("{\"cmd\":\"resume\"}").ConfigureAwait(false);
                    _stateMachine.RequestTransition(TransitionTrigger.LoginScreenDetected);
                });
            _sessionWatcher.Start();

            // FIX-007: Verifying state 5-second timeout watchdog (CC-2: Interlocked long)
            // If CVEngine crashes during Verifying, state machine would hang forever.
            _ = Task.Run(async () =>
            {
                while (!ct.IsCancellationRequested)
                {
                    await Task.Delay(1000, ct);
                    if (_stateMachine.Current == GuardState.Verifying)
                    {
                        long existing = Interlocked.CompareExchange(ref _verifyingStartTicks, DateTime.UtcNow.Ticks, 0);
                        long startTicks = existing == 0 ? Interlocked.Read(ref _verifyingStartTicks) : existing;
                        if (startTicks != 0 && (DateTime.UtcNow - new DateTime(startTicks, DateTimeKind.Utc)).TotalSeconds > 5)
                        {
                            _logger.LogWarning("Verifying timeout (5s) — forcing HostileLock");
                            _stateMachine.RequestTransition(TransitionTrigger.CameraObstructed);
                            Interlocked.Exchange(ref _verifyingStartTicks, 0);
                        }
                    }
                    else
                    {
                        Interlocked.Exchange(ref _verifyingStartTicks, 0);
                    }
                }
            }, ct);

            // Signal initial state
            _stateMachine.RequestTransition(TransitionTrigger.ProfileValidated);

            // ── STEP 8: Load enrolled embeddings into CV Engine ──────
            _ = LoadEmbeddingsAsync();

            // ── WAIT ──────────────────────────────────────────────────
            await Task.WhenAll(pipeTasks, monitorTask, inactivityTask, watchdogTask, dispatchTask);
        }

        // ─────────────────────────────────────────────────────────────
        // STATE CHANGE HANDLER
        // Dispatches side effects when the state machine transitions
        // ─────────────────────────────────────────────────────────────
        // C5: Sync handler — enqueues to channel for serialized dispatch (no async void)
        private void OnStateChanged(object? sender, StateChangedEventArgs e)
        {
            _stateChangeQueue.Writer.TryWrite(e);
        }

        private async Task RunStateChangeDispatchAsync(CancellationToken ct)
        {
            await foreach (var e in _stateChangeQueue.Reader.ReadAllAsync(ct))
            {
                try
                {
                    await DispatchStateChangeAsync(e);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "B-031: State change dispatch failed — state:{State}", e.Current);
                }
            }
        }

        private async Task DispatchStateChangeAsync(StateChangedEventArgs e)
        {
            _logger.LogInformation("Handling state transition: {Prev} → {Current}", e.Previous, e.Current);

            // Det_size switching — high-res during verification, low-res during idle
            if (e.Current is GuardState.Verifying or GuardState.BootScan)
                await _presenceMonitor.SetCvDetSizeAsync(320, 320);
            else if (e.Current == GuardState.Unlocked)
                await _presenceMonitor.SetCvDetSizeAsync(160, 160);

            // C-003: BlockInput from Session 0 is silently ineffective (Service runs SYSTEM/Session 0).
            // Input blocking is handled by the Overlay process in Session 1.

            // Build overlay command for every state
            var overlayCmd = e.Current switch
            {
                GuardState.BootScan       => new OverlayCommandMsg { DisplayState = OverlayDisplayState.Searching,     BlurAmount = _config.ScanBlurAmount },
                GuardState.Verifying      => new OverlayCommandMsg { DisplayState = OverlayDisplayState.Verifying,     BlurAmount = _config.ScanBlurAmount },
                GuardState.Unlocked       => new OverlayCommandMsg { DisplayState = OverlayDisplayState.Unlocked,      BlurAmount = 0.0 },
                GuardState.InactivityLock => new OverlayCommandMsg { DisplayState = OverlayDisplayState.InactivityLock,BlurAmount = 1.0 },
                GuardState.SocialLock     => new OverlayCommandMsg { DisplayState = OverlayDisplayState.SocialLock,    BlurAmount = _config.SocialLockBlurAmount },
                GuardState.HostileLock    => new OverlayCommandMsg { DisplayState = OverlayDisplayState.HostileLock,   BlurAmount = 1.0 },
                _                         => new OverlayCommandMsg { DisplayState = OverlayDisplayState.Hidden,        BlurAmount = 0.0 },
            };

            if (_overlayPipe != null)
                await _overlayPipe.SendAsync(overlayCmd);

            // C2: Only send AuthDecision on face recognition path — NOT on ManualFallback.
            // ManualFallback means user chose PIN; CredProvider handles PIN locally.
            if (e.Current == GuardState.Unlocked &&
                e.Previous == GuardState.Verifying &&
                e.Trigger == TransitionTrigger.FaceRecognized)
            {
                if (_credProvPipe != null)
                    await _credProvPipe.SendAsync(new AuthDecisionMsg { Granted = true, Reason = "FaceMatch" });
            }

            // ── DOOR LOCK PRINCIPLE ───────────────────────────────────────
            // InactivityLock and HostileLock are VISUAL locks only.
            // They block the SCREEN (what others can see) and USER INPUT,
            // but NEVER touch running processes, downloads, audio, or tasks.
            // Think: closing a door — the room keeps running.
            //
            // SocialLock (Safe Mode) is different — it deliberately restricts
            // specific apps because another person is physically present.
            // ─────────────────────────────────────────────────────────────

            // Regular lock states: Overlay handles everything. Nothing else touched.
            // Background tasks (downloads, Spotify, renders) continue unaffected.

            // Social lock side effects — ONLY for SocialLock, NEVER for other states
            if (e.Current == GuardState.SocialLock && e.Previous != GuardState.SocialLock)
            {
                await _socialLockEngine.ActivateAsync();
            }
            else if (e.Previous == GuardState.SocialLock && e.Current != GuardState.SocialLock)
            {
                // Restore everything that SocialLock restricted
                await _socialLockEngine.DeactivateAsync();
            }
            // EXPLICIT: InactivityLock exit also restores if we somehow entered from SocialLock
            else if (e.Current == GuardState.InactivityLock || e.Current == GuardState.HostileLock)
            {
                // Assert: no process manipulation happens here.
                // The overlay is the only thing that changes.
                _logger.LogDebug("Regular lock — overlay only. Background tasks unaffected.");
            }
        }

        // ─────────────────────────────────────────────────────────────
        // CV ENGINE MESSAGE HANDLER
        // Translates detection results into state machine triggers
        // ─────────────────────────────────────────────────────────────
        private Task OnCvMessageAsync(IpcMessage message)
        {
            if (message is not DetectionResultMsg result) return Task.CompletedTask;

            // Virtual camera / replay attack
            if (result.VirtualCameraDetected)
            {
                _logger.LogWarning("Virtual camera detected — possible replay attack");
                _stateMachine.RequestTransition(TransitionTrigger.CameraObstructed);
                return Task.CompletedTask;
            }

            if (result.CameraObstructed)
            {
                _stateMachine.RequestTransition(TransitionTrigger.CameraObstructed);
                return Task.CompletedTask;
            }

            if (result.FaceCount == 0)
            {
                _stateMachine.RequestTransition(TransitionTrigger.NoFaceDetected);
                return Task.CompletedTask;
            }

            // Liveness check — must pass before ANY recognition
            if (!result.LivenessPassed)
            {
                _logger.LogWarning("Liveness check failed (score: {Score:F3}). Spoof attempt?",
                    result.LivenessScore);
                _stateMachine.RequestTransition(TransitionTrigger.LivenessCheckFailed);
                return Task.CompletedTask;
            }

            // Multi-face detection — stranger logic
            if (result.FaceCount > 1 && _stateMachine.Current == GuardState.Unlocked)
            {
                _socialLockEngine.ReportStrangerFrame();
                if (_socialLockEngine.ShouldTriggerSocialLock())
                    _stateMachine.RequestTransition(TransitionTrigger.StrangerDetected);
                return Task.CompletedTask;
            }

            // B-003: Reset stranger tracking when only primary user is present
            if (result.FaceCount == 1 && _stateMachine.Current == GuardState.Unlocked)
                _stateMachine.ResetStrangerTracking();

            // Primary user recognition
            if (result.PrimaryUserPresent &&
                result.RecognitionScore >= _config.RecognitionThreshold)
            {
                _stateMachine.RequestTransition(TransitionTrigger.FaceRecognized);
            }
            else if (result.FaceCount > 0)
            {
                _stateMachine.RequestTransition(TransitionTrigger.FaceUnrecognized);
            }

            return Task.CompletedTask;
        }

        private Task OnCredProvMessageAsync(IpcMessage message)
        {
            // S-3: Use dedicated ManualFallbackRequestMsg, not StateChangeMsg{Unlocked}
            // StateChangeMsg with Current:Unlocked was an auth bypass — any process could send it
            if (message is ManualFallbackRequestMsg)
                _stateMachine.RequestTransition(TransitionTrigger.ManualFallback);

            return Task.CompletedTask;
        }

        private Task OnOverlayMessageAsync(IpcMessage message)
        {
            // H-003: Handle messages from overlay (idle/activity reported from Session 1)
            // GetLastInputInfo from Session 0 SYSTEM service is blind to user input (FIX-016)
            switch (message)
            {
                case UserIdleMsg idle:
                    _logger.LogDebug("Overlay reports user idle: {Ms}ms", idle.IdleMs);
                    if (idle.IdleMs >= (ulong)(_config.InactivityTimeoutSeconds * 1000))
                        _stateMachine.RequestTransition(TransitionTrigger.InactivityThresholdHit);
                    break;

                case UserActivityMsg:
                    _stateMachine.RequestTransition(TransitionTrigger.UserInputDetected);
                    break;
            }

            return Task.CompletedTask;
        }

        // HB-1: Gate PresenceMonitor until CV engine has loaded embeddings.
        // Starting presence scan before embeddings are loaded causes every frame
        // to report FaceUnrecognized → HostileLock storm at startup.
        private async Task RunPresenceMonitorAsync(CancellationToken ct)
        {
            var timeout = DateTime.UtcNow.AddSeconds(30);
            while (!_embeddingsLoaded && DateTime.UtcNow < timeout && !ct.IsCancellationRequested)
                await Task.Delay(500, ct);

            if (!_embeddingsLoaded)
                _logger.LogWarning("HB-1: PresenceMonitor starting without confirmed embedding load (timeout)");

            await _presenceMonitor.RunAsync(ct);
        }

        // ─────────────────────────────────────────────────────────────
        // CHILD PROCESS LAUNCHERS
        // ─────────────────────────────────────────────────────────────

        private Process? LaunchCvEngine()  // H-005: nullable return
        {
            var pythonExe = FindPython();
            if (pythonExe == null)
            {
                _logger.LogError("Python not found. CV engine cannot start.");
                _stateMachine.RequestTransition(TransitionTrigger.CameraObstructed);
                return null;  // H-005: was null! which threw NRE in watchdog
            }

            var scriptPath = Path.Combine(AppContext.BaseDirectory, "CVEngine", "cv_server.py");
            var psi = new ProcessStartInfo
            {
                FileName        = pythonExe,
                Arguments       = $"\"{scriptPath}\"",
                UseShellExecute = false,
                CreateNoWindow  = true,
                RedirectStandardOutput = true,
                RedirectStandardError  = true,
            };

            psi.EnvironmentVariables["MG_CV_PIPE"]    = _config.CvPipeName;
            psi.EnvironmentVariables["MG_MODEL_DIR"]  = _config.ModelDirectory;
            psi.EnvironmentVariables["MG_CAMERA_IDX"] = _config.CameraDeviceIndex.ToString();

            var proc = Process.Start(psi)!;
            proc.OutputDataReceived += (_, e) => { if (e.Data != null) _logger.LogDebug("[CVEngine] {Line}", e.Data); };
            proc.ErrorDataReceived  += (_, e) => { if (e.Data != null) _logger.LogError("[CVEngine] {Line}", e.Data); };
            proc.BeginOutputReadLine();
            proc.BeginErrorReadLine();

            _logger.LogInformation("CV Engine launched (PID {Pid}) using {Python}", proc.Id, pythonExe);
            return proc;
        }

        private static string? FindPython()
        {
            // 1. Bundled Python in install directory
            var bundled = Path.Combine(AppContext.BaseDirectory, "python", "python.exe");
            if (File.Exists(bundled)) return bundled;

            // 2. System PATH
            var pathDirs = Environment.GetEnvironmentVariable("PATH")?.Split(';') ?? [];
            foreach (var dir in pathDirs)
            {
                var candidate = Path.Combine(dir.Trim(), "python.exe");
                if (File.Exists(candidate)) return candidate;
            }

            // 3. Microsoft Store Python
            var storeDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Microsoft", "WindowsApps");
            var storePython = Path.Combine(storeDir, "python.exe");
            if (File.Exists(storePython)) return storePython;

            return null;
        }

        private Process? LaunchOverlay()
        {
            // CODEX: Path to the WinUI 3 overlay executable
            var psi = new ProcessStartInfo
            {
                FileName        = $"{AppContext.BaseDirectory}\\MajestyGuard.Overlay.exe",
                UseShellExecute = false,
                CreateNoWindow  = false,
            };

            psi.EnvironmentVariables["MG_OVERLAY_PIPE"] = _config.OverlayPipeName;

            var proc = Process.Start(psi);
            if (proc == null)
            {
                _logger.LogError("Overlay process failed to start");
                return null;
            }
            _logger.LogInformation("Overlay launched (PID {Pid})", proc.Id);
            return proc;
        }

        // ─────────────────────────────────────────────────────────────
        // CHILD PROCESS WATCHDOG + MEMORY TRIM (B4)
        // ─────────────────────────────────────────────────────────────

        private long _lastCvHeartbeatTicks = DateTime.UtcNow.Ticks;  // V2: Interlocked-safe
        private DateTime _lastGcTrim = DateTime.UtcNow;

        private async Task RunChildWatchdogAsync(CancellationToken ct)
        {
            // Also hook heartbeat messages
            if (_cvPipe != null)
            {
                _cvPipe.MessageReceived += msg =>
                {
                    if (msg is HeartbeatMsg) Interlocked.Exchange(ref _lastCvHeartbeatTicks, DateTime.UtcNow.Ticks);
                    return Task.CompletedTask;
                };
            }

            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(30_000, ct);

                // CV Engine watchdog
                if (_cvEngineProcess != null && _cvEngineProcess.HasExited)
                {
                    _logger.LogWarning("CV Engine exited unexpectedly (code {Code}). Relaunching...",
                        _cvEngineProcess.ExitCode);
                    await Task.Delay(5000, ct);
                    _cvEngineProcess = LaunchCvEngine();
                }
                else if ((DateTime.UtcNow - new DateTime(Interlocked.Read(ref _lastCvHeartbeatTicks), DateTimeKind.Utc)).TotalSeconds > 20)
                {
                    _logger.LogWarning("No heartbeat from CV Engine for 20s. Restarting...");
                    try { _cvEngineProcess?.Kill(entireProcessTree: true); } catch { }
                    await Task.Delay(3000, ct);
                    _cvEngineProcess = LaunchCvEngine();
                    Interlocked.Exchange(ref _lastCvHeartbeatTicks, DateTime.UtcNow.Ticks);
                }

                // Overlay watchdog
                if (_overlayProcess != null && _overlayProcess.HasExited)
                {
                    _logger.LogWarning("Overlay exited unexpectedly. Relaunching...");
                    await Task.Delay(3000, ct);
                    _overlayProcess = LaunchOverlay();
                }

                // ── B4: Periodic GC trim during idle states ──────────
                var currentState = _stateMachine.Current;
                var sinceLastGc = (DateTime.UtcNow - _lastGcTrim).TotalSeconds;

                if (currentState == GuardState.Dormant && sinceLastGc >= 60)
                {
                    GC.Collect(2, GCCollectionMode.Aggressive, blocking: false);
                    SetProcessWorkingSetSize(GetCurrentProcess(), (nint)(-1), (nint)(-1));
                    _lastGcTrim = DateTime.UtcNow;
                }
                else if (currentState == GuardState.Unlocked && sinceLastGc >= 120)
                {
                    GC.Collect(1, GCCollectionMode.Optimized, blocking: false);
                    SetProcessWorkingSetSize(GetCurrentProcess(), (nint)(-1), (nint)(-1));
                    _lastGcTrim = DateTime.UtcNow;
                }
            }
        }

        // ─────────────────────────────────────────────────────────────
        // DPAPI HELPER — EMBEDDING LOADING (A8)
        // Service runs as SYSTEM and cannot decrypt DPAPI-CurrentUser
        // data. DpapiHelper.exe runs as the logged-in user via
        // WTSQueryUserToken, decrypts embeddings, outputs JSON to stdout.
        // ─────────────────────────────────────────────────────────────

        private async Task LoadEmbeddingsAsync()
        {
            try
            {
                await Task.Delay(3000);

                var helperPath = Path.Combine(AppContext.BaseDirectory, "MajestyGuard.DpapiHelper.exe");
                if (!File.Exists(helperPath))
                {
                    _logger.LogWarning("DpapiHelper.exe not found at {Path}. Embeddings not loaded.", helperPath);
                    return;
                }

                var sessionId = WTSGetActiveConsoleSessionId();
                if (!WTSQueryUserToken(sessionId, out var userToken))
                {
                    _logger.LogError("WTSQueryUserToken failed (err {Err}). Cannot load embeddings.",
                        Marshal.GetLastWin32Error());
                    return;
                }

                try
                {
                    // Create pipe for child stdout
                    var sa = new SECURITY_ATTRIBUTES
                    {
                        nLength = Marshal.SizeOf<SECURITY_ATTRIBUTES>(),
                        bInheritHandle = true
                    };

                    if (!CreatePipe(out var hReadPipe, out var hWritePipe, ref sa, 0))
                    {
                        _logger.LogError("CreatePipe failed (err {Err})", Marshal.GetLastWin32Error());
                        return;
                    }

                    // Read end must NOT be inherited by child
                    SetHandleInformation(hReadPipe, HANDLE_FLAG_INHERIT, 0);

                    var si = new STARTUPINFOW
                    {
                        cb = Marshal.SizeOf<STARTUPINFOW>(),
                        dwFlags = STARTF_USESTDHANDLES,
                        hStdOutput = hWritePipe,
                        hStdError = hWritePipe,
                        hStdInput = IntPtr.Zero,
                        lpDesktop = "winsta0\\default",
                    };

                    var cmdLine = $"\"{helperPath}\" \"{_config.EmbeddingStorePath}\"";

                    if (!CreateProcessAsUserW(
                            userToken,
                            null,
                            cmdLine,
                            IntPtr.Zero,
                            IntPtr.Zero,
                            true,
                            CREATE_NO_WINDOW,
                            IntPtr.Zero,
                            null,
                            ref si,
                            out var pi))
                    {
                        _logger.LogError("CreateProcessAsUser failed (err {Err})",
                            Marshal.GetLastWin32Error());
                        CloseHandle(hReadPipe);
                        CloseHandle(hWritePipe);
                        return;
                    }

                    // Close write end in parent so ReadFile will see EOF
                    CloseHandle(hWritePipe);

                    // Read stdout from child
                    var output = new System.Text.StringBuilder();
                    var buf = new byte[4096];
                    while (true)
                    {
                        bool ok = ReadFile(hReadPipe, buf, (uint)buf.Length, out uint bytesRead, IntPtr.Zero);
                        if (!ok || bytesRead == 0) break;
                        output.Append(System.Text.Encoding.UTF8.GetString(buf, 0, (int)bytesRead));
                    }

                    // Wait for process exit (max 10s)
                    WaitForSingleObject(pi.hProcess, 10000);

                    CloseHandle(hReadPipe);
                    CloseHandle(pi.hProcess);
                    CloseHandle(pi.hThread);

                    var stdout = output.ToString().Trim();
                    if (string.IsNullOrWhiteSpace(stdout))
                    {
                        _logger.LogWarning("DpapiHelper returned empty output");
                        return;
                    }

                    var embeddings = JsonSerializer.Deserialize<float[][]>(stdout);
                    if (embeddings == null || embeddings.Length == 0)
                    {
                        _logger.LogWarning("No embeddings returned by DpapiHelper");
                        return;
                    }

                    var cmd = JsonSerializer.Serialize(new
                    {
                        cmd = "load_embeddings",
                        embeddings,
                    });

                    if (_cvPipe != null)
                        await _cvPipe.SendRawAsync(cmd);

                    _logger.LogInformation("Loaded {Count} embeddings via DpapiHelper", embeddings.Length);
                    _embeddingsLoaded = true;  // HB-1: ungate PresenceMonitor
                }
                finally
                {
                    CloseHandle(userToken);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to load embeddings via DpapiHelper");
            }
        }

        // ─────────────────────────────────────────────────────────────
        // PROCESS HARDENING
        // Restrict DACL so standard users cannot TerminateProcess() us.
        // SYSTEM and Administrators retain full access.
        // ─────────────────────────────────────────────────────────────
        private void HardenProcess()
        {
            try
            {
                // SDDL: SYSTEM=full, Admins=full, Everyone=read-only (no terminate)
                // Deny PROCESS_TERMINATE (0x0001) and PROCESS_SUSPEND_RESUME (0x0800)
                // to non-privileged callers by granting only minimal rights to WD (World).
                const string sddl =
                    "D:(A;;0x001FFFFF;;;SY)" +  // SYSTEM: full
                    "(A;;0x001FFFFF;;;BA)" +     // Built-in Admins: full
                    "(A;;0x00100000;;;WD)";      // Everyone: SYNCHRONIZE only (no kill/suspend)

                if (!ConvertStringSecurityDescriptorToSecurityDescriptor(
                        sddl, 1, out var pSd, out _))
                {
                    _logger.LogWarning("HardenProcess: SDDL parse failed (err {E})",
                        Marshal.GetLastWin32Error());
                    return;
                }

                GetSecurityDescriptorDacl(pSd, out _, out var pDacl, out _);

                var hProc = GetCurrentProcess();
                bool ok = SetSecurityInfo(
                    hProc, SE_KERNEL_OBJECT, DACL_SECURITY_INFORMATION,
                    IntPtr.Zero, IntPtr.Zero, pDacl, IntPtr.Zero);

                if (ok)
                    _logger.LogInformation("Process DACL hardened — non-admins cannot terminate");
                else
                    _logger.LogWarning("HardenProcess: SetSecurityInfo failed (err {E})",
                        Marshal.GetLastWin32Error());

                // Enable DEP + CFG mitigations (belt-and-suspenders)
                // ProcessExtensionPointDisablePolicy = 7 — blocks injected DLLs via shims
                ulong policy = 1UL;
                SetProcessMitigationPolicy(7, ref policy, sizeof(ulong));
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "HardenProcess failed — continuing without hardening");
            }
        }

        public override async Task StopAsync(CancellationToken ct)
        {
            _logger.LogInformation("MajestyGuard Service stopping");

            // Gracefully terminate child processes
            try { _cvEngineProcess?.Kill(entireProcessTree: true); } catch { }
            try { _overlayProcess?.Kill(entireProcessTree: true);  } catch { }

            _sessionWatcher?.Dispose();
            _cvPipe?.Dispose();
            _overlayPipe?.Dispose();
            _credProvPipe?.Dispose();

            await base.StopAsync(ct);
        }
    }
}
