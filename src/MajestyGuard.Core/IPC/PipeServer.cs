// MajestyGuard.Core/IPC/PipeServer.cs
// Named pipe IPC backbone used by ALL components.
// The Service runs as SERVER. CVEngine, Overlay, CredProvider are CLIENTS.
//
// SECURITY NOTE:
//   Pipes are created with restricted ACL — only the enrolled user SID
//   and SYSTEM may connect. This prevents other processes from injecting
//   fake detection results or commands.
//
//   MG-C-05 / MG-F-03 (fail-closed caller verification): a server created
//   with requireTrustedClientPid=true additionally verifies, via
//   GetNamedPipeClientProcessId, that the connecting client is exactly the
//   process whose PID was published through ExpectedClientProcessId (the
//   CV engine the service itself spawned). Any other caller — or any
//   failure to determine the caller — is disconnected before a single
//   message (inbound OR outbound, including queued embedding payloads) is
//   exchanged. Escape hatch for diagnostics: MG_ALLOW_ANY_CV_CLIENT=1
//   (read once at startup, loud warning logged; default is strict).
//
// CODEX: Implement SendAsync, the reconnect loop, and the ACL setup.

using System;
using System.IO;
using System.IO.Pipes;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Concurrent;
using Microsoft.Extensions.Logging;

namespace MajestyGuard.Core.IPC
{
    // ─────────────────────────────────────────────────────────────────
    // SERVER — runs in the Windows Service
    // ─────────────────────────────────────────────────────────────────
    public class MajestyPipeServer : IDisposable
    {
        private readonly string _pipeName;
        private readonly ILogger _logger;
        private readonly string? _enrolledUserSid;
        private readonly SemaphoreSlim _writeLock = new(1, 1);
        private NamedPipeServerStream? _pipe;
        private CancellationTokenSource _cts = new();
        private readonly ConcurrentQueue<string> _pendingMessages = new();

        // MG-C-05 / MG-F-03: fail-closed caller-PID gate. Armed per server
        // instance (the Worker arms it for the CV pipe only, so Overlay and
        // CredProvider keep their existing, DACL-only trust model).
        private readonly bool _requireTrustedClientPid;
        private readonly bool _allowAnyClient;
        private volatile int _expectedClientPid;   // 0 = not published yet


        public event Func<IpcMessage, Task>? MessageReceived;

