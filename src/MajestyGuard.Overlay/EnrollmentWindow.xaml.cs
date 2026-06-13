// MajestyGuard.Overlay/EnrollmentWindow.xaml.cs
// Full enrollment wizard logic.
//ha
// FIXES IN THIS VERSION:
//   FIX-CAPTURE: OnFrameArrived now saves _latestFrame copy (thread-safe).
//                SendEnrollCaptureAsync uses BitmapEncoder on _latestFrame
//                instead of CapturePhotoToStreamAsync (which conflicts with
//                the running MediaFrameReader â€” "capture already running" error).
//   FIX-FINALIZE: FinalizeEnrollmentAsync calls enroll_from_jpegs.py (Python
//                subprocess) to extract face embeddings from the captured JPEGs,
//                then saves them via EmbeddingStore with DPAPI-NG.

using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics.Imaging;
using Windows.Media.Capture;
using Windows.Media.Capture.Frames;
using Windows.Media.MediaProperties;
using Windows.Storage.Streams;
using Windows.UI;
using MajestyGuard.Core.IPC;
using MajestyGuard.Core.Security;
using MajestyGuard.Core.Models;
using Microsoft.Extensions.Logging;

namespace MajestyGuard.Overlay
{
    public class EnrollStep : INotifyPropertyChanged
    {
        public string Number  { get; set; } = "";
        public string Label   { get; set; } = "";

        private StepState _state = StepState.Pending;
        public StepState State
        {
            get => _state;
            set { _state = value; OnPropertyChanged(); OnPropertyChanged(nameof(DotFill));
                  OnPropertyChanged(nameof(NumberFore)); OnPropertyChanged(nameof(LabelFore));
                  OnPropertyChanged(nameof(CheckVisible)); OnPropertyChanged(nameof(NumberVisible)); }
        }

        public SolidColorBrush DotFill => State switch
        {
            StepState.Active    => new SolidColorBrush(Color.FromArgb(255, 10, 132, 255)),
            StepState.Complete  => new SolidColorBrush(Color.FromArgb(255, 48, 209, 88)),
            _                   => new SolidColorBrush(Color.FromArgb(255, 30, 30, 34)),
        };
        public SolidColorBrush NumberFore => State == StepState.Active
            ? new SolidColorBrush(Color.FromArgb(255, 255, 255, 255))
            : new SolidColorBrush(Color.FromArgb(255, 80, 80, 88));
        public SolidColorBrush LabelFore => State switch
        {
            StepState.Active    => new SolidColorBrush(Color.FromArgb(255, 255, 255, 255)),
            StepState.Complete  => new SolidColorBrush(Color.FromArgb(255, 48, 209, 88)),
            _                   => new SolidColorBrush(Color.FromArgb(255, 80, 80, 88)),
        };
        public Visibility CheckVisible  => State == StepState.Complete ? Visibility.Visible : Visibility.Collapsed;
        public Visibility NumberVisible => State != StepState.Complete  ? Visibility.Visible : Visibility.Collapsed;

        public event PropertyChangedEventHandler? PropertyChanged;
        private void OnPropertyChanged([CallerMemberName] string? n = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
    }

    public enum StepState { Pending, Active, Complete }

    public sealed partial class EnrollmentWindow : Window
    {
        private readonly ILogger<EnrollmentWindow> _logger;
        private readonly AppConfig _config;
        private MajestyPipeClient? _pipe;
        private CancellationTokenSource _cts = new();

        private int _currentStep = 0;
        private int _currentAngleIndex = 0;

        // Camera
        private MediaCapture? _mediaCapture;
        private MediaFrameReader? _frameReader;
        private readonly SoftwareBitmapSource _previewSource = new();
        private long _lastFrameTicks;
        private readonly Dictionary<string, string> _capturedFramePaths = new();
        private readonly object _frameLock = new();
        private SoftwareBitmap? _latestFrame;  // populated by OnFrameArrived

        private static readonly (string Angle, string Title, string Subtitle, bool Optional)[] Angles =
        [
            ("Front",       "Look straight ahead",    "Keep your face centred in the oval and hold still.",              false),
            ("SlightLeft",  "Turn slightly left",      "Rotate your head about 15Â° to your left.",                       false),
            ("SlightRight", "Turn slightly right",     "Rotate your head about 15Â° to your right.",                      false),
            ("WithGlasses", "Put on your glasses",     "If you wear glasses, put them on now. Otherwise skip this step.", true),
        ];

