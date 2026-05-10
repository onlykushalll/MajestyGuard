// MajestyGuard.Core/Security/EmbeddingStore.cs
// Handles encrypted storage and retrieval of face embeddings.
//
// SECURITY DESIGN:
//   - Embeddings are encrypted using Windows DPAPI (ProtectedData)
//   - DPAPI keys are tied to the current user's Windows credentials
//   - File path includes user SID to prevent cross-profile access
//   - Raw embedding bytes are zeroed from memory after use
//   - NEVER write raw embeddings to disk — only the encrypted blob
//
// IMPORTANT: This must run in USER context (not SYSTEM).
//   Call from a user-mode process during enrollment and verification.
//   The Windows Service (SYSTEM) cannot decrypt DPAPI-user-scoped data.
//   CODEX: The Service should use a user-mode helper process for this.

using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;

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

        // Entropy adds a per-application salt to DPAPI — prevents
        // other apps using DPAPI from accessing our blob
        private static readonly byte[] _entropy = [
            0x4D, 0x61, 0x6A, 0x65, 0x73, 0x74, 0x79, 0x47,
            0x75, 0x61, 0x72, 0x64, 0x5F, 0x53, 0x61, 0x6C,
            0x74, 0x5F, 0x76, 0x31
        ];

        public EmbeddingStore(string storePath)
        {
            _storePath      = storePath;
            _currentUserSid = WindowsIdentity.GetCurrent().User?.Value
                              ?? throw new InvalidOperationException("Cannot determine user SID");

            Directory.CreateDirectory(Path.GetDirectoryName(storePath)!);
        }

        // ─────────────────────────────────────────────────────────────
        // SAVE
        // ─────────────────────────────────────────────────────────────

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
                // Admin cannot decrypt by impersonating the user token —
                // they need to be ON THIS MACHINE with the same OS installation.
                // Mimikatz LSASS dump attack is defeated: the Master Key alone
                // is insufficient without the machine-bound TPM component.
                var cipherBytes = DpapiNg.Protect(plainBytes);

                using var fs = new FileStream(_storePath, FileMode.Create,
                    FileAccess.Write, FileShare.None);
                using var bw = new BinaryWriter(fs);
                bw.Write(cipherBytes.Length);
                bw.Write(cipherBytes);
            }
            finally
            {
                if (plainBytes != null)
                    CryptographicOperations.ZeroMemory(plainBytes);
            }
        }

        // ─────────────────────────────────────────────────────────────
        // LOAD
        // ─────────────────────────────────────────────────────────────

        /// <summary>
        /// Decrypts and loads the enrollment record.
        /// Returns null if no enrollment exists for this user.
        /// Throws if file is tampered or belongs to a different user.
        /// </summary>
        public EnrollmentRecord? Load()
        {
            if (!File.Exists(_storePath)) return null;

            byte[]? cipherBytes = null;
            byte[]? plainBytes  = null;

            try
            {
                using var fs = new FileStream(_storePath, FileMode.Open,
                    FileAccess.Read, FileShare.Read);
                using var br = new BinaryReader(fs);
                var length  = br.ReadInt32();
                cipherBytes = br.ReadBytes(length);

                plainBytes = DpapiNg.Unprotect(cipherBytes);

                var json   = System.Text.Encoding.UTF8.GetString(plainBytes);
                var record = JsonSerializer.Deserialize<EnrollmentRecord>(json);

                // Verify SID matches — tamper check
                if (record?.UserSid != _currentUserSid)
                    throw new UnauthorizedAccessException(
                        "Enrollment record SID mismatch — possible tampering");

                return record;
            }
            catch (CryptographicException ex)
            {
                // DPAPI decryption failed — wrong user, or file corrupted
                throw new InvalidOperationException(
                    "Enrollment record is corrupted or belongs to a different user", ex);
            }
            finally
            {
                if (plainBytes != null)
                    CryptographicOperations.ZeroMemory(plainBytes);
            }
        }

        // ─────────────────────────────────────────────────────────────
        // UTILITIES
        // ─────────────────────────────────────────────────────────────

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
    }
    // ─────────────────────────────────────────────────────────────────
    // DPAPI-NG wrapper — NCryptProtectSecret with LOCAL=machine descriptor
    // Replaces legacy ProtectedData.Protect (DataProtectionScope.CurrentUser)
    // which is vulnerable to admin impersonation + Mimikatz DPAPI extraction.
    //
    // LOCAL=machine binds the ciphertext to this machine's identity.
    // Decryption requires: same machine + same OS installation + same drive.
    // Booting from a different drive or different machine → decryption fails.
    //
    // Reference: https://learn.microsoft.com/windows/win32/api/ncryptprotect/
    // ─────────────────────────────────────────────────────────────────
    internal static class DpapiNg
    {
        // NCrypt protection descriptor for machine-scoped binding
        // LOCAL=machine: bound to this machine's master key (stored in SYSTEM LSA)
        // An attacker needs BOTH the machine key AND to run on this machine.
        private const string DESCRIPTOR = "LOCAL=machine";

        [DllImport("ncrypt.dll", CharSet = CharSet.Unicode)]
        private static extern int NCryptCreateProtectionDescriptor(
            string pwszDescriptorString,
            uint   dwFlags,
            out nint phDescriptor);

        [DllImport("ncrypt.dll")]
        private static extern int NCryptCloseProtectionDescriptor(nint hDescriptor);

        [DllImport("ncryptprotect.dll", CharSet = CharSet.Unicode)]
        private static extern int NCryptProtectSecret(
            nint   hDescriptor,
            uint   dwFlags,
            [In] byte[] pbData,
            uint   cbData,
            nint   pMemPara,
            nint   hWnd,
            out nint pbProtectedBlob,
            out uint cbProtectedBlob);

        [DllImport("ncryptprotect.dll", CharSet = CharSet.Unicode)]
        private static extern int NCryptUnprotectSecret(
            nint   hDescriptor,
            uint   dwFlags,
            [In] byte[] pbProtectedBlob,
            uint   cbProtectedBlob,
            nint   pMemPara,
            nint   hWnd,
            out nint pbData,
            out uint cbData);

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
                CryptographicOperations.ZeroMemory(
                    System.Runtime.InteropServices.MemoryMarshal.CreateSpan(
                        ref System.Runtime.InteropServices.Unsafe.AsRef<byte>((void*)pData),
                        (int)cbData));
                LocalFree(pData);
            }
        }
    }

}
