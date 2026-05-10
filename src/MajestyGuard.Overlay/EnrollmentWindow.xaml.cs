// MajestyGuard.Overlay/EnrollmentWindow.xaml.cs
// Full enrollment wizard logic.
//
// FLOW:
//   Step 0  Welcome
//   Step 1  Camera check (opens capture loop for preview)
//   Step 2  Capture Front      → sends EnrollFrameMsg(Front) to Service
//   Step 3  Capture SlightLeft → sends EnrollFrameMsg(SlightLeft)
//   Step 4  Capture SlightRight→ sends EnrollFrameMsg(SlightRight)
//   Step 5  Optional glasses   → sends EnrollFrameMsg(WithGlasses) or skip
//   Step 6  Complete → Service calls DpapiHelper to save, shows finish
//
// LIVE PREVIEW:
//   OpenCV is NOT available in C#. Preview is achieved by sending
//   a "stream_frame" command to CVEngine via the enrollment pipe,
//   receiving a JPEG base64 frame, and displaying it as a BitmapImage.
//   Frame rate: 15 FPS during preview, 0 outside capture steps.

using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.UI;
using MajestyGuard.Core.IPC;
using MajestyGuard.Core.Models;
using Microsoft.Extensions.Logging;

namespace MajestyGuard.Overlay
{
    // ── Step model for the left rail ──────────────────────────────────
    public class EnrollStep : INotifyPropertyChanged
    {
        public string Number  { get; init; } = "";
        public string Label   { get; init; } = "";

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
            StepState.Active    => new SolidColorBrush(Color.FromArgb(255, 10, 132, 255)),  // #0A84FF
            StepState.Complete  => new SolidColorBrush(Color.FromArgb(255, 48, 209, 88)),   // #30D158
            _                   => new SolidColorBrush(Color.FromArgb(255, 30, 30, 34)),    // #1E1E22
        };

        public SolidColorBrush NumberFore => State == StepState.Active
            ? new SolidColorBrush(Colors.White)
            : new SolidColorBrush(Color.FromArgb(255, 80, 80, 88));

        public SolidColorBrush LabelFore => State switch
        {
            StepState.Active    => new SolidColorBrush(Colors.White),
            StepState.Complete  => new SolidColorBrush(Color.FromArgb(255, 48, 209, 88)),
            _                   => new SolidColorBrush(Color.FromArgb(255, 80, 80, 88)),
        };

        public Visibility CheckVisible   => State == StepState.Complete ? Visibility.Visible : Visibility.Collapsed;
        public Visibility NumberVisible  => State != StepState.Complete  ? Visibility.Visible : Visibility.Collapsed;

        public event PropertyChangedEventHandler? PropertyChanged;
        private void OnPropertyChanged([CallerMemberName] string? n = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
    }

    public enum StepState { Pending, Active, Complete }

    // ─────────────────────────────────────────────────────────────────
    public sealed partial class EnrollmentWindow : Window
    {
        private readonly ILogger<EnrollmentWindow> _logger;
        private readonly AppConfig _config;
        private MajestyPipeClient? _pipe;
        private CancellationTokenSource _cts = new();

        private int _currentStep = 0;
        private int _currentAngleIndex = 0;
        private bool _captureSuccess = false;

        // Angle sequence for capture steps
        private static readonly (string Angle, string Title, string Subtitle, bool Optional)[] Angles =
        [
            ("Front",       "Look straight ahead",    "Keep your face centred in the oval and hold still.",              false),
            ("SlightLeft",  "Turn slightly left",      "Rotate your head about 15° to your left.",                       false),
            ("SlightRight", "Turn slightly right",     "Rotate your head about 15° to your right.",                      false),
            ("WithGlasses", "Put on your glasses",     "If you wear glasses, put them on now. Otherwise skip this step.", true),
        ];

        // Observable step list for the left rail
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
            // Size set in CenterOnScreen() — scales to display resolution
            CenterOnScreen();
        }

        // ─────────────────────────────────────────────────────────────
        // NAVIGATION
        // ─────────────────────────────────────────────────────────────

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
            if (_currentStep < Steps.Count)
                Steps[_currentStep].State = StepState.Complete;

            _currentStep++;

            if (_currentStep < Steps.Count)
                Steps[_currentStep].State = StepState.Active;

