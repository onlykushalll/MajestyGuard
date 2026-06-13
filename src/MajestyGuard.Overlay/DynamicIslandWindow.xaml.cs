// MajestyGuard.Overlay/DynamicIslandWindow.xaml.cs
// Code-behind for the Dynamic Island overlay window.

using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Windowing;
using Microsoft.Graphics.Canvas;
using Microsoft.Graphics.Canvas.Effects;
using Microsoft.Graphics.Canvas.UI.Xaml;
using Windows.Graphics;
using Windows.Graphics.Capture;
using Windows.Graphics.DirectX;
using MajestyGuard.Core;
using MajestyGuard.Core.IPC;
using MajestyGuard.Core.Models;
using Microsoft.Extensions.Logging;

namespace MajestyGuard.Overlay
{
    public sealed partial class DynamicIslandWindow : Window
    {
        private readonly ILogger<DynamicIslandWindow> _logger;
        private readonly AppConfig _config;
        private readonly LockScreenGuard _lockGuard;
        private MajestyPipeClient? _pipeClient;
        private CanvasBitmap? _desktopSnapshot;
        private CanvasDevice? _canvasDevice;
        private float _currentBlurSigma = 0;

        // Fires every 45s to suppress Windows native idle-lock while ours is active
        private readonly DispatcherTimer _idleSuppressTimer;

        private OverlayDisplayState _displayState = OverlayDisplayState.Hidden;

        private nint _hwnd;

        [DllImport("user32.dll")]
        private static extern nint SetWindowLongPtr(nint hWnd, int nIndex, nint dwNewLong);

        [DllImport("user32.dll")]
        private static extern nint GetWindowLongPtr(nint hWnd, int nIndex);

        [DllImport("user32.dll")]
        private static extern bool SetWindowPos(
            nint hWnd, nint hWndInsertAfter,
            int X, int Y, int cx, int cy, uint uFlags);

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(nint hWnd, int nCmdShow);

        [DllImport("user32.dll")]
        private static extern bool SetWindowDisplayAffinity(nint hwnd, uint dwAffinity);

        [DllImport("dwmapi.dll")]
        private static extern int DwmExtendFrameIntoClientArea(nint hwnd, ref MARGINS pMarInset);

        [StructLayout(LayoutKind.Sequential)]
        private struct MARGINS
        {
            public int cxLeftWidth;
            public int cxRightWidth;
            public int cyTopHeight;
            public int cyBottomHeight;
        }

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentProcess();

        [DllImport("kernel32.dll")]
        private static extern bool SetProcessWorkingSetSize(
            IntPtr hProcess, nint dwMinimumWorkingSetSize, nint dwMaximumWorkingSetSize);

        // Low-level keyboard hook for lock state input blocking
        [DllImport("user32.dll", SetLastError = true)]
        private static extern nint SetWindowsHookEx(int idHook, LowLevelKeyboardProc lpfn, nint hMod, uint dwThreadId);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool UnhookWindowsHookEx(nint hhk);

        [DllImport("user32.dll")]
        private static extern nint CallNextHookEx(nint hhk, int nCode, nint wParam, nint lParam);

        [DllImport("kernel32.dll")]
        private static extern nint GetModuleHandle(string? lpModuleName);

        [DllImport("user32.dll")]
        private static extern short GetAsyncKeyState(int vKey);

        private delegate nint LowLevelKeyboardProc(int nCode, nint wParam, nint lParam);

        private const int WH_KEYBOARD_LL = 13;
        private const int WM_KEYDOWN = 0x0100;
        private const int WM_SYSKEYDOWN = 0x0104;
        private const int VK_LWIN = 0x5B;
        private const int VK_RWIN = 0x5C;
        private const int VK_TAB = 0x09;
        private const int VK_ESCAPE = 0x1B;
        private const int VK_F4 = 0x73;
        private const int VK_DELETE = 0x2E;

        private nint _keyboardHook;
        private LowLevelKeyboardProc? _hookProc;
        private bool _blockingInput;

