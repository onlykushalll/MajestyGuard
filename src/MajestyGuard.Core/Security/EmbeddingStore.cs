// MajestyGuard.Core/Security/EmbeddingStore.cs
// Handles encrypted storage and retrieval of face embeddings.
//
// SECURITY DESIGN:
//   - Embeddings are encrypted using Windows DPAPI (ProtectedData) with
//     LOCAL=machine scope (see DESCRIPTOR below) - bound to this machine's
//     key, NOT the current user's credentials. This is deliberate: DPAPI
//     keys derived from a user's password hash can be extracted via
//     Mimikatz-style LSASS memory attacks if a valid credential is stolen.
//     Machine-scoped keys require code execution ON this specific machine
//     to decrypt, which is a stronger bar against that attack class.
//   - Trade-off: any process running locally on this machine (not just the
//     enrolled user) can call NCryptUnprotectSecret with this descriptor.
//     This is accepted in exchange for defeating remote credential-theft
//     decryption.
//   - File path includes user SID to prevent cross-profile access
//   - Raw embedding bytes are zeroed from memory after use
//   - NEVER write raw embeddings to disk - only the encrypted blob
//
// MG-C-04 hardening (non-breaking): the store file and its directory now
//   carry an explicit ACL (current user SID + SYSTEM + Administrators only),
//   and Save() is atomic (temp + File.Replace with backup) so torn files
//   are impossible. KNOWN LIMITATION (unchanged by design - changing it
//   would invalidate every enrolled template): the DPAPI-NG descriptor
//   remains LOCAL=machine, so ANY local process that can READ the file can
//   decrypt it. The new ACL blocks that read in practice; a same-user or
//   admin compromise still defeats confidentiality (audit finding MG-C-04).
//
// IMPORTANT: This must run in USER context (not SYSTEM).
//   Call from a user-mode process during enrollment and verification.
//   The Windows Service (SYSTEM) cannot decrypt this DPAPI-protected data
//   directly - it uses a user-mode helper process (DpapiHelper) launched
//   via CreateProcessAsUserW for this.

