// MajestyGuard.Core/IPC/PipeServer.cs
// Named pipe IPC backbone used by ALL components.
// The Service runs as SERVER. CVEngine, Overlay, CredProvider are CLIENTS.
//
// SECURITY NOTE:
//   Pipes are created with restricted ACL — only the enrolled user SID
//   and SYSTEM may connect. This prevents other processes from injecting
//   fake detection results or commands.
//
// CODEX: Implement SendAsync, the reconnect loop, and the ACL setup.

using System;
using System.IO;
using System.IO.Pipes;
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


        public event Func<IpcMessage, Task>? MessageReceived;

        public MajestyPipeServer(string pipeName, ILogger logger, string? enrolledUserSid = null)
        {
            _pipeName = pipeName;
            _logger   = logger;
            _enrolledUserSid = enrolledUserSid;
        }

        public async Task StartAsync(CancellationToken ct)
        {
            _logger.LogInformation("Pipe server starting: {Name}", _pipeName);

            while (!ct.IsCancellationRequested)
            {
                try
                {
                    _pipe = CreateSecurePipe();
                    _logger.LogDebug("Waiting for client on pipe: {Name}", _pipeName);

                    await _pipe.WaitForConnectionAsync(ct);
                    _logger.LogDebug("Client connected on pipe: {Name}", _pipeName);

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