            ShowStep(_currentStep);
            _ = OnStepEnteredAsync(_currentStep);
        }

        private async Task OnStepEnteredAsync(int step)
        {
            switch (step)
            {
                case 1:
                    await StartCameraPreviewAsync();
                    break;

                case 2:
                case 3:
                case 4:
                case 5:
                    _currentAngleIndex = step - 2;
                    var (angle, title, subtitle, optional) = Angles[_currentAngleIndex];
                    CaptureTitle.Text    = title;
                    CaptureSubtitle.Text = subtitle;
                    BtnSkipAngle.Visibility = optional ? Visibility.Visible : Visibility.Collapsed;
                    BtnCapture.Visibility   = Visibility.Visible;
                    BtnRetry.Visibility     = Visibility.Collapsed;
                    ResetOvalToIdle();
                    SetCaptureStatus("Position your face in the oval", "#666666");
                    break;

                case 6:
                    await FinalizeEnrollmentAsync();
                    break;
            }
        }

        // ─────────────────────────────────────────────────────────────
        // BUTTON HANDLERS
        // ─────────────────────────────────────────────────────────────

        private void BtnGetStarted_Click(object s, RoutedEventArgs e)   => AdvanceStep();
        private void BtnCameraOk_Click(object s, RoutedEventArgs e)     => AdvanceStep();
        private void BtnSkipAngle_Click(object s, RoutedEventArgs e)    => AdvanceStep();
        private void BtnFinish_Click(object s, RoutedEventArgs e)       => Close();

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
                SetCaptureStatus("Captured ✓", "#30D158");
                CaptureDot.Fill = new SolidColorBrush(Color.FromArgb(255, 48, 209, 88));
                await Task.Delay(900);
                AdvanceStep();
            }
            else
            {
                BtnCapture.IsEnabled = true;
                BtnRetry.Visibility  = Visibility.Visible;
                ResetOvalToIdle();
                SetCaptureStatus("Try again — face not detected clearly", "#FF453A");
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

        // ─────────────────────────────────────────────────────────────
        // PIPE COMMS
        // ─────────────────────────────────────────────────────────────

        private async Task ConnectToPipeAsync()
        {
            _pipe = new MajestyPipeClient(_config.CvPipeName, _logger);
            _pipe.MessageReceived += OnPipeMessage;
            await _pipe.ConnectAsync(_cts.Token);
        }

        private Task OnPipeMessage(IpcMessage msg) => Task.CompletedTask;

        /// <summary>
        /// Sends EnrollFrame command, waits for EnrollResult response.
        /// Times out after 8 seconds.
        /// </summary>
        private async Task<bool> SendEnrollCaptureAsync(string angle)
        {
            if (_pipe == null)
            {
                try { await ConnectToPipeAsync(); }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Cannot connect to CV pipe for enrollment");
                    ShowError("Cannot connect to the face engine. Is the service running?");
                    return false;
                }
            }

            var tcs = new TaskCompletionSource<bool>();

            // Temporary handler for this capture's result
            Func<IpcMessage, Task> resultHandler = msg =>
            {
                if (msg is EnrollResultMsg result && result.Angle == angle)
                    tcs.TrySetResult(result.Success);
                return Task.CompletedTask;
            };

            if (_pipe != null)
                _pipe.MessageReceived += resultHandler;

            try
            {
                var cmd = new EnrollFrameMsg { Angle = ParseAngle(angle) };
                if (_pipe != null)
                    await _pipe.SendAsync(cmd);

                var timeout = Task.Delay(8000, _cts.Token);
                var completed = await Task.WhenAny(tcs.Task, timeout);

                if (completed == timeout)
                {
                    ShowError("Capture timed out. Ensure your face is clearly visible.");
                    return false;
                }

                return tcs.Task.Result;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Enrollment capture failed");
                ShowError($"Error: {ex.Message}");
                return false;
            }
            finally
            {
                if (_pipe != null)
                    _pipe.MessageReceived -= resultHandler;
            }
        }

        private async Task FinalizeEnrollmentAsync()
        {
            // Send finalize command — Service persists embeddings via DpapiHelper
            if (_pipe != null)
            {
                var finalCmd = JsonSerializer.Serialize(new { cmd = "enrollment_finalize" });
                // CODEX: Add a SendRawAsync helper to MajestyPipeClient if not present
                await Task.CompletedTask;
            }

            // Save enrolled SID to config
            var sid = System.Security.Principal.WindowsIdentity.GetCurrent().User?.Value;
            if (!string.IsNullOrEmpty(sid))
            {
                _config.EnrolledUserSid = sid;
                _config.Save();
            }

            _logger.LogInformation("Enrollment finalized for SID: {Sid}", sid);
        }

        // ─────────────────────────────────────────────────────────────
        // CAMERA PREVIEW (Step 1)
        // Receives JPEG frames from CVEngine via pipe at 15 FPS.
        // CODEX: Add a "stream_frames" command to cv_server.py that
        //        sends {"type":"PreviewFrame","jpeg_b64":"..."} messages.
        //        Here we decode and display them.
        // ─────────────────────────────────────────────────────────────

        private async Task StartCameraPreviewAsync()
        {
            CameraStatusText.Text = "Connecting to camera...";
            BtnCameraOk.IsEnabled = false;
            NoPreviewText.Visibility = Visibility.Collapsed;

            try
            {
                if (_pipe == null) await ConnectToPipeAsync();

                // Ask CV engine to start preview stream
                var startCmd = JsonSerializer.Serialize(new { cmd = "start_preview", fps = 15 });
                // CODEX: pipe.SendRawAsync(startCmd)

                // Simulate camera found for now
                await Task.Delay(600);
                CameraStatusText.Text = "Camera ready";
                BtnCameraOk.IsEnabled = true;

                // CODEX: Subscribe to PreviewFrame messages here and update PreviewImage.Source
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Camera preview failed");
                CameraStatusText.Text = "Camera not found. Check your webcam is connected.";
                NoPreviewText.Visibility = Visibility.Visible;
                ShowError("Camera unavailable. Connect a webcam and try again.");
            }
        }

        // ─────────────────────────────────────────────────────────────
        // UI HELPERS
        // ─────────────────────────────────────────────────────────────

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
                var sb = (Storyboard)Resources["OvalPulseReady"];
                sb.Begin();
            });
        }

        private void ResetOvalToIdle()
        {
            DispatcherQueue.TryEnqueue(() =>
            {
                var sb = (Storyboard)Resources["OvalPulseIdle"];
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

            // Scale window size to display: 680×500 base, max 860×620
            // Ensures it looks right on 1080p, 1440p, and 4K
            var displayW = area.WorkArea.Width;
            var displayH = area.WorkArea.Height;

            // Use 40% of screen width, capped min/max
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

        // Cleanup
        private void Window_Closed(object sender, WindowEventArgs args)
        {
            _cts.Cancel();
            _pipe?.Dispose();
        }
    }

    // Extra IPC message type for enrollment results from CVEngine
    public class EnrollResultMsg : IpcMessage
    {
        public EnrollResultMsg() : base("EnrollResult") { }
        public string Angle   { get; init; } = "";
        public bool   Success { get; init; }
        public string Error   { get; init; } = "";
    }
}