        private const int  GWL_EXSTYLE     = -20;
        private const nint WS_EX_TOPMOST   = 0x00000008;
        private const nint WS_EX_TOOLWINDOW= 0x00000080;
        private const nint WS_EX_TRANSPARENT= 0x00000020;
        private const nint WS_EX_LAYERED   = 0x00080000;
        private static readonly nint HWND_TOPMOST = new(-1);
        private const uint SWP_NOMOVE      = 0x0002;
        private const uint SWP_NOSIZE      = 0x0001;
        private const uint SWP_NOACTIVATE  = 0x0010;
        private const uint WDA_EXCLUDEFROMCAPTURE = 0x00000011;

        public DynamicIslandWindow(
            ILogger<DynamicIslandWindow> logger,
            AppConfig config)
        {
            _logger = logger;
            _config = config;
            _lockGuard = new LockScreenGuard(logger);
            _canvasDevice = CanvasDevice.GetSharedDevice();

            // Suppress Windows own idle-lock every 45s so it doesnt stack with ours
            _idleSuppressTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(45) };
            _idleSuppressTimer.Tick += (_, _) => LockScreenGuard.SuppressWindowsIdleLock();
            _idleSuppressTimer.Start();

            InitializeComponent();
            Activated += OnFirstActivation;
        }

        private void OnFirstActivation(object sender, WindowActivatedEventArgs args)
        {
            Activated -= OnFirstActivation;
            _hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);

