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
        private NamedPipeServerStream? _pipe;
        private CancellationTokenSource _cts = new();

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
                _logger.LogWarning("SendAsync called but no client connected on {Name}", _pipeName);
                return;
            }

            try
            {
                var bytes = Encoding.UTF8.GetBytes(json + "\n");
                await _pipe.WriteAsync(bytes);
                await _pipe.FlushAsync();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to send message on pipe {Name}", _pipeName);
            }
        }

        private async Task ReadLoopAsync(NamedPipeServerStream pipe, CancellationToken ct)
        {
            using var reader = new StreamReader(pipe, Encoding.UTF8, leaveOpen: true);

            while (!ct.IsCancellationRequested && pipe.IsConnected)
            {
                var line = await reader.ReadLineAsync(ct);
                if (line == null) break;  // Client disconnected

                var msg = IpcMessage.Deserialize(line);
                if (msg != null && MessageReceived != null)
                    await MessageReceived(msg);
            }
        }

        /// <summary>
        /// Creates a NamedPipeServerStream with an ACL that restricts
        /// access to the enrolled user SID and SYSTEM only.
        /// CODEX: Implement the PipeSecurity setup below.
        /// </summary>
        private NamedPipeServerStream CreateSecurePipe()
        {
            var security = new PipeSecurity();

            // Allow SYSTEM full control
            security.AddAccessRule(new PipeAccessRule(
                new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                PipeAccessRights.FullControl,
                AccessControlType.Allow));

            // Allow enrolled user read/write (use config SID, not current process identity)
            if (!string.IsNullOrEmpty(_enrolledUserSid))
            {
                security.AddAccessRule(new PipeAccessRule(
                    new SecurityIdentifier(_enrolledUserSid),
                    PipeAccessRights.ReadWrite,
                    AccessControlType.Allow));
            }

            // DENY everyone else — evaluated before Allow but specific SIDs above take precedence
            security.AddAccessRule(new PipeAccessRule(
                new SecurityIdentifier(WellKnownSidType.WorldSid, null),
                PipeAccessRights.FullControl,
                AccessControlType.Deny));

            // CODEX: Use NamedPipeServerStreamAcl.Create() on .NET 5+
            // to apply the PipeSecurity correctly.
            // See: https://learn.microsoft.com/dotnet/api/system.io.pipes.namedpipeserverstreamacl
            return NamedPipeServerStreamAcl.Create(
                pipeName:          _pipeName,
                direction:         PipeDirection.InOut,
                maxNumberOfServerInstances: 1,
                transmissionMode:  PipeTransmissionMode.Byte,
                options:           PipeOptions.Asynchronous,
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

        public event Func<IpcMessage, Task>? MessageReceived;

        public MajestyPipeClient(string pipeName, ILogger logger)
        {
            _pipeName = pipeName;
            _logger   = logger;
        }

        /// <summary>
        /// Connects to the server pipe with exponential backoff retry.
        /// CODEX: Implement retry logic here. Max retries = 10, base delay 200ms.
        /// </summary>
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

                    await _pipe.ConnectAsync(timeoutMs: 3000, ct);
                    _writer = new StreamWriter(_pipe, Encoding.UTF8) { AutoFlush = true };
                    _logger.LogInformation("Connected to pipe: {Name}", _pipeName);

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
            }
        }

        public async Task SendAsync(IpcMessage message)
        {
            if (_writer == null) throw new InvalidOperationException("Not connected");
            await _writer.WriteLineAsync(message.Serialize());
        }

        private async Task ReadLoopAsync(NamedPipeClientStream pipe, CancellationToken ct)
        {
            using var reader = new StreamReader(pipe, Encoding.UTF8, leaveOpen: true);

            while (!ct.IsCancellationRequested && pipe.IsConnected)
            {
                try
                {
                    var line = await reader.ReadLineAsync(ct);
                    if (line == null) break;

                    var msg = IpcMessage.Deserialize(line);
                    if (msg != null && MessageReceived != null)
                        await MessageReceived(msg);
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
