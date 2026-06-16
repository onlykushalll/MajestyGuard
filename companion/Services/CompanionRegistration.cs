// CompanionRegistration.cs — One-time WHCDF device registration with Windows Hello.
// Stores the WHCDF keys in Windows Credential Manager (DPAPI-protected).
// The Python daemon intentionally does not trust an environment key by default;
// WHCDF IPC remains fail-closed until secure key handoff/caller auth is wired.

using System;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using Windows.Security.Authentication.Identity.Provider;
using Windows.Security.Credentials;

namespace MajestyGuard.Companion.Services
{
    public record RegistrationResult(bool Success, string? ErrorMessage = null);

    public sealed class CompanionRegistration
    {
        private const string DeviceId          = "MajestyGuard-FaceUnlock-v2";
        private const string DeviceFriendlyName = "MajestyGuard Face Unlock";
        private const string AuthKeyVaultResource = "MajestyGuard_MutualAuthKey";
        private const string DeviceKeyVaultResource = "MajestyGuard_DeviceKey";

        // ── Public API ────────────────────────────────────────────────────────

        public async Task<bool> IsRegisteredAsync()
        {
            try
            {
                var devices = await
                    SecondaryAuthenticationFactorRegistration
                    .FindAllRegisteredDeviceInfoAsync(
                        SecondaryAuthenticationFactorDeviceFindScope.User);

                foreach (var d in devices)
                    if (d.DeviceId == DeviceId)
                        return true;

                return false;
            }
            catch
            {
                return false;
            }
        }

        public async Task<RegistrationResult> RegisterAsync()
        {
            var stage = "preparing registration";
            try
            {
                stage = "KeyCredentialManager.IsSupportedAsync";
                if (!await KeyCredentialManager.IsSupportedAsync())
                {
                    return new RegistrationResult(false,
                        "Windows Hello PIN is not configured. Set up a Windows Hello PIN in " +
                        "Settings > Accounts > Sign-in options, then retry registration.");
                }

                // 1. Generate a fresh device key and use the daemon's configured auth key.
                var deviceKey = new byte[32];
                using var rng = System.Security.Cryptography.RandomNumberGenerator.Create();
                rng.GetBytes(deviceKey);
                var mutualAuthKey = LoadOrCreateMutualAuthKey(rng);

                // 2. Register with Windows Hello WHCDF
                stage = "RequestStartRegisteringDeviceAsync";
                var regResult = await
                    SecondaryAuthenticationFactorRegistration
                    .RequestStartRegisteringDeviceAsync(
                        DeviceId,
                        SecondaryAuthenticationFactorDeviceCapabilities.SecureStorage |
                        SecondaryAuthenticationFactorDeviceCapabilities.HMacSha256 |
                        SecondaryAuthenticationFactorDeviceCapabilities.StoreKeys,
                        DeviceFriendlyName,
                        "MG-FACE-V2",
                        deviceKey.AsBuffer(),
                        mutualAuthKey.AsBuffer());

                if (regResult.Status !=
                    SecondaryAuthenticationFactorRegistrationStatus.Started)
                {
                    return new RegistrationResult(false,
                        $"{stage} returned {regResult.Status}. " +
                        "Ensure setup_whcdf.ps1 was run as Administrator.");
                }

                stage = "FinishRegisteringDeviceAsync";
                var configData = Encoding.UTF8.GetBytes("MajestyGuard-v2").AsBuffer();
                await regResult.Registration.FinishRegisteringDeviceAsync(configData);

                // 3. Persist the keys so the companion/daemon bridge can load them.
                stage = "PersistKeysToCredentialVault";
                PersistKeysToCredentialVault(deviceKey, mutualAuthKey);

                return new RegistrationResult(true);
            }
            catch (Exception ex)
            {
                var message = string.IsNullOrWhiteSpace(ex.Message) ? "(no exception message)" : ex.Message;
                if (ex.HResult == unchecked((int)0x80090029))
                {
                    return new RegistrationResult(false,
                        $"{stage}: WHCDF secondaryAuthenticationFactor is unavailable on this machine/app package. " +
                        "Microsoft restricts this capability to specially provisioned UWP companion apps; " +
                        "use the Credential Provider fallback path for local development.");
                }

                return new RegistrationResult(false,
                    $"{stage}: {ex.GetType().Name}, HRESULT=0x{ex.HResult:X8}. {message}");
            }
        }