        public ObservableCollection<EnrollStep> Steps { get; } =
        [
            new() { Number = "1", Label = "Welcome",       State = StepState.Active  },
            new() { Number = "2", Label = "Camera check",  State = StepState.Pending },
            new() { Number = "3", Label = "Face front",    State = StepState.Pending },
            new() { Number = "4", Label = "Turn left",     State = StepState.Pending },
            new() { Number = "5", Label = "Turn right",    State = StepState.Pending },
            new() { Number = "6", Label = "With glasses",  State = StepState.Pending },
        ];

        public EnrollmentWindow(ILogger<EnrollmentWindow> logger, AppConfig config)
        {
            _logger = logger;
            _config = config;
            InitializeComponent();
            CenterOnScreen();
        }

        // â”€â”€ NAVIGATION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

        private void ShowStep(int step)
        {
            StepWelcome.Visibility     = step == 0 ? Visibility.Visible : Visibility.Collapsed;
            StepCameraCheck.Visibility = step == 1 ? Visibility.Visible : Visibility.Collapsed;
            StepCapture.Visibility     = (step >= 2 && step <= 5) ? Visibility.Visible : Visibility.Collapsed;
            StepComplete.Visibility    = step == 6 ? Visibility.Visible : Visibility.Collapsed;
            HideError();
        }

        private void AdvanceStep()
        {
            if (_currentStep < Steps.Count) Steps[_currentStep].State = StepState.Complete;
            _currentStep++;
            if (_currentStep < Steps.Count) Steps[_currentStep].State = StepState.Active;
            ShowStep(_currentStep);
            _ = OnStepEnteredAsync(_currentStep);
        }

        private async Task OnStepEnteredAsync(int step)
        {
            switch (step)
            {
                case 1: await StartCameraPreviewAsync(); break;
                case 2: case 3: case 4: case 5:
                    _currentAngleIndex = step - 2;
                    var (_, title, subtitle, optional) = Angles[_currentAngleIndex];
                    CaptureTitle.Text    = title;
                    CaptureSubtitle.Text = subtitle;
                    BtnSkipAngle.Visibility = optional ? Visibility.Visible : Visibility.Collapsed;
                    BtnCapture.Visibility   = Visibility.Visible;
                    BtnCapture.IsEnabled    = true;
                    BtnRetry.Visibility     = Visibility.Collapsed;
                    ResetOvalToIdle();
                    SetCaptureStatus("Position your face in the oval", "#666666");
                    break;
                case 6:
                    CompleteSubtitle.Text = "Generating your face profile — please wait...";
                    await FinalizeEnrollmentAsync();
                    CompleteSubtitle.Text = "Face recognition is now active. Your PC will lock automatically when you step away.";
                    break;
            }
        }

        // â”€â”€ BUTTON HANDLERS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

        private void BtnGetStarted_Click(object s, RoutedEventArgs e) => AdvanceStep();
        private void BtnCameraOk_Click(object s, RoutedEventArgs e)   => AdvanceStep();
        private void BtnSkipAngle_Click(object s, RoutedEventArgs e)  => AdvanceStep();
        private void BtnFinish_Click(object s, RoutedEventArgs e)     => Close();

        private void BtnSwitchCamera_Click(object s, RoutedEventArgs e)
        {
            _config.CameraDeviceIndex = (_config.CameraDeviceIndex + 1) % 4;
            _ = StartCameraPreviewAsync();
        }