        public MajestyPipeServer(string pipeName, ILogger logger, string? enrolledUserSid = null,
            bool requireTrustedClientPid = false)
        {
            _pipeName = pipeName;
            _logger   = logger;
            _enrolledUserSid = enrolledUserSid;
            _requireTrustedClientPid = requireTrustedClientPid;

            // MG-C-05 escape hatch for diagnostics (dev tools such as
            // mg_recog_diag.py, or the default-off daemon ServiceIPCServer
            // bridge, which cannot know the spawned engine's PID):
            // read ONCE at startup. Default: strict.
            _allowAnyClient = requireTrustedClientPid && string.Equals(
                Environment.GetEnvironmentVariable("MG_ALLOW_ANY_CV_CLIENT"),
                "1", StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>
        /// MG-C-05: PID of the only client process allowed to connect while
        /// requireTrustedClientPid is enabled. The Worker publishes the spawned
        /// CV-engine PID here immediately after Process.Start, and again on
        /// every watchdog respawn, so the gate never sticks to a stale PID —
        /// transient mismatches self-heal through the engine's reconnect loop.
        /// </summary>
        public int ExpectedClientProcessId
        {
            get => _expectedClientPid;
            set => _expectedClientPid = value;
        }

        public async Task StartAsync(CancellationToken ct)
        {
            _logger.LogInformation("Pipe server starting: {Name}", _pipeName);

            if (_allowAnyClient)
            {
                _logger.LogWarning(
                    "MG_ALLOW_ANY_CV_CLIENT=1 — caller-PID verification DISABLED on pipe {Name}. " +
                    "Any process passing the pipe DACL can impersonate the CV engine and inject " +
                    "DetectionResult messages (MG-C-05/MG-F-03). Use only for isolated diagnostics.",
                    _pipeName);
            }

            while (!ct.IsCancellationRequested)
            {
                try
                {
                    _pipe = CreateSecurePipe();
                    _logger.LogDebug("Waiting for client on pipe: {Name}", _pipeName);

                    await _pipe.WaitForConnectionAsync(ct);
                    _logger.LogDebug("Client connected on pipe: {Name}", _pipeName);

                    // MG-C-05 / MG-F-03: verify the caller BEFORE any traffic flows —
                    // including FlushPendingMessagesAsync, whose queue can carry the
                    // enrolled embedding payload. Fail-closed: disconnect on mismatch.
                    if (!VerifyClientProcessId(_pipe))
                    {
                        _pipe.Dispose();
                        _pipe = null;
                        await Task.Delay(500, ct);  // Brief pause to blunt reconnect-spam
                        continue;
                    }

                    await FlushPendingMessagesAsync();
                    await ReadLoopAsync(_pipe, ct);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Pipe server error on {Name}", _pipeName);
                    await Task.Delay(500, ct);  // Brief pause before accepting next connection
                }
                finally
                {
                    _pipe?.Dispose();
                    _pipe = null;
                }
            }
        }

        // ─────────────────────────────────────────────────────────────
        // MG-C-05 / MG-F-03 — CALLER VERIFICATION (fail-closed)
        // ─────────────────────────────────────────────────────────────

        /// <summary>
        /// Returns true only when this connection's client process is the
        /// process this service spawned (ExpectedClientProcessId). Mismatch,
        /// failure to query the OS, or an armed gate with no published PID
        /// all disconnect the client before any byte is processed.
        /// </summary>
        private bool VerifyClientProcessId(NamedPipeServerStream pipe)
        {
            if (!_requireTrustedClientPid || _allowAnyClient)
                return true;  // gate not armed for this pipe instance

            int expected = _expectedClientPid;  // volatile read
            if (expected == 0)
            {
                _logger.LogWarning(
                    "Pipe {Name}: trusted-client PID gate armed but no expected PID published — " +
                    "disconnecting client (fail-closed, MG-C-05)",
                    _pipeName);
                return false;
            }

            if (!PipeClientIdentity.TryGetClientProcessId(pipe, out uint clientPid, out int win32Error))
            {
                _logger.LogWarning(
                    "Pipe {Name}: could not determine client PID (handle unavailable or " +
                    "GetNamedPipeClientProcessId failed, Win32Error={Err}) — disconnecting client " +
                    "(fail-closed, MG-C-05)",
                    _pipeName, win32Error);
                return false;
            }

            if (clientPid != unchecked((uint)expected))
            {
                _logger.LogWarning(
                    "Pipe {Name}: untrusted client PID {ClientPid} (expected {ExpectedPid} — the " +
                    "spawned CV engine). Disconnecting; no message processed (fail-closed, MG-C-05/MG-F-03).",
                    _pipeName, clientPid, expected);
                return false;
            }

            _logger.LogDebug("Pipe {Name}: client PID {Pid} verified", _pipeName, clientPid);
            return true;
        }

        public async Task SendAsync(IpcMessage message)
        {
            await SendRawAsync(message.Serialize());
        }

        public async Task SendRawAsync(string json)
        {
            if (_pipe?.IsConnected != true)
            {
                _logger.LogWarning("SendAsync called but no client connected on {Name}. Queueing message.", _pipeName);
                if (_pendingMessages.Count < 500)
                {
                    _pendingMessages.Enqueue(json);
                }
                return;
            }

            await FlushPendingMessagesAsync();
            await WriteMessageDirectAsync(json);
        }

        private async Task FlushPendingMessagesAsync()
        {
            while (_pipe?.IsConnected == true && _pendingMessages.TryDequeue(out var pendingJson))
            {
                await WriteMessageDirectAsync(pendingJson);
            }
        }

        private async Task WriteMessageDirectAsync(string json)
        {
            var pipe = _pipe;
            if (pipe?.IsConnected != true) return;
            await _writeLock.WaitAsync();
            try
            {
                var bytes = Encoding.UTF8.GetBytes(json + "\n");
                await pipe.WriteAsync(bytes);
                await pipe.FlushAsync();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to send message on pipe {Name}", _pipeName);
                if (_pendingMessages.Count < 500)
                {
                    _pendingMessages.Enqueue(json);
                }
            }
            finally
            {
                _writeLock.Release();
            }
        }


        private async Task ReadLoopAsync(NamedPipeServerStream pipe, CancellationToken ct)
        {
            using var reader = new StreamReader(pipe, Encoding.UTF8, leaveOpen: true);

            while (!ct.IsCancellationRequested && pipe.IsConnected)
            {
                try
                {
                    var line = await ReadLineWithLimitAsync(reader, 1024 * 1024, ct);
                    if (line == null) break;  // Client disconnected

                    var msg = IpcMessage.Deserialize(line);
                    if (msg != null && MessageReceived != null)
                        await MessageReceived(msg);
                }
                catch (InvalidDataException ex)
                {
                    _logger.LogError(ex, "Terminating pipe connection on {Name} due to protocol error", _pipeName);
                    break;
                }
            }
        }

        internal static async Task<string?> ReadLineWithLimitAsync(StreamReader reader, int maxChars, CancellationToken ct)
        {
            var sb = new StringBuilder();
            char[] buffer = new char[512];
            while (sb.Length < maxChars)
            {
                int maxToRead = Math.Min(buffer.Length, maxChars - sb.Length);
                int read = await reader.ReadAsync(buffer.AsMemory(0, maxToRead), ct);
                if (read == 0)
                {
                    return sb.Length > 0 ? sb.ToString() : null;
                }
                for (int i = 0; i < read; i++)
                {
                    char c = buffer[i];
                    if (c == '\n')
                    {
                        if (sb.Length > 0 && sb[sb.Length - 1] == '\r')
                        {
                            sb.Length--;
                        }
                        return sb.ToString();
                    }
                    sb.Append(c);
                }
            }
            throw new InvalidDataException($"IPC message size limit exceeded ({maxChars} characters)");
        }


        /// <summary>
        /// Creates a NamedPipeServerStream with an ACL that restricts
        /// access to the enrolled user SID and SYSTEM only.
        /// CODEX: Implement the PipeSecurity setup below.
        /// </summary>
        private NamedPipeServerStream CreateSecurePipe()
        {
            var security = new PipeSecurity();

            // SYSTEM: full control
            security.AddAccessRule(new PipeAccessRule(
                new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                PipeAccessRights.FullControl,
                AccessControlType.Allow));

            // Enrolled user: read/write
            if (!string.IsNullOrEmpty(_enrolledUserSid))
            {
                security.AddAccessRule(new PipeAccessRule(
                    new SecurityIdentifier(_enrolledUserSid),
                    PipeAccessRights.ReadWrite,
                    AccessControlType.Allow));
            }

            // No World Deny — would block enrolled user (World SID is in every token).
            // Security by allowlist: unlisted callers have no Allow rule = denied by default.

            return NamedPipeServerStreamAcl.Create(
                pipeName:          _pipeName,
                direction:         PipeDirection.InOut,
                maxNumberOfServerInstances: 1,
                transmissionMode:  PipeTransmissionMode.Byte,
                options:           PipeOptions.Asynchronous | PipeOptions.FirstPipeInstance,
                inBufferSize:      1024,
                outBufferSize:     1024,
                pipeSecurity:      security);
        }

        public void Dispose()
        {
            _cts.Cancel();
            _pipe?.Dispose();
        }
    }

    // ─────────────────────────────────────────────────────────────────
    // MG-C-05 — native caller identity for a server pipe instance.
    // ─────────────────────────────────────────────────────────────────
    internal static class PipeClientIdentity
    {
        // PipeStream.SafePipeHandle's declared accessibility varies across
        // .NET flavors (protected in several releases), so the handle is
        // fetched via reflection with a cached PropertyInfo: this compiles
        // and works regardless of the property's declared accessibility.
        // If the handle cannot be obtained, callers fail closed
        // (disconnect) — see MajestyPipeServer.VerifyClientProcessId.
        private static readonly PropertyInfo? SafePipeHandleProperty =
            typeof(NamedPipeServerStream).GetProperty(
                "SafePipeHandle",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetNamedPipeClientProcessId(
            SafeHandle hNamedPipe, out uint clientProcessId);

        /// <summary>
        /// Returns the PID of the process that created this pipe connection,
        /// as reported by the kernel at accept time (not a client-supplied
        /// claim, and not a stale PID re-resolved later — the MG-F-06
        /// PID-reuse caveat does not apply to this query shape).
        /// </summary>
        internal static bool TryGetClientProcessId(
            NamedPipeServerStream pipe, out uint clientProcessId, out int win32Error)
        {
            clientProcessId = 0;
            win32Error = 0;

            SafeHandle? handle = null;
            try
            {
                handle = SafePipeHandleProperty?.GetValue(pipe) as SafeHandle;
            }
            catch (Exception)
            {
                // Inaccessible handle — fall through to fail-closed.
            }

            if (handle is null || handle.IsInvalid)
                return false;

            if (!GetNamedPipeClientProcessId(handle, out clientProcessId))
            {
                win32Error = Marshal.GetLastWin32Error();
                return false;
            }

            return clientProcessId != 0;
        }
    }

    // ─────────────────────────────────────────────────────────────────
    // CLIENT — used by CVEngine bridge, Overlay, CredentialProvider
    // ─────────────────────────────────────────────────────────────────
    public class MajestyPipeClient : IDisposable
    {
        private readonly string _pipeName;
        private readonly ILogger _logger;
        private NamedPipeClientStream? _pipe;
        private StreamWriter? _writer;
        private readonly ConcurrentQueue<IpcMessage> _pendingMessages = new();

        public event Func<IpcMessage, Task>? MessageReceived;

        public MajestyPipeClient(string pipeName, ILogger logger)
        {
            _pipeName = pipeName;
            _logger   = logger;
        }

        public async Task ConnectAsync(CancellationToken ct)
        {
            int attempt = 0;
            while (!ct.IsCancellationRequested)
            {
                try
                {
                    _pipe = new NamedPipeClientStream(
                        ".",
                        _pipeName,
                        PipeDirection.InOut,
                        PipeOptions.Asynchronous);

                    await _pipe.ConnectAsync(3000, ct);
                    _writer = new StreamWriter(_pipe, Encoding.UTF8) { AutoFlush = true };
                    _logger.LogInformation("Connected to pipe: {Name}", _pipeName);

                    await FlushPendingMessagesAsync();

                    // Start reading in background
                    _ = ReadLoopAsync(_pipe, ct);
                    return;
                }
                catch (Exception ex) when (attempt < 10)
                {
                    attempt++;
                    var delay = Math.Min(200 * (1 << attempt), 5000);  // Exponential backoff cap 5s
                    _logger.LogWarning("Pipe connect failed ({Attempt}/10), retry in {Delay}ms: {Err}",
                        attempt, delay, ex.Message);
                    await Task.Delay(delay, ct);
                }
                catch (Exception ex)
                {
                    _logger.LogError("Pipe connect failed after 10 attempts — giving up: {Err}", ex.Message);
                    throw;
                }
            }
        }

        public async Task SendAsync(IpcMessage message)
        {
            var writer = _writer;
            if (writer == null)
            {
                _logger.LogWarning("SendAsync called but client not connected on {Name}. Queueing message.", _pipeName);
                if (_pendingMessages.Count < 500)
                {
                    _pendingMessages.Enqueue(message);
                }
                return;
            }

            await FlushPendingMessagesAsync();

            try
            {
                await writer.WriteLineAsync(message.Serialize());
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to send message on client pipe {Name}", _pipeName);
                if (_pendingMessages.Count < 500)
                {
                    _pendingMessages.Enqueue(message);
                }
            }
        }

        private async Task FlushPendingMessagesAsync()
        {
            var writer = _writer;
            if (writer == null) return;

            while (_pendingMessages.TryDequeue(out var pendingMsg))
            {
                try
                {
                    await writer.WriteLineAsync(pendingMsg.Serialize());
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Failed to send queued message on client pipe {Name}", _pipeName);
                    if (_pendingMessages.Count < 500)
                    {
                        _pendingMessages.Enqueue(pendingMsg);
                    }
                    break;
                }
            }
        }

        private async Task ReadLoopAsync(NamedPipeClientStream pipe, CancellationToken ct)
        {
            using var reader = new StreamReader(pipe, Encoding.UTF8, leaveOpen: true);

            while (!ct.IsCancellationRequested && pipe.IsConnected)
            {
                try
                {
                    var line = await MajestyPipeServer.ReadLineWithLimitAsync(reader, 1024 * 1024, ct);
                    if (line == null) break;

                    var msg = IpcMessage.Deserialize(line);
                    if (msg != null && MessageReceived != null)
                        await MessageReceived(msg);
                }
                catch (InvalidDataException ex)
                {
                    _logger.LogError(ex, "Terminating pipe client connection on {Name} due to protocol error", _pipeName);
                    break;
                }
                catch (OperationCanceledException) { break; }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Read error on pipe {Name}", _pipeName);
                    break;
                }
            }

            _logger.LogWarning("Disconnected from pipe {Name} — attempting reconnect", _pipeName);
            _writer?.Dispose();
            _pipe?.Dispose();
            _writer = null;
            _pipe = null;

            try
            {
                await ConnectAsync(ct);
            }
            catch (OperationCanceledException) { }
        }

        public void Dispose()
        {
            _writer?.Dispose();
            _pipe?.Dispose();
        }
    }
}
