// UnlockTask.cs — WHCDF background task.
// Fires when the Windows lock screen activates.
// Connects to the Python daemon via named pipe, verifies face state,
// computes HMAC, and calls Windows Hello to unlock — NO password stored.

using System;
using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using Windows.ApplicationModel.Background;
using Windows.Security.Authentication.Identity.Provider;
using Windows.Storage.Streams;

namespace MajestyGuard.Companion.Background
{
    public sealed class UnlockTask : IBackgroundTask
    {
        private const string PipeName    = "MajestyGuard_WHCDF";
        private const int    PipeTimeout = 3000; // ms

        // ── IBackgroundTask entry point ────────────────────────────────────────

        public async void Run(IBackgroundTaskInstance taskInstance)
        {
            var deferral = taskInstance.GetDeferral();
            try
            {
                await HandleUnlockRequestAsync();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"[WHCDF] UnlockTask error: {ex.Message}");
            }
            finally
            {
                deferral.Complete();
            }
        }

        // ── Registration helper ────────────────────────────────────────────────

        /// <summary>
        /// Registers this background task for the SecondaryAuthenticationFactorAuthentication
        /// trigger. Safe to call repeatedly — skips if already registered.
        /// </summary>
        public static async Task EnsureRegisteredAsync()
        {
            const string TaskName = "MajestyGuardUnlockTask";

            foreach (var existing in BackgroundTaskRegistration.AllTasks.Values)
            {
                if (existing.Name == TaskName) return; // already registered
            }

            var builder = new BackgroundTaskBuilder
            {
                Name      = TaskName,
                TaskEntryPoint = "MajestyGuard.Companion.Background.UnlockTask",
            };
            builder.SetTrigger(
                new SecondaryAuthenticationFactorAuthenticationTrigger());

            var registration = builder.Register();
            System.Diagnostics.Debug.WriteLine(
                $"[WHCDF] Background task registered: {registration.TaskId}");

            await Task.CompletedTask;
        }

        // ── Core unlock flow ───────────────────────────────────────────────────

        private static async Task HandleUnlockRequestAsync()
        {
            // 1. Notify user that MajestyGuard is looking for their face
            await SecondaryAuthenticationFactorAuthentication
                .ShowNotificationMessageAsync(null,
                    SecondaryAuthenticationFactorAuthenticationMessage.LookingForDevice);

            // Wait for the authentication stage to begin
            var authStageInfo = await
                SecondaryAuthenticationFactorAuthentication
                .GetAuthenticationStageInfoAsync();

            if (authStageInfo.Stage !=
                SecondaryAuthenticationFactorAuthenticationStage.WaitingForUserConfirmation
                &&
                authStageInfo.Stage !=
                SecondaryAuthenticationFactorAuthenticationStage.CollectingCredential)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[WHCDF] Unexpected auth stage: {authStageInfo.Stage}");
                return;
            }

            // 2. Start authentication — this provides the three nonces
            var openResult = await
                SecondaryAuthenticationFactorAuthentication
                .StartAuthenticationAsync(
                    authStageInfo.DeviceId,
                    null); // no user blob needed

            if (openResult.Status !=
                SecondaryAuthenticationFactorAuthenticationStatus.Started)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[WHCDF] StartAuthentication failed: {openResult.Status}");

                if (openResult.Status ==
                    SecondaryAuthenticationFactorAuthenticationStatus.DisabledByPolicy)
                {
                    System.Diagnostics.Debug.WriteLine(
                        "[WHCDF] DisabledByPolicy — WHCDF capability not provisioned. " +
                        "Re-run setup_whcdf.ps1 as Admin or switch to credential provider.");
                }
                return;
            }

            var auth = openResult.Authentication;

            // 3. Ask Python daemon to compute HMAC (it verifies face state first)
            var hmac = await AskDaemonForHmacAsync(
                auth.ServiceAuthenticationHmac,
                auth.DeviceNonce,
                auth.SessionNonce);

            if (hmac == null)
            {
                // Face not authorized — abort unlock
                await auth.AbortAuthenticationAsync("face-not-authorized");
                return;
            }

            // 4. Send HMAC to Windows Hello → desktop unlocks
            await auth.FinishAuthenticationAsync(hmac, null);
            System.Diagnostics.Debug.WriteLine("[WHCDF] Authentication finished — unlocked.");
        }

        // ── Python daemon pipe call ────────────────────────────────────────────

        private static async Task<IBuffer?> AskDaemonForHmacAsync(
            IBuffer serviceNonce,
            IBuffer deviceNonce,
            IBuffer sessionNonce)
        {
            try
            {
                using var pipe = new NamedPipeClientStream(
                    ".", PipeName,
                    PipeDirection.InOut,
                    PipeOptions.Asynchronous);

                await pipe.ConnectAsync(PipeTimeout);

                // Send three nonces as hex lines, prefixed by protocol header
                var sb = new StringBuilder();
                sb.AppendLine("WHCDF_HMAC_REQUEST");
                sb.AppendLine(BufferToHex(serviceNonce));
                sb.AppendLine(BufferToHex(deviceNonce));
                sb.AppendLine(BufferToHex(sessionNonce));

                var request = Encoding.UTF8.GetBytes(sb.ToString());
                await pipe.WriteAsync(request, 0, request.Length);
                await pipe.FlushAsync();

                // Read response
                var buf = new byte[1024];
                var read = await pipe.ReadAsync(buf, 0, buf.Length);
                var response = Encoding.UTF8.GetString(buf, 0, read).Trim();

                var lines = response.Split('\n', StringSplitOptions.RemoveEmptyEntries);
                if (lines.Length < 2 || lines[0].Trim() != "HMAC_OK")
                {
                    System.Diagnostics.Debug.WriteLine(
                        $"[WHCDF] Daemon denied: {response}");
                    return null;
                }

                // Convert hex HMAC back to IBuffer
                var hmacHex = lines[1].Trim();
                return HexToBuffer(hmacHex);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"[WHCDF] Pipe error: {ex.Message}");
                return null;
            }
        }

        // ── Helpers ────────────────────────────────────────────────────────────

        private static string BufferToHex(IBuffer buffer)
        {
            using var dr = Windows.Storage.Streams.DataReader.FromBuffer(buffer);
            var bytes = new byte[buffer.Length];
            dr.ReadBytes(bytes);
            return Convert.ToHexString(bytes).ToLowerInvariant();
        }

        private static Windows.Storage.Streams.IBuffer HexToBuffer(string hex)
        {
            var bytes  = Convert.FromHexString(hex);
            var writer = new Windows.Storage.Streams.DataWriter();
            writer.WriteBytes(bytes);
            return writer.DetachBuffer();
        }
    }
}