        private async void BtnCapture_Click(object s, RoutedEventArgs e)
        {
            BtnCapture.IsEnabled = false;
            SetCaptureStatus("Capturing...", "#0A84FF");
            CaptureDot.Fill = new SolidColorBrush(Color.FromArgb(255, 10, 132, 255));

            var (angle, _, _, _) = Angles[_currentAngleIndex];
            var success = await SendEnrollCaptureAsync(angle);

            if (success)
            {
                SetOvalReady();
                SetCaptureStatus("Captured âœ“", "#30D158");
                CaptureDot.Fill = new SolidColorBrush(Color.FromArgb(255, 48, 209, 88));
                await Task.Delay(900);
                AdvanceStep();
            }
            else
            {
                BtnCapture.IsEnabled = true;
                BtnRetry.Visibility  = Visibility.Visible;
                ResetOvalToIdle();
                SetCaptureStatus("Try again â€” face not detected clearly", "#FF453A");
                CaptureDot.Fill = new SolidColorBrush(Color.FromArgb(255, 255, 69, 58));
            }
        }

        private void BtnRetry_Click(object s, RoutedEventArgs e)
        {
            BtnRetry.Visibility  = Visibility.Collapsed;
            BtnCapture.IsEnabled = true;
            ResetOvalToIdle();
            SetCaptureStatus("Position your face in the oval", "#666666");
        }

        private async void BtnTest_Click(object s, RoutedEventArgs e)
        {
            CompleteSubtitle.Text = "Testing... look at the camera.";
            await Task.Delay(2000);
            CompleteSubtitle.Text = "Face recognition is now active. Your PC will lock automatically when you step away.";
        }

        // â”€â”€ PIPE (unused in standalone enrollment mode) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

        private async Task ConnectToPipeAsync()
        {
            _pipe = new MajestyPipeClient(_config.CvPipeName, _logger);
            _pipe.MessageReceived += OnPipeMessage;
            await _pipe.ConnectAsync(_cts.Token);
        }

        private Task OnPipeMessage(IpcMessage msg) => Task.CompletedTask;

        // â”€â”€ CAPTURE (FIX-CAPTURE) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        // Uses _latestFrame stored by OnFrameArrived instead of
        // CapturePhotoToStreamAsync (which fails when MediaFrameReader
        // is already running â€” "capture is already running").

        private async Task<bool> SendEnrollCaptureAsync(string angle)
        {
            try
            {
                // Grab a thread-safe copy of the latest frame
                SoftwareBitmap? frameCopy;
                lock (_frameLock)
                {
                    if (_latestFrame == null)
                    {
                        ShowError("No camera frame yet â€” wait a moment for the preview to start.");
                        return false;
                    }
                    // Copy so the reader can keep overwriting _latestFrame
                    frameCopy = SoftwareBitmap.Copy(_latestFrame);
                }

                using (frameCopy)
                {
                    var enrollDir = Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                        "MajestyGuard", "enrollment");
                    Directory.CreateDirectory(enrollDir);
                    var filePath = Path.Combine(enrollDir, $"{angle}.jpg");

                    // Encode to JPEG using BitmapEncoder (no photo-capture conflict)
                    using var memStream = new InMemoryRandomAccessStream();
                    var encoder = await BitmapEncoder.CreateAsync(
                        BitmapEncoder.JpegEncoderId, memStream);

                    // BitmapEncoder requires Bgra8 Premultiplied
                    SoftwareBitmap toEncode;
                    if (frameCopy.BitmapPixelFormat == BitmapPixelFormat.Bgra8 &&
                        frameCopy.BitmapAlphaMode  == BitmapAlphaMode.Premultiplied)
                    {
                        toEncode = frameCopy;
                    }
                    else
                    {
                        toEncode = SoftwareBitmap.Convert(
                            frameCopy, BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied);
                    }

                    encoder.SetSoftwareBitmap(toEncode);
                    await encoder.FlushAsync();
                    if (toEncode != frameCopy) toEncode.Dispose();

                    // Write to disk
                    memStream.Seek(0);
                    using var diskStream = File.Create(filePath);
                    await memStream.AsStreamForRead().CopyToAsync(diskStream);

                    _capturedFramePaths[angle] = filePath;
                    _logger.LogInformation("Captured {Angle} â†’ {Path}", angle, filePath);
                    return true;
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Capture failed for {Angle}", angle);
                ShowError($"Capture failed: {ex.Message}");
                return false;
            }
        }

        // â”€â”€ FINALIZATION (FIX-FINALIZE) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        // Calls enroll_from_jpegs.py (Python) to extract face embeddings
        // from the captured JPEG files, then saves them via EmbeddingStore.