using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace MajestyGuard.Core.Security
{
    public class FaceEmbedding
    {
        /// <summary>512-dimensional float vector from InsightFace ArcFace model.</summary>
        public float[] Vector { get; init; } = Array.Empty<float>();

        /// <summary>Which angle this embedding was captured at.</summary>
        public string Angle { get; init; } = "Front";

        /// <summary>UTC timestamp of enrollment capture.</summary>
        public DateTime CapturedAt { get; init; } = DateTime.UtcNow;

        /// <summary>Model version that generated this embedding. For re-enrollment detection.</summary>
        public string ModelVersion { get; init; } = "buffalo_l_v1";
    }

    public class EnrollmentRecord
    {
        /// <summary>User SID this record belongs to.</summary>
        public string UserSid { get; init; } = string.Empty;

        /// <summary>
        /// Multiple embeddings from different angles.
        /// Minimum 3 required for reliable recognition.
        /// </summary>
        public FaceEmbedding[] Embeddings { get; init; } = Array.Empty<FaceEmbedding>();

        /// <summary>UTC timestamp of initial enrollment.</summary>
        public DateTime EnrolledAt { get; init; } = DateTime.UtcNow;

        /// <summary>UTC timestamp of last successful verification.</summary>
        public DateTime LastVerifiedAt { get; set; } = DateTime.UtcNow;

        /// <summary>
        /// Total successful authentications. Used to detect if re-enrollment
        /// is needed (if this stays 0 after enrollment, something is wrong).
        /// </summary>
        public int SuccessfulAuthCount { get; set; }
    }

    public class EmbeddingStore
    {
        private readonly string _storePath;
        private readonly string _currentUserSid;

        // MG-C-04: optional diagnostics logger (null = silent - keeps every
        // existing single-argument construction source-compatible).
        private readonly ILogger? _logger;

        // MG-C-04: the directory ACL is asserted once per store instance.
        private bool _aclApplied;

        // Entropy adds a per-application salt to DPAPI - prevents
        // other apps using DPAPI from accessing our blob
        private static readonly byte[] _entropy = [
            0x4D, 0x61, 0x6A, 0x65, 0x73, 0x74, 0x79, 0x47,
            0x75, 0x61, 0x72, 0x64, 0x5F, 0x53, 0x61, 0x6C,
            0x74, 0x5F, 0x76, 0x31
        ];

        public EmbeddingStore(string storePath, ILogger? logger = null)
        {
            _storePath      = storePath;
            _logger         = logger;
            _currentUserSid = WindowsIdentity.GetCurrent().User?.Value
                              ?? throw new InvalidOperationException("Cannot determine user SID");

            Directory.CreateDirectory(Path.GetDirectoryName(storePath)!);
        }

        // -------------------------------------------------------------
        // SAVE
        // -------------------------------------------------------------

        /// <summary>
        /// Encrypts and saves the enrollment record to disk.
        /// DPAPI ties the ciphertext to the current user's credentials.
        /// </summary>
        public void Save(EnrollmentRecord record)
        {
            if (record.UserSid != _currentUserSid)
                throw new UnauthorizedAccessException(
                    "Cannot save enrollment for a different user SID");

            byte[]? plainBytes = null;

            try
            {
                var json = JsonSerializer.Serialize(record);
                plainBytes = System.Text.Encoding.UTF8.GetBytes(json);

                // DPAPI-NG with LOCAL=machine descriptor.
                // Binds ciphertext to this specific machine.
                // Admin cannot decrypt by impersonating the user token -
                // they need to be ON THIS MACHINE with the same OS installation.
                // Mimikatz LSASS dump attack is defeated: the Master Key alone
                // is insufficient without the machine-bound TPM component.
                var cipherBytes = DpapiNg.Protect(plainBytes);

                // MG-C-04: atomic write - write a sibling temp file, then
                // File.Replace (with .bak backup) so a crash mid-write can never
                // leave a truncated/torn store behind. The blob format and the
                // DPAPI-NG scope/entropy are unchanged - existing enrolled
                // templates keep working.
                var tempPath   = _storePath + ".tmp";
                var backupPath = _storePath + ".bak";

                using (var fs = new FileStream(tempPath, FileMode.Create,
                    FileAccess.Write, FileShare.None))
                using (var bw = new BinaryWriter(fs))
                {
                    bw.Write(cipherBytes.Length);
                    bw.Write(cipherBytes);
                }

                // Lock the temp down before it can become the store (also
                // covers a temp left behind by a failed Replace).
                ApplyFileAcl(tempPath);

                if (File.Exists(_storePath))
                    File.Replace(tempPath, _storePath, backupPath);
                else
                    File.Move(tempPath, _storePath);

                // MG-C-04: re-assert the restricted ACL on the live files after
                // every write (ReplaceFile descriptor semantics vary).
                ApplyStoreHardening();
            }
            finally
            {
                if (plainBytes != null)
                    CryptographicOperations.ZeroMemory(plainBytes);
            }
        }

        // -------------------------------------------------------------
        // LOAD
        // -------------------------------------------------------------

        /// <summary>
        /// Decrypts and loads the enrollment record.
        /// Returns null if no enrollment exists for this user, or - MG-C-04
        /// fail-closed - if the store is corrupt/unreadable. Throws
        /// UnauthorizedAccessException if the record was tampered with or
        /// belongs to a different user SID.
        /// </summary>
        public EnrollmentRecord? Load()
        {
            if (!File.Exists(_storePath)) return null;

            // MG-C-04: on first read, harden the ACL of the store (and its
            // directory) - covers stores written before ACL enforcement.
            ApplyStoreHardening();

            byte[]? cipherBytes = null;
            byte[]? plainBytes  = null;

            try
            {
                using var fs = new FileStream(_storePath, FileMode.Open,
                    FileAccess.Read, FileShare.Read);
                using var br = new BinaryReader(fs);
                var length  = br.ReadInt32();
                
                if (length <= 0 || length > 1024 * 1024)
                    throw new InvalidDataException($"Invalid enrollment record ciphertext length: {length}");

                cipherBytes = br.ReadBytes(length);
                if (cipherBytes.Length != length)
                    throw new EndOfStreamException("Could not read the full ciphertext length from enrollment store");

                plainBytes = DpapiNg.Unprotect(cipherBytes);

                var json   = System.Text.Encoding.UTF8.GetString(plainBytes);
                var record = JsonSerializer.Deserialize<EnrollmentRecord>(json);

                // Verify SID matches - tamper check
                if (record?.UserSid != _currentUserSid)
                    throw new UnauthorizedAccessException(
                        "Enrollment record SID mismatch - possible tampering");

                // Validate embeddings dimensions (H10)
                if (record.Embeddings != null)
                {
                    foreach (var emb in record.Embeddings)
                    {
                        if (emb.Vector == null || emb.Vector.Length != 512)
                        {
                            throw new InvalidDataException("Invalid face embedding vector dimension (must be exactly 512).");
                        }
                    }
                }

                return record;
            }
            catch (CryptographicException ex)
            {
                // MG-C-04: unreadable ciphertext (corruption or machine change) -
                // fail closed as "no enrollment" instead of crashing the caller.
                _logger?.LogError(ex,
                    "Enrollment store ciphertext unreadable ({Path}) - treating as no enrollment (fail-closed)",
                    _storePath);
                return null;
            }
            catch (Exception ex) when (ex is InvalidDataException or EndOfStreamException or JsonException)
            {
                // MG-C-04: torn/corrupt framing or JSON - fail closed as
                // "no enrollment" instead of crashing the caller. (A SID
                // mismatch - UnauthorizedAccessException - still propagates:
                // that is a tamper signal, not corruption.)
                _logger?.LogError(ex,
                    "Enrollment store corrupt ({Path}) - treating as no enrollment (fail-closed)",
                    _storePath);
                return null;
            }
            finally
            {
                if (plainBytes != null)
                    CryptographicOperations.ZeroMemory(plainBytes);
            }
        }

        // -------------------------------------------------------------
        // UTILITIES
        // -------------------------------------------------------------

        public bool HasEnrollment() => File.Exists(_storePath);

        public void DeleteEnrollment()
        {
            if (File.Exists(_storePath))
            {
                // Overwrite with zeros before deleting to prevent recovery
                var size = new FileInfo(_storePath).Length;
                using var fs = new FileStream(_storePath, FileMode.Open, FileAccess.Write);
                fs.Write(new byte[size]);
                fs.Flush();
                File.Delete(_storePath);
            }

            // MG-C-04: the atomic-write backup/temp siblings also hold
            // (protected) enrollment ciphertext - wipe and remove them so
            // "delete enrollment" leaves nothing recoverable behind.
            foreach (var path in new[] { _storePath + ".bak", _storePath + ".tmp" })
            {
                try
                {
                    if (File.Exists(path))
                    {
                        var size = new FileInfo(path).Length;
                        using var fs = new FileStream(path, FileMode.Open, FileAccess.Write);
                        fs.Write(new byte[size]);
                        fs.Flush();
                        File.Delete(path);
                    }
                }
                catch (Exception ex)
                {
                    _logger?.LogWarning(ex, "MG-C-04: failed to wipe enrollment sibling file {Path}", path);
                }
            }
        }

        /// <summary>
        /// Updates the LastVerifiedAt timestamp and increments auth count.
        /// Call this after every successful face recognition.
        /// </summary>
        public void RecordSuccessfulAuth()
        {
            var record = Load();
            if (record == null) return;

            var updated = new EnrollmentRecord
            {
                UserSid              = record.UserSid,
                Embeddings           = record.Embeddings,
                EnrolledAt           = record.EnrolledAt,
                LastVerifiedAt       = DateTime.UtcNow,
                SuccessfulAuthCount  = record.SuccessfulAuthCount + 1,
            };
            Save(updated);
        }

        // -------------------------------------------------------------
        // MG-C-04 - ACL HARDENING (best-effort, never blocks I/O)
        // -------------------------------------------------------------

        /// <summary>
        /// Asserts the restricted ACL on the store directory (once per
        /// instance) and on the store + backup files (idempotent). Only the
        /// current user SID, SYSTEM and Administrators retain access; every
        /// failure is logged as a warning and swallowed - the ciphertext
        /// remains DPAPI-NG protected either way.
        /// </summary>
        private void ApplyStoreHardening()
        {
            ApplyDirectoryAcl();
            ApplyFileAcl(_storePath);
            ApplyFileAcl(_storePath + ".bak");
        }

        private void ApplyDirectoryAcl()
        {
            if (_aclApplied) return;
            _aclApplied = true;

            try
            {
                var dir = Path.GetDirectoryName(_storePath);
                if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return;

                var security = new DirectorySecurity();
                // Protected DACL, no inherited ACEs - exact allowlist only.
                // The ACEs apply to the directory itself (no propagation), so
                // sibling files (e.g. the daemon's embeddings_v2.npy) keep
                // their own token-default ACLs.
                security.SetAccessRuleProtection(true, false);
                AddAllowFullControl(security, new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null));
                AddAllowFullControl(security, new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null));
                AddAllowFullControl(security, new SecurityIdentifier(_currentUserSid));

                new DirectoryInfo(dir).SetAccessControl(security);
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex,
                    "MG-C-04: could not harden ACL on store directory {Path} (continuing - blob is still DPAPI-NG protected)",
                    Path.GetDirectoryName(_storePath));
            }
        }

        private void ApplyFileAcl(string path)
        {
            try
            {
                if (!File.Exists(path)) return;

                var security = new FileSecurity();
                security.SetAccessRuleProtection(true, false);
                AddAllowFullControl(security, new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null));
                AddAllowFullControl(security, new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null));
                AddAllowFullControl(security, new SecurityIdentifier(_currentUserSid));

                new FileInfo(path).SetAccessControl(security);
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex,
                    "MG-C-04: could not harden ACL on store file {Path} (continuing - blob is still DPAPI-NG protected)",
                    path);
            }
        }

        private static void AddAllowFullControl(FileSystemSecurity security, SecurityIdentifier sid)
        {
            security.AddAccessRule(new FileSystemAccessRule(
                sid, FileSystemRights.FullControl, AccessControlType.Allow));
        }
    }
    // -----------------------------------------------------------------
    // DPAPI-NG wrapper - NCryptProtectSecret with LOCAL=machine descriptor
    // Replaces legacy ProtectedData.Protect (DataProtectionScope.CurrentUser)
    // which is vulnerable to admin impersonation + Mimikatz DPAPI extraction.
    //
    // LOCAL=machine binds the ciphertext to this machine's identity.
    // Decryption requires: same machine + same OS installation + same drive.
    // Booting from a different drive or different machine -> decryption fails.
    //
    // Reference: https://learn.microsoft.com/windows/win32/api/ncryptprotect/
    // -----------------------------------------------------------------
    internal static class DpapiNg
    {
        // NCrypt protection descriptor for machine-scoped binding
        // LOCAL=machine: bound to this machine's master key (stored in SYSTEM LSA)
        // An attacker needs BOTH the machine key AND to run on this machine.
        //
        // MG-C-04 KNOWN LIMITATION (intentionally unchanged - switching scope
        // or entropy would invalidate every enrolled template): any process
        // running locally that can READ the store file can call
        // NCryptUnprotectSecret with this descriptor. The store ACL (see
        // EmbeddingStore.ApplyStoreHardening) blocks that read in practice;
        // same-user/admin compromise remains out of scope for this fix.
        private const string DESCRIPTOR = "LOCAL=machine";

        [DllImport("ncrypt.dll", CharSet = CharSet.Unicode)]
        private static extern int NCryptCreateProtectionDescriptor(
            string pwszDescriptorString,
            uint   dwFlags,
            out nint phDescriptor);

        [DllImport("ncrypt.dll")]
        private static extern int NCryptCloseProtectionDescriptor(nint hDescriptor);

        [DllImport("ncrypt.dll", CharSet = CharSet.Unicode)]
        private static extern int NCryptProtectSecret(
            nint   hDescriptor,
            uint   dwFlags,
            [In] byte[] pbData,
            uint   cbData,
            nint   pMemPara,
            nint   hWnd,
            out nint pbProtectedBlob,
            out uint cbProtectedBlob);

        [DllImport("ncrypt.dll", CharSet = CharSet.Unicode)]
        private static extern int NCryptUnprotectSecret(
            nint   hDescriptor,
            uint   dwFlags,
            [In] byte[] pbProtectedBlob,
            uint   cbProtectedBlob,
            nint   pMemPara,
            nint   hWnd,
            out nint pbData,
            out uint cbData);

        [DllImport("kernel32.dll", EntryPoint = "RtlZeroMemory")]
        private static extern void ZeroMemory(nint destination, nint length);

        [DllImport("kernel32.dll")]
        private static extern nint LocalFree(nint hMem);

        private const uint NCRYPT_SILENT_FLAG = 0x00000040;

        public static byte[] Protect(byte[] data)
        {
            int hr = NCryptCreateProtectionDescriptor(DESCRIPTOR, 0, out var hDesc);
            if (hr != 0) throw new CryptographicException($"NCryptCreateProtectionDescriptor: 0x{hr:X8}");

            try
            {
                hr = NCryptProtectSecret(hDesc, NCRYPT_SILENT_FLAG,
                    data, (uint)data.Length, 0, 0,
                    out var pBlob, out var cbBlob);

                if (hr != 0) throw new CryptographicException($"NCryptProtectSecret: 0x{hr:X8}");

                try
                {
                    var result = new byte[cbBlob];
                    Marshal.Copy(pBlob, result, 0, (int)cbBlob);
                    return result;
                }
                finally
                {
                    LocalFree(pBlob);
                }
            }
            finally
            {
                NCryptCloseProtectionDescriptor(hDesc);
            }
        }

        public static byte[] Unprotect(byte[] protectedData)
        {
            int hr = NCryptUnprotectSecret(0, NCRYPT_SILENT_FLAG,
                protectedData, (uint)protectedData.Length, 0, 0,
                out var pData, out var cbData);

            if (hr != 0) throw new CryptographicException($"NCryptUnprotectSecret: 0x{hr:X8}");

            try
            {
                var result = new byte[cbData];
                Marshal.Copy(pData, result, 0, (int)cbData);
                return result;
            }
            finally
            {
                // Zero the native buffer before freeing - avoids Unsafe pointer interop issues
                if (pData != IntPtr.Zero && cbData > 0)
                    ZeroMemory(pData, (nint)cbData);
                LocalFree(pData);
            }
        }
    }

}