            ConfigureWindowStyle();
            StretchToFullScreen();
            DetectDpiScale();
            ConnectToPipeAsync().ConfigureAwait(false);
        }

        // ─────────────────────────────────────────────────────────────
        // WINDOW STYLE SETUP
        // ─────────────────────────────────────────────────────────────

        private void ConfigureWindowStyle()
        {
            var exStyle = GetWindowLongPtr(_hwnd, GWL_EXSTYLE);
            exStyle |= WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT;
            SetWindowLongPtr(_hwnd, GWL_EXSTYLE, exStyle);

            SetWindowPos(_hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);

            SetWindowDisplayAffinity(_hwnd, WDA_EXCLUDEFROMCAPTURE);

            // Make window background transparent via DWM
            var margins = new MARGINS { cxLeftWidth = -1, cxRightWidth = -1, cyTopHeight = -1, cyBottomHeight = -1 };
            DwmExtendFrameIntoClientArea(_hwnd, ref margins);

            // Explicitly block WM_CLOSE/WM_DESTROY regardless of sender IL.
            // MIC blocks these from lower-IL processes automatically,
            // but this adds defense-in-depth against same-IL injection.
            BlockDestructiveMessages();

            _logger.LogInformation("Overlay window configured (HWND: {Hwnd:X})", _hwnd);
        }

        [DllImport("user32.dll")]
        private static extern bool ChangeWindowMessageFilterEx(
            nint hwnd, uint msg, uint action, nint pChangeFilterStruct);

        private const uint MSGFLT_DISALLOW = 2;
        private const uint WM_CLOSE        = 0x0010;
        private const uint WM_SYSCOMMAND   = 0x0112;
        private const uint WM_DESTROY      = 0x0002;

        private void BlockDestructiveMessages()
        {
            ChangeWindowMessageFilterEx(_hwnd, WM_CLOSE,      MSGFLT_DISALLOW, 0);
            ChangeWindowMessageFilterEx(_hwnd, WM_DESTROY,    MSGFLT_DISALLOW, 0);
            ChangeWindowMessageFilterEx(_hwnd, WM_SYSCOMMAND, MSGFLT_DISALLOW, 0);
        }

        private void DetectDpiScale()
        {
            // Read DPI from the window's display
            // WinUI 3: DisplayInformation.GetForCurrentView() not available in WinUI desktop
            // Use Win32 GetDpiForWindow instead
            var dpi = GetDpiForWindow(_hwnd);
            _dpiScale = dpi / 96.0;
            // Clamp: don't scale pill > 1.5x even on 8K displays
            _dpiScale = Math.Clamp(_dpiScale, 1.0, 1.5);
            _logger.LogDebug("Display DPI: {Dpi} → pill scale: {Scale:F2}", dpi, _dpiScale);
        }

        [DllImport("user32.dll")]
        private static extern uint GetDpiForWindow(nint hWnd);

        private void StretchToFullScreen()
        {
            var appWindow = AppWindow.GetFromWindowId(
                Microsoft.UI.Win32Interop.GetWindowIdFromWindow(_hwnd));
            var displayArea = DisplayArea.GetFromWindowId(appWindow.Id, DisplayAreaFallback.Primary);
            var bounds = displayArea.OuterBounds;
            appWindow.MoveAndResize(new RectInt32(0, 0, bounds.Width, bounds.Height));
        }

        // ─────────────────────────────────────────────────────────────
        // PIPE CONNECTION
        // ─────────────────────────────────────────────────────────────

        private async Task ConnectToPipeAsync()
        {
            _pipeClient = new MajestyPipeClient(
                _config.OverlayPipeName, _logger);

            _pipeClient.MessageReceived += OnMessageReceived;

            using var cts = new CancellationTokenSource();
            await _pipeClient.ConnectAsync(cts.Token);
        }

        private Task OnMessageReceived(IpcMessage message)
        {
            if (message is not OverlayCommandMsg cmd) return Task.CompletedTask;
            DispatcherQueue.TryEnqueue(() => ApplyState(cmd));
            return Task.CompletedTask;
        }

        // ─────────────────────────────────────────────────────────────
        // STATE APPLICATION
        // ─────────────────────────────────────────────────────────────

        private void ApplyState(OverlayCommandMsg cmd)
        {
            var prevState = _displayState;
            _displayState = cmd.DisplayState;
            _logger.LogDebug("Overlay state: {Prev} → {State}", prevState, cmd.DisplayState);

            // ── Reset all content panels to hidden ───────────────────
            SearchingContent.Visibility  = Visibility.Collapsed;
            VerifyingContent.Visibility  = Visibility.Collapsed;
            SocialLockContent.Visibility = Visibility.Collapsed;
            LockContent.Visibility       = Visibility.Collapsed;

            // ── Reset pill gradient if coming from SocialLock ────────
            if (prevState == OverlayDisplayState.SocialLock &&
                cmd.DisplayState != OverlayDisplayState.SocialLock)
            {
                ResetPillGradient();
                IslandPill.Margin = new Thickness(0, 16, 0, 0);
            }

            switch (cmd.DisplayState)
            {
                // ── HIDDEN: full reset ────────────────────────────────
                case OverlayDisplayState.Hidden:
                    _lockGuard.Release();
                    SetClickThrough(true);
                    UninstallKeyboardHook();
                    StopAllScanAnimations();
                    BeginStoryboard("CaptionFadeOut");
                    BeginStoryboard("PillDisappear");
                    BeginStoryboard("BlurFadeOut");
                    BeginStoryboard("BlackOverlayOut");
                    BeginStoryboard("FrostOut");
                    ScheduleMemoryTrim();
                    break;

                // ── SEARCHING: wide pill, camera dot pulse, blur fades in
                case OverlayDisplayState.Searching:
                    SetClickThrough(true);
                    SearchingContent.Visibility = Visibility.Visible;
                    SearchingContent.Opacity = 1;
                    StopAllScanAnimations();
                    BeginStoryboard("CaptionFadeOut");
                    // Pill: wide pill shape (morphs back if coming from square)
                    AnimatePillTo(380, 46, 23);
                    if (prevState == OverlayDisplayState.Hidden)
                        BeginStoryboard("PillAppear");
                    BeginStoryboard("BlurFadeIn");
                    BeginStoryboard("CameraDotPulse");
                    _ = CaptureAndBlurDesktop(cmd.BlurAmount);
                    break;

                // ── VERIFYING: MORPHS TO SQUARE — Apple FaceID behaviour
                // The pill changes shape entirely: wide pill → rounded square.
                // FaceID icon fills the square. Caption floats below.
                // Scan rings orbit around the square. Blue scan line sweeps.
                case OverlayDisplayState.Verifying:
                    SetClickThrough(true);
                    StopStoryboard("CameraDotPulse");
                    BeginStoryboard("CaptionFadeOut"); // reset first

                    // Show content AFTER morph animation completes (180ms delay)
                    VerifyingContent.Visibility = Visibility.Visible;
                    VerifyingContent.Opacity = 0; // fades in via FaceIdContentIn

                    // ── KEY: Morph pill to SQUARE ─────────────────────
                    // base 108×108, radius 24 → looks like Apple's FaceID island
                    AnimatePillToSquare(108, 24);

                    // Delayed: start icon + scan line + caption after morph
                    DelayedAction(180, () =>
                    {
                        BeginStoryboard("FaceIdContentIn");
                        BeginStoryboard("ScanLineSweep");
                        BeginStoryboard("CaptionFadeIn");
                        BeginStoryboard("ScanRingsIn");
                        BeginStoryboard("ScanRingRotation");
                    });

                    SetGlowColor("#0A84FF");
                    BeginStoryboard("PillGlowPulse"); // subtle blue breathing glow
                    break;

                // ── UNLOCKED: green flash on whatever shape, then collapse
                case OverlayDisplayState.Unlocked:
                    _lockGuard.Release();
                    SetClickThrough(true);
                    UninstallKeyboardHook();
                    StopAllScanAnimations();
                    BeginStoryboard("CaptionFadeOut");
                    SetGlowColor("#30D158");
                    BeginStoryboard("UnlockPulse");
                    BeginStoryboard("ScanRingsOut");
                    BeginStoryboard("BlurFadeOut");
                    BeginStoryboard("BlackOverlayOut");

                    // Morph back to wide pill then disappear
                    DelayedAction(120, () => AnimatePillTo(380, 46, 23));
                    DelayedAction(900, () => BeginStoryboard("PillDisappear"));
                    break;

                // ── SOCIAL LOCK: full-width amber bar
                case OverlayDisplayState.SocialLock:
                    SetClickThrough(true);
                    StopAllScanAnimations();
                    BeginStoryboard("CaptionFadeOut");
                    SocialLockContent.Visibility = Visibility.Visible;
                    SocialLockContent.Opacity = 1;
                    AnimatePillTo(RootGrid.ActualWidth, 56, 0);
                    IslandPill.Margin = new Thickness(0);
                    SetPillGradient("#1A0F00", "#0F0900");
                    SetGlowColor("#FF9F0A");
                    BeginStoryboard("FrostIn");
                    BeginStoryboard("PillAppear");
                    break;

                // ── LOCK (Inactivity or Hostile): wide pill over black screen
                // DOOR LOCK: visual only. Background tasks run unaffected.
                case OverlayDisplayState.HostileLock:
                case OverlayDisplayState.InactivityLock:
                    SetClickThrough(false);
                    InstallKeyboardHook();
                    _lockGuard.Engage();
                    StopAllScanAnimations();
                    BeginStoryboard("CaptionFadeOut");
                    LockContent.Visibility = Visibility.Visible;
                    LockContent.Opacity = 1;
                    // Wide pill at top, fully visible against black overlay
                    AnimatePillTo(420, 54, 27);
                    IslandPill.Margin = new Thickness(0, 16, 0, 0);
                    SetGlowColor("#FF453A"); // Red tint for lock state
                    BeginStoryboard("BlackOverlayIn");
                    BeginStoryboard("PillAppear");
                    break;
            }
        }

        // ─────────────────────────────────────────────────────────────
        // SQUARE MORPH (Verifying state)
        // Animates pill from any shape → a square with matching H = W
        // Uses BackEase spring to feel physical like Apple's animation
        // ─────────────────────────────────────────────────────────────
        private void AnimatePillToSquare(double baseSize, double radius)
        {
            var size = Math.Min(baseSize * _dpiScale, 160); // cap for 4K
            radius  = radius * Math.Min(_dpiScale, 1.3);

            var duration = TimeSpan.FromMilliseconds(320);
            var easing   = new BackEase { EasingMode = EasingMode.EaseOut, Amplitude = 0.15 };

            var wAnim = new DoubleAnimation
                { To = size, Duration = new Duration(duration), EasingFunction = easing };
            Storyboard.SetTarget(wAnim, IslandPill);
            Storyboard.SetTargetProperty(wAnim, "Width");

            var hAnim = new DoubleAnimation
                { To = size, Duration = new Duration(duration), EasingFunction = easing };
            Storyboard.SetTarget(hAnim, IslandPill);
            Storyboard.SetTargetProperty(hAnim, "Height");

            var sb = new Storyboard();
            sb.Children.Add(wAnim);
            sb.Children.Add(hAnim);
            sb.Begin();

            IslandPill.CornerRadius = new CornerRadius(radius);
            // Update caption vertical position to sit just below the square
            FloatingCaption.Margin = new Thickness(0, size + 24, 0, 0);
        }

        // ─────────────────────────────────────────────────────────────
        // HELPERS
        // ─────────────────────────────────────────────────────────────

        private void StopAllScanAnimations()
        {
            StopStoryboard("ScanRingRotation");
            StopStoryboard("ScanLineSweep");
            StopStoryboard("CameraDotPulse");
            // Fade out scan line
            if (ScanLine.Opacity > 0)
            {
                var fade = new DoubleAnimation { To = 0, Duration = new Duration(TimeSpan.FromMilliseconds(200)) };
                Storyboard.SetTarget(fade, ScanLine);
                Storyboard.SetTargetProperty(fade, "Opacity");
                var sb = new Storyboard();
                sb.Children.Add(fade);
                sb.Begin();
            }
        }

        private void ResetPillGradient()
        {
            IslandPill.Background = new LinearGradientBrush
            {
                StartPoint = new Windows.Foundation.Point(0, 0),
                EndPoint   = new Windows.Foundation.Point(0, 1),
                GradientStops =
                {
                    new GradientStop { Color = ParseHexColor("#16181A"), Offset = 0.0 },
                    new GradientStop { Color = ParseHexColor("#0E1012"), Offset = 1.0 },
                }
            };
            IslandPill.CornerRadius = new CornerRadius(23);
        }

        /// Runs action on UI thread after delayMs milliseconds.
        private void DelayedAction(int delayMs, Action action)
        {
            var t = DispatcherQueue.CreateTimer();
            t.Interval    = TimeSpan.FromMilliseconds(delayMs);
            t.IsRepeating = false;
            t.Tick += (_, _) =>
            {
                action();
                t.Stop();
            };
            t.Start();
        }

        // ─────────────────────────────────────────────────────────────
        // CLICK-THROUGH TOGGLE
        // ─────────────────────────────────────────────────────────────

        private void SetClickThrough(bool clickThrough)
        {
            var exStyle = GetWindowLongPtr(_hwnd, GWL_EXSTYLE);

            if (clickThrough)
                exStyle |= WS_EX_TRANSPARENT;
            else
                exStyle &= ~WS_EX_TRANSPARENT;

            SetWindowLongPtr(_hwnd, GWL_EXSTYLE, exStyle);
        }

        // ─────────────────────────────────────────────────────────────
        // STORYBOARD HELPERS
        // ─────────────────────────────────────────────────────────────

        private void BeginStoryboard(string key)
        {
            if (RootGrid.Resources.TryGetValue(key, out var resource) && resource is Storyboard sb)
                sb.Begin();
        }

        private void StopStoryboard(string key)
        {
            if (RootGrid.Resources.TryGetValue(key, out var resource) && resource is Storyboard sb)
                sb.Stop();
        }

        // ─────────────────────────────────────────────────────────────
        // PILL DIMENSION ANIMATION (runtime values, not XAML-defined)
        // ─────────────────────────────────────────────────────────────

        // Scale factor based on display DPI — pill stays proportionally sized on 4K
        private double _dpiScale = 1.0;

        private void AnimatePillTo(double width, double height, double radius)
        {
            // Apply DPI scaling for high-resolution displays
            // 1.0 = 96dpi (1080p), 1.25 = 120dpi, 1.5 = 144dpi (4K)
            // Cap at 1.5x so pill doesn't become enormous on 4K
            width  = Math.Min(width  * _dpiScale, RootGrid.ActualWidth  * 0.45);
            height = Math.Min(height * _dpiScale, 120);
            radius = radius * Math.Min(_dpiScale, 1.3);

            var duration = TimeSpan.FromMilliseconds(280);
            var easing = new CubicEase { EasingMode = EasingMode.EaseOut };

            var widthAnim = new DoubleAnimation
            {
                To = width, Duration = new Duration(duration),
                EasingFunction = easing,
            };
            Storyboard.SetTarget(widthAnim, IslandPill);
            Storyboard.SetTargetProperty(widthAnim, "Width");

            var heightAnim = new DoubleAnimation
            {
                To = height, Duration = new Duration(duration),
                EasingFunction = easing,
            };
            Storyboard.SetTarget(heightAnim, IslandPill);
            Storyboard.SetTargetProperty(heightAnim, "Height");

            var sb = new Storyboard();
            sb.Children.Add(widthAnim);
            sb.Children.Add(heightAnim);
            sb.Begin();

            IslandPill.CornerRadius = new CornerRadius(radius);
        }

        // ─────────────────────────────────────────────────────────────
        // COLOR HELPERS
        // ─────────────────────────────────────────────────────────────

        private void SetGlowColor(string hex)
        {
            GlowBrush.Color = ParseHexColor(hex);
        }

        private void SetPillGradient(string topHex, string bottomHex)
        {
            IslandPill.Background = new LinearGradientBrush
            {
                StartPoint = new Windows.Foundation.Point(0, 0),
                EndPoint = new Windows.Foundation.Point(0, 1),
                GradientStops =
                {
                    new GradientStop { Color = ParseHexColor(topHex), Offset = 0.0 },
                    new GradientStop { Color = ParseHexColor(bottomHex), Offset = 1.0 },
                }
            };
        }

        private static Windows.UI.Color ParseHexColor(string hex)
        {
            hex = hex.TrimStart('#');
            byte a = 255, r, g, b;
            if (hex.Length == 8)
            {
                a = Convert.ToByte(hex[..2], 16);
                r = Convert.ToByte(hex[2..4], 16);
                g = Convert.ToByte(hex[4..6], 16);
                b = Convert.ToByte(hex[6..8], 16);
            }
            else
            {
                r = Convert.ToByte(hex[..2], 16);
                g = Convert.ToByte(hex[2..4], 16);
                b = Convert.ToByte(hex[4..6], 16);
            }
            return Windows.UI.Color.FromArgb(a, r, g, b);
        }

        // ─────────────────────────────────────────────────────────────
        // WIN2D BLUR RENDERING
        // ─────────────────────────────────────────────────────────────

        private void BlurCanvas_Draw(
            CanvasControl sender,
            Microsoft.Graphics.Canvas.UI.Xaml.CanvasDrawEventArgs args)
        {
            if (_desktopSnapshot == null || _currentBlurSigma <= 0) return;

            var blurEffect = new GaussianBlurEffect
            {
                Source       = _desktopSnapshot,
                BlurAmount   = _currentBlurSigma,
                BorderMode   = EffectBorderMode.Hard,
                Optimization = EffectOptimization.Speed,
            };

            args.DrawingSession.DrawImage(blurEffect, 0, 0);
        }

        private async Task CaptureAndBlurDesktop(double blurAmount)
        {
            _currentBlurSigma = (float)(blurAmount * 40.0);

            try
            {
                if (!GraphicsCaptureSession.IsSupported())
                {
                    _logger.LogWarning("Windows Graphics Capture not supported on this system");
                    return;
                }

                var appWindow = AppWindow.GetFromWindowId(
                    Microsoft.UI.Win32Interop.GetWindowIdFromWindow(_hwnd));
                var displayArea = DisplayArea.GetFromWindowId(appWindow.Id, DisplayAreaFallback.Primary);
                var item = GraphicsCaptureItem.TryCreateFromDisplayId(
                    new Windows.Graphics.DisplayId { Value = displayArea.DisplayId.Value });

                if (item == null)
                {
                    _logger.LogWarning("Failed to create capture item for display");
                    return;
                }

                if (_canvasDevice == null) return;

                var framePool = Direct3D11CaptureFramePool.CreateFreeThreaded(
                    _canvasDevice,
                    DirectXPixelFormat.B8G8R8A8UIntNormalized,
                    1,
                    item.Size);

                var session = framePool.CreateCaptureSession(item);
                session.IsBorderRequired = false;
                session.IsCursorCaptureEnabled = false;

                var tcs = new TaskCompletionSource<bool>();

                framePool.FrameArrived += (pool, _) =>
                {
                    using var frame = pool.TryGetNextFrame();
                    if (frame != null)
                    {
                        _desktopSnapshot = CanvasBitmap.CreateFromDirect3D11Surface(
                            _canvasDevice!, frame.Surface);
                    }
                    session.Dispose();
                    pool.Dispose();
                    tcs.TrySetResult(true);
                };

                session.StartCapture();

                // Wait up to 2s for capture
                await Task.WhenAny(tcs.Task, Task.Delay(2000));

                DispatcherQueue.TryEnqueue(() => BlurCanvas.Invalidate());
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Desktop capture failed");
            }
        }

        // ─────────────────────────────────────────────────────────────
        // MEMORY OPTIMIZATION (B2)
        // ─────────────────────────────────────────────────────────────

        private void ScheduleMemoryTrim()
        {
            var timer = DispatcherQueue.CreateTimer();
            timer.Interval = TimeSpan.FromSeconds(5);
            timer.IsRepeating = false;
            timer.Tick += (_, _) =>
            {
                _desktopSnapshot?.Dispose();
                _desktopSnapshot = null;
                GC.Collect(2, GCCollectionMode.Aggressive, blocking: false);
                SetProcessWorkingSetSize(GetCurrentProcess(), (nint)(-1), (nint)(-1));
            };
            timer.Start();
        }

        // ─────────────────────────────────────────────────────────────
        // INPUT BLOCKING (Lock states — unbypassable)
        // Blocks Win key, Alt+Tab, Alt+F4, Ctrl+Alt+Del combos
        // ─────────────────────────────────────────────────────────────

        private void InstallKeyboardHook()
        {
            if (_keyboardHook != 0) return;
            _hookProc = KeyboardHookCallback;
            _blockingInput = true;
            var hModule = GetModuleHandle(null);
            _keyboardHook = SetWindowsHookEx(WH_KEYBOARD_LL, _hookProc, hModule, 0);
            _logger.LogInformation("Keyboard hook installed for lock state");
        }

        private void UninstallKeyboardHook()
        {
            _blockingInput = false;
            if (_keyboardHook != 0)
            {
                UnhookWindowsHookEx(_keyboardHook);
                _keyboardHook = 0;
                _hookProc = null;
                _logger.LogInformation("Keyboard hook removed");
            }
        }

        private nint KeyboardHookCallback(int nCode, nint wParam, nint lParam)
        {
            if (nCode >= 0 && _blockingInput &&
                (wParam == WM_KEYDOWN || wParam == WM_SYSKEYDOWN))
            {
                int vkCode = System.Runtime.InteropServices.Marshal.ReadInt32(lParam);

                // Block: Win key, Alt+Tab, Alt+F4, Ctrl+Esc
                if (vkCode == VK_LWIN || vkCode == VK_RWIN)
                    return 1;
                if (vkCode == VK_TAB && (wParam == WM_SYSKEYDOWN))
                    return 1;  // Alt+Tab
                if (vkCode == VK_F4 && (wParam == WM_SYSKEYDOWN))
                    return 1;  // Alt+F4
                if (vkCode == VK_ESCAPE)
                    return 1;  // Esc and Ctrl+Esc

                // Ctrl+Shift+Esc → Task Manager. Block during lock states.
                // Task Manager at elevated IL can terminate our overlay process.
                // VK_ESCAPE = 0x1B, Ctrl = 0x11, Shift = 0x10
                bool ctrl  = (GetAsyncKeyState(0x11) & 0x8000) != 0;
                bool shift = (GetAsyncKeyState(0x10) & 0x8000) != 0;
                if (ctrl && shift && vkCode == VK_ESCAPE)
                    return 1;

                // Also block Delete key when Ctrl+Alt+Delete shortcut chain is active
                // (blocks post-SAS interactions — Ctrl+Alt+Del itself cannot be blocked)
                bool alt = (GetAsyncKeyState(0x12) & 0x8000) != 0;
                if (ctrl && alt && vkCode == VK_DELETE)
                    return 1;
            }
            return CallNextHookEx(_keyboardHook, nCode, wParam, lParam);
        }
    }
}