        private async Task FinalizeEnrollmentAsync()
        {
            var sid = System.Security.Principal.WindowsIdentity.GetCurrent().User?.Value;
            if (string.IsNullOrEmpty(sid))
            {
                ShowError("Cannot determine user identity. Run as a normal user account.");
                return;
            }

            if (_capturedFramePaths.Count < 2)
            {
                ShowError($"Only {_capturedFramePaths.Count} angle(s) captured. Need at least 2.");
                return;
            }

            SetCaptureStatus("Generating face embeddings...", "#0A84FF");

            try
            {
                var cvEngineDir = ResolveCvEngineDirectory();
                var pythonExe = ResolvePythonExe(cvEngineDir);
                var scriptPath = Path.Combine(cvEngineDir, "enroll_from_jpegs.py");
                var modelDir = ResolveModelDirectory(cvEngineDir);

                if (!IsInsightFaceModelReady(modelDir))
                {
                    ShowError(
                        "Face models are not ready. Run the staged installer without -SkipModelDownload, " +
                        "or run CVEngine\\download_models.py before enrollment.");
                    _logger.LogError("InsightFace buffalo_l model missing from {ModelDir}", modelDir);
                    return;
                }

                _logger.LogInformation("Running enrollment script: {Script}", scriptPath);

                var psi = new ProcessStartInfo
                {
                    FileName               = pythonExe,
                    RedirectStandardOutput = true,
                    RedirectStandardError  = true,
                    UseShellExecute        = false,
                    CreateNoWindow         = true,
                };
                psi.ArgumentList.Add(scriptPath);
                psi.ArgumentList.Add(modelDir);
                foreach (var jpegPath in _capturedFramePaths.Values)
                    psi.ArgumentList.Add(jpegPath);

                using var proc = Process.Start(psi)
                    ?? throw new InvalidOperationException("Failed to start Python process");

                var stdoutTask = proc.StandardOutput.ReadToEndAsync();
                var stderrTask = proc.StandardError.ReadToEndAsync();
                await Task.WhenAll(stdoutTask, stderrTask);
                await proc.WaitForExitAsync();

                var stdout = stdoutTask.Result;
                var stderr = stderrTask.Result;

                if (!string.IsNullOrWhiteSpace(stderr))
                    _logger.LogDebug("enroll_from_jpegs.py stderr:\n{Stderr}", stderr);

                if (proc.ExitCode != 0)
                {
                    var errMsg = TryParseJsonError(stdout) ?? $"Python exited {proc.ExitCode}";
                    _logger.LogError("Enrollment script failed: {Msg}\nstdout:{Out}", errMsg, stdout);
                    ShowError($"Face processing failed: {errMsg}");
                    return;
                }

                // ONNX runtime prints "Applied providers: [...]" to stdout alongside our JSON.
                // Extract only the line that is valid JSON (starts with '{').
                var jsonLine = stdout
                    .Split('\n')
                    .Select(l => l.Trim())
                    .LastOrDefault(l => l.StartsWith("{") && l.EndsWith("}"));

                if (string.IsNullOrEmpty(jsonLine))
                {
                    _logger.LogError("No JSON found in script output:\n{Out}", stdout);
                    ShowError("Face processing failed — no output from embedding script. Check logs.");
                    return;
                }

                var doc = JsonDocument.Parse(jsonLine);
                var root = doc.RootElement;

                if (!root.TryGetProperty("embeddings", out var embArray) ||
                    embArray.GetArrayLength() < 2)
                {
                    ShowError("Not enough valid face angles. Please retry enrollment.");
                    return;
                }

                // Build EnrollmentRecord
                var embeddings = embArray.EnumerateArray()
                    .Select(arr => new FaceEmbedding
                    {
                        Vector = arr.EnumerateArray().Select(v => v.GetSingle()).ToArray()
                    })
                    .ToArray();

                var record = new EnrollmentRecord
                {
                    UserSid    = sid,
                    Embeddings = embeddings,
                    EnrolledAt = DateTime.UtcNow,
                };

                // Save via EmbeddingStore (DPAPI-NG encrypted)
                var store = new EmbeddingStore(_config.EmbeddingStorePath);
                store.Save(record);

                // Verify the save
                var verify = store.Load();
                if (verify == null || verify.Embeddings.Length < 2 || verify.UserSid != sid)
                {
                    ShowError("Enrollment save verification failed. Please retry.");
                    return;
                }

                // Write enrolled SID to HKLM for Credential Provider
                TryWriteEnrolledSidToRegistry(sid);

                // Commit to config â€” only after verified
                _config.EnrolledUserSid = sid;
                _config.Save();

                _logger.LogInformation(
                    "Enrollment complete â€” {Count} embeddings saved for SID: {Sid}",
                    embeddings.Length, sid);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "FinalizeEnrollmentAsync threw");
                ShowError($"Enrollment failed: {ex.Message}");
            }
        }

