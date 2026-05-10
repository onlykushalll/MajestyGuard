using System;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Extensions.Logging;

namespace MajestyGuard.Service
{
    public sealed class SessionWatcher : IDisposable
    {
        private readonly ILogger _logger;
        private readonly Action _onSessionLock;
        private readonly Action _onSessionUnlock;
        private readonly Action? _onSuspend;
        private readonly Action? _onResume;
        private Thread? _thread;
        private uint _threadId;
        private WndProcDelegate? _wndProcDelegate;

        private const int NOTIFY_FOR_THIS_SESSION = 0;
        private const uint WM_WTSSESSION_CHANGE = 0x02B1;
        private const uint WM_POWERBROADCAST = 0x0218;
        private const int WTS_SESSION_LOCK = 0x7;
        private const int WTS_SESSION_UNLOCK = 0x8;
        private const int PBT_APMSUSPEND = 0x0004;
        private const int PBT_APMRESUMEAUTOMATIC = 0x0012;
        private const int PBT_APMRESUMESUSPEND = 0x0007;
        private const uint WM_QUIT = 0x0012;
        private static readonly IntPtr HWND_MESSAGE = new(-3);

        [DllImport("wtsapi32.dll", SetLastError = true)]
        private static extern bool WTSRegisterSessionNotification(IntPtr hWnd, int dwFlags);

        [DllImport("wtsapi32.dll", SetLastError = true)]
        private static extern bool WTSUnRegisterSessionNotification(IntPtr hWnd);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern ushort RegisterClassW(ref WNDCLASS wc);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr CreateWindowExW(
            uint dwExStyle, string lpClassName, string? lpWindowName,
            uint dwStyle, int x, int y, int nWidth, int nHeight,
            IntPtr hWndParent, IntPtr hMenu, IntPtr hInstance, IntPtr lpParam);

        [DllImport("user32.dll")]
        private static extern bool DestroyWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool GetMessageW(out MSG msg, IntPtr hWnd, uint wMsgFilterMin, uint wMsgFilterMax);

        [DllImport("user32.dll")]
        private static extern IntPtr DefWindowProcW(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool PostThreadMessageW(uint threadId, uint msg, IntPtr wParam, IntPtr lParam);

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetModuleHandleW(string? lpModuleName);

        private delegate IntPtr WndProcDelegate(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct WNDCLASS
        {
            public uint style;
            public WndProcDelegate lpfnWndProc;
            public int cbClsExtra;
            public int cbWndExtra;
            public IntPtr hInstance;
            public IntPtr hIcon;
            public IntPtr hCursor;
            public IntPtr hbrBackground;
            public string? lpszMenuName;
            public string lpszClassName;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct MSG
        {
            public IntPtr hwnd;
            public uint message;
            public IntPtr wParam;
            public IntPtr lParam;
            public uint time;
            public int ptX;
            public int ptY;
        }

        public SessionWatcher(
            ILogger logger,
            Action onSessionLock,
            Action onSessionUnlock,
            Action? onSuspend = null,
            Action? onResume = null)
        {
            _logger = logger;
            _onSessionLock = onSessionLock;
            _onSessionUnlock = onSessionUnlock;
            _onSuspend = onSuspend;
            _onResume = onResume;
        }

        public void Start()
        {
            _thread = new Thread(MessagePumpThread)
            {
                IsBackground = true,
                Name = "MajestyGuard.SessionWatcher",
            };
            _thread.Start();
        }

        private void MessagePumpThread()
        {
            _threadId = GetCurrentThreadId();
            _wndProcDelegate = WndProc;

            var hInstance = GetModuleHandleW(null);
            const string className = "MajestyGuard_SessionWatcher";

            var wc = new WNDCLASS
            {
                lpfnWndProc = _wndProcDelegate,
                hInstance = hInstance,
                lpszClassName = className,
            };

            RegisterClassW(ref wc);

            var hwnd = CreateWindowExW(
                0, className, null, 0,
                0, 0, 0, 0,
                HWND_MESSAGE, IntPtr.Zero, hInstance, IntPtr.Zero);

            if (hwnd == IntPtr.Zero)
            {
                _logger.LogError("SessionWatcher: CreateWindowExW failed (err {Err})",
                    Marshal.GetLastWin32Error());
                return;
            }

            if (!WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION))
            {
                _logger.LogWarning("WTSRegisterSessionNotification failed (err {Err})",
                    Marshal.GetLastWin32Error());
            }
            else
            {
                _logger.LogInformation("SessionWatcher registered for WTS notifications");
            }

            while (GetMessageW(out _, IntPtr.Zero, 0, 0))
            {
            }

            WTSUnRegisterSessionNotification(hwnd);
            DestroyWindow(hwnd);
        }

        private IntPtr WndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam)
        {
            if (msg == WM_WTSSESSION_CHANGE)
            {
                switch (wParam.ToInt32())
                {
                    case WTS_SESSION_LOCK:
                        _logger.LogInformation("Session locked (Win+L detected)");
                        _onSessionLock();
                        break;
                    case WTS_SESSION_UNLOCK:
                        _logger.LogInformation("Session unlocked");
                        _onSessionUnlock();
                        break;
                }
                return IntPtr.Zero;
            }

            if (msg == WM_POWERBROADCAST)
            {
                switch (wParam.ToInt32())
                {
                    case PBT_APMSUSPEND:
                        _logger.LogInformation("System entering sleep/hibernate");
                        _onSuspend?.Invoke();
                        break;
                    case PBT_APMRESUMEAUTOMATIC:
                    case PBT_APMRESUMESUSPEND:
                        _logger.LogInformation("System resuming from sleep/hibernate");
                        _onResume?.Invoke();
                        break;
                }
                return IntPtr.Zero;
            }

            return DefWindowProcW(hWnd, msg, wParam, lParam);
        }

        public void Dispose()
        {
            if (_threadId != 0)
                PostThreadMessageW(_threadId, WM_QUIT, IntPtr.Zero, IntPtr.Zero);
            _thread?.Join(3000);
        }
    }
}