        public async Task UnregisterAsync()
        {
            var devices = await
                SecondaryAuthenticationFactorRegistration
                .FindAllRegisteredDeviceInfoAsync(
                    SecondaryAuthenticationFactorDeviceFindScope.User);

            foreach (var d in devices)
            {
                if (d.DeviceId == DeviceId)
                {
                    await SecondaryAuthenticationFactorRegistration
                        .UnregisterDeviceAsync(DeviceId);
                    RemoveKeyFromCredentialVault();
                    return;
                }
            }
        }

        // ── Key persistence via Windows Credential Manager ────────────────────

        private static byte[] LoadOrCreateMutualAuthKey(RandomNumberGenerator rng)
        {
            var hex =
                Environment.GetEnvironmentVariable("MAJESTYGUARD_MUTUAL_AUTH_KEY", EnvironmentVariableTarget.Machine) ??
                Environment.GetEnvironmentVariable("MAJESTYGUARD_MUTUAL_AUTH_KEY", EnvironmentVariableTarget.User) ??
                Environment.GetEnvironmentVariable("MAJESTYGUARD_MUTUAL_AUTH_KEY");

            if (!string.IsNullOrWhiteSpace(hex))
            {
                try
                {
                    var key = Convert.FromHexString(hex.Trim());
                    if (key.Length == 32) return key;
                }
                catch { /* fall through and generate */ }
            }

            var generated = new byte[32];
            rng.GetBytes(generated);
            return generated;
        }

        private static void PersistKeysToCredentialVault(byte[] deviceKey, byte[] mutualAuthKey)
        {
            var vault = new PasswordVault();
            var deviceHex = Convert.ToHexString(deviceKey).ToLowerInvariant();
            var authHex = Convert.ToHexString(mutualAuthKey).ToLowerInvariant();

            // Remove any stale entries first.
            try { vault.Remove(vault.Retrieve(DeviceKeyVaultResource, DeviceId)); }
            catch { /* not present */ }
            try { vault.Remove(vault.Retrieve(AuthKeyVaultResource, DeviceId)); }
            catch { /* not present */ }

            vault.Add(new PasswordCredential(DeviceKeyVaultResource, DeviceId, deviceHex));
            vault.Add(new PasswordCredential(AuthKeyVaultResource, DeviceId, authHex));
        }

        private static void RemoveKeyFromCredentialVault()
        {
            try
            {
                var vault = new PasswordVault();
                try { vault.Remove(vault.Retrieve(DeviceKeyVaultResource, DeviceId)); }
                catch { /* already gone */ }
                try { vault.Remove(vault.Retrieve(AuthKeyVaultResource, DeviceId)); }
                catch { /* already gone */ }
            }
            catch { /* already gone */ }
        }

        /// <summary>
        /// Reads the MutualAuthKey back from Credential Manager.
        /// Call this to inject it into the daemon's environment.
        /// Returns null if not found.
        /// </summary>
        public static string? ReadKeyHex()
        {
            try
            {
                var vault = new PasswordVault();
                var cred  = vault.Retrieve(AuthKeyVaultResource, DeviceId);
                cred.RetrievePassword();
                return cred.Password;
            }
            catch
            {
                return null;
            }
        }

        public static string? ReadDeviceKeyHex()
        {
            try
            {
                var vault = new PasswordVault();
                var cred  = vault.Retrieve(DeviceKeyVaultResource, DeviceId);
                cred.RetrievePassword();
                return cred.Password;
            }
            catch
            {
                return null;
            }
        }
    }
}