        private static string? TryParseJsonError(string json)
        {
            try
            {
                var doc = JsonDocument.Parse(json.Trim());
                if (doc.RootElement.TryGetProperty("error", out var err))
                    return err.GetString();
            }
            catch { }
            return null;
        }

        private string ResolveCvEngineDirectory()
        {
            var candidates = new List<string>();
            var baseDir = AppContext.BaseDirectory;

            candidates.Add(Path.Combine(baseDir, "CVEngine"));
            candidates.Add(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                "MajestyGuard", "CVEngine"));

            for (var dir = new DirectoryInfo(baseDir); dir != null; dir = dir.Parent)
            {
                candidates.Add(Path.Combine(dir.FullName, "CVEngine"));
                candidates.Add(Path.Combine(dir.FullName, "MajestyGuard.CVEngine"));
                candidates.Add(Path.Combine(dir.FullName, "src", "MajestyGuard.CVEngine"));
            }

            foreach (var candidate in candidates.Distinct(StringComparer.OrdinalIgnoreCase))
            {
                if (File.Exists(Path.Combine(candidate, "enroll_from_jpegs.py")))
                    return candidate;
            }

            throw new DirectoryNotFoundException(
                "Could not find MajestyGuard CVEngine. Rebuild with .\\Build.ps1 or reinstall the staged package.");
        }

        private string ResolvePythonExe(string cvEngineDir)
        {
            var candidates = new List<string>();
            var configured = Environment.GetEnvironmentVariable("MG_PYTHON");
            if (!string.IsNullOrWhiteSpace(configured))
                candidates.Add(configured);

            candidates.Add(Path.Combine(cvEngineDir, ".venv", "Scripts", "python.exe"));
            candidates.Add(Path.Combine(AppContext.BaseDirectory, "python", "python.exe"));

            var cvParent = Directory.GetParent(cvEngineDir);
            if (cvParent != null)
                candidates.Add(Path.Combine(cvParent.FullName, "python", "python.exe"));

            for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
            {
                candidates.Add(Path.Combine(dir.FullName, "MajestyGuard.CVEngine", ".venv", "Scripts", "python.exe"));
                candidates.Add(Path.Combine(dir.FullName, "src", "MajestyGuard.CVEngine", ".venv", "Scripts", "python.exe"));
            }

            var pythonExe = candidates
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .FirstOrDefault(File.Exists);

            return pythonExe ?? "python";
        }

        private string ResolveModelDirectory(string cvEngineDir)
        {
            var candidates = new List<string>();
            var configured = Environment.GetEnvironmentVariable("MG_MODEL_DIR");
            if (!string.IsNullOrWhiteSpace(configured))
                candidates.Add(configured);

            if (!string.IsNullOrWhiteSpace(_config.ModelDirectory))
                candidates.Add(_config.ModelDirectory);

            candidates.Add(Path.Combine(AppContext.BaseDirectory, "models"));
            candidates.Add(Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                "MajestyGuard", "models"));

            for (var dir = new DirectoryInfo(cvEngineDir); dir != null; dir = dir.Parent)
                candidates.Add(Path.Combine(dir.FullName, "models"));

            var existing = candidates
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .Where(Directory.Exists)
                .ToList();

            var ready = existing.FirstOrDefault(IsInsightFaceModelReady);
            if (!string.IsNullOrEmpty(ready))
                return ready;

            return existing.FirstOrDefault()
                ?? Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                    "MajestyGuard", "models");
        }

        private static bool IsInsightFaceModelReady(string modelDir)
        {
            var buffaloDir = Path.Combine(modelDir, "models", "buffalo_l");
            return File.Exists(Path.Combine(buffaloDir, "det_10g.onnx")) &&
                   File.Exists(Path.Combine(buffaloDir, "w600k_r50.onnx"));
        }

        private static void TryWriteEnrolledSidToRegistry(string sid)
        {
            try
            {
                using var key = Microsoft.Win32.Registry.LocalMachine.CreateSubKey(
                    @"SOFTWARE\MajestyGuard", writable: true);
                key?.SetValue("EnrolledUserSid", sid,
                    Microsoft.Win32.RegistryValueKind.String);
            }
            catch (Exception ex)
            {
                // Non-fatal â€” CP will fall back to reading from config file
                System.Diagnostics.Debug.WriteLine($"Registry write failed: {ex.Message}");
            }
        }

        // â”€â”€ CAMERA PREVIEW â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

        private async Task StartCameraPreviewAsync()
        {
            CameraStatusText.Text    = "Connecting to camera...";
            BtnCameraOk.IsEnabled    = false;
            NoPreviewText.Visibility = Visibility.Collapsed;

            try
            {
                await StopCameraAsync();

                _mediaCapture = new MediaCapture();
                var settings = new MediaCaptureInitializationSettings
                {
                    StreamingCaptureMode = StreamingCaptureMode.Video,
                    MemoryPreference     = MediaCaptureMemoryPreference.Cpu,
                };

                var devices = await Windows.Devices.Enumeration.DeviceInformation.FindAllAsync(
                    Windows.Devices.Enumeration.DeviceClass.VideoCapture);
                var localDevices = devices
                    .Where(d => d.IsEnabled
                        && !d.Name.Contains("Phone", StringComparison.OrdinalIgnoreCase)
                        && !d.Name.Contains("Virtual", StringComparison.OrdinalIgnoreCase))
                    .ToList();

                if (localDevices.Count == 0)
                    localDevices = devices.ToList();

                if (localDevices.Count == 0)
                {
                    CameraStatusText.Text = "No camera found.";
                    NoPreviewText.Visibility = Visibility.Visible;
                    return;
                }

                var cameraIndex = Math.Clamp(_config.CameraDeviceIndex, 0, localDevices.Count - 1);
                settings.VideoDeviceId = localDevices[cameraIndex].Id;

                await _mediaCapture.InitializeAsync(settings);

                var colorSource = _mediaCapture.FrameSources.Values
                    .FirstOrDefault(s => s.Info.MediaStreamType == MediaStreamType.VideoRecord)
                    ?? _mediaCapture.FrameSources.Values
                    .FirstOrDefault(s => s.Info.MediaStreamType == MediaStreamType.VideoPreview)
                    ?? _mediaCapture.FrameSources.Values.FirstOrDefault();

                if (colorSource == null)
                {
                    CameraStatusText.Text    = "Camera not found.";
                    NoPreviewText.Visibility = Visibility.Visible;
                    return;
                }

                _frameReader = await _mediaCapture.CreateFrameReaderAsync(
                    colorSource, MediaEncodingSubtypes.Bgra8);
                _frameReader.FrameArrived += OnFrameArrived;
                await _frameReader.StartAsync();

                // Wire preview Image source
                PreviewImage.Source  = _previewSource;
                CapturePreview.Source = _previewSource;

                CameraStatusText.Text = "Camera ready";
                BtnCameraOk.IsEnabled = true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Camera preview failed");
                CameraStatusText.Text    = "Camera not found. Check your webcam is connected.";
                NoPreviewText.Visibility = Visibility.Visible;
                ShowError("Camera unavailable. Connect a webcam and try again.");
            }
        }

        private void OnFrameArrived(MediaFrameReader sender, MediaFrameArrivedEventArgs args)
        {
            // Throttle to ~15 FPS
            var now = DateTime.UtcNow.Ticks;
            if (now - Interlocked.Read(ref _lastFrameTicks) < TimeSpan.TicksPerSecond / 15)
                return;
            Interlocked.Exchange(ref _lastFrameTicks, now);

            using var frameRef = sender.TryAcquireLatestFrame();
            var softwareBitmap = frameRef?.VideoMediaFrame?.SoftwareBitmap;
            if (softwareBitmap == null) return;

            var converted = SoftwareBitmap.Convert(
                softwareBitmap, BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied);

            // FIX-CAPTURE: Save a copy for SendEnrollCaptureAsync to use.
            // Must copy before DispatcherQueue async lambda takes ownership.
            SoftwareBitmap captureCopy = SoftwareBitmap.Copy(converted);
            lock (_frameLock)
            {
                _latestFrame?.Dispose();
                _latestFrame = captureCopy;
            }

            bool queued = DispatcherQueue.TryEnqueue(async () =>
            {
                try   { await _previewSource.SetBitmapAsync(converted); }
                finally { converted.Dispose(); }
            });
            if (!queued) converted.Dispose();
        }

        private async Task StopCameraAsync()
        {
            if (_frameReader != null)
            {
                _frameReader.FrameArrived -= OnFrameArrived;
                await _frameReader.StopAsync();
                _frameReader.Dispose();
                _frameReader = null;
            }
            if (_mediaCapture != null)
            {
                _mediaCapture.Dispose();
                _mediaCapture = null;
            }
            lock (_frameLock)
            {
                _latestFrame?.Dispose();
                _latestFrame = null;
            }
        }

        // â”€â”€ UI HELPERS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

        private void SetCaptureStatus(string text, string hexColor)
        {
            DispatcherQueue.TryEnqueue(() =>
            {
                CaptureStatusText.Text = text;
                var parts = hexColor.TrimStart('#');
                var r = Convert.ToByte(parts[0..2], 16);
                var g = Convert.ToByte(parts[2..4], 16);
                var b = Convert.ToByte(parts[4..6], 16);
                CaptureStatusText.Foreground = new SolidColorBrush(Color.FromArgb(255, r, g, b));
            });
        }

        private void SetOvalReady()
        {
            DispatcherQueue.TryEnqueue(() =>
            {
                var sb = (Storyboard)MainGrid.Resources["OvalPulseReady"];
                sb.Begin();
            });
        }

        private void ResetOvalToIdle()
        {
            DispatcherQueue.TryEnqueue(() =>
            {
                var sb = (Storyboard)MainGrid.Resources["OvalPulseIdle"];
                sb.Begin();
            });
        }

        private void ShowError(string message)
        {
            DispatcherQueue.TryEnqueue(() =>
            {
                ErrorText.Text = message;
                ErrorBanner.Visibility = Visibility.Visible;
            });
        }

        private void HideError()
            => DispatcherQueue.TryEnqueue(() => ErrorBanner.Visibility = Visibility.Collapsed);

        private void CenterOnScreen()
        {
            var area = Microsoft.UI.Windowing.DisplayArea.GetFromWindowId(
                AppWindow.Id, Microsoft.UI.Windowing.DisplayAreaFallback.Primary);
            var displayW = area.WorkArea.Width;
            var displayH = area.WorkArea.Height;
            var winW = Math.Clamp((int)(displayW * 0.40), 680, 860);
            var winH = Math.Clamp((int)(winW * (500.0 / 680.0)), 500, 630);
            AppWindow.Resize(new Windows.Graphics.SizeInt32(winW, winH));
            var x = (displayW - winW) / 2;
            var y = (displayH - winH) / 2;
            AppWindow.Move(new Windows.Graphics.PointInt32(x, y));
        }

        private static EnrollmentAngle ParseAngle(string s) => s switch
        {
            "Front"        => EnrollmentAngle.Front,
            "SlightLeft"   => EnrollmentAngle.SlightLeft,
            "SlightRight"  => EnrollmentAngle.SlightRight,
            "WithGlasses"  => EnrollmentAngle.WithGlasses,
            _              => EnrollmentAngle.Front,
        };

        private async void Window_Closed(object sender, WindowEventArgs args)
        {
            _cts.Cancel();
            await StopCameraAsync();
            _pipe?.Dispose();
        }
    }

    public class EnrollResultMsg : IpcMessage
    {
        public EnrollResultMsg() : base("EnrollResult") { }
        public string Angle   { get; init; } = "";
        public bool   Success { get; init; }
        public string Error   { get; init; } = "";
    }
}


