// MajestyGuard.Service/ProcessRestrictor.cs
// Suspends and resumes Win32 and UWP processes during SocialLock.
// Also handles DACL (file permission) restriction/restore and hosts file blocking.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;

namespace MajestyGuard.Service
{
    public class ProcessRestrictor
    {
        private readonly ILogger<ProcessRestrictor> _logger;

        private readonly HashSet<int> _suspendedPids = new();
        private readonly Dictionary<string, DirectorySecurity> _originalDacls = new();
        private readonly Dictionary<string, string> _originalSddl = new();
        private bool _hostsModified;

        private static readonly string _hostsPath =
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                "drivers", "etc", "hosts");

        private static readonly string _journalPath =
            Path.Combine(Path.GetTempPath(), "MajestyGuard_restore_journal.json");

        private const string HostsMarker = "# MajestyGuard SocialLock";

        // ── P/INVOKE ──────────────────────────────────────────────────

        [DllImport("ntdll.dll")]
        private static extern uint NtSuspendProcess(IntPtr processHandle);

        [DllImport("ntdll.dll")]
        private static extern uint NtResumeProcess(IntPtr processHandle);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(
            uint dwDesiredAccess, bool bInheritHandle, int dwProcessId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        private const uint PROCESS_SUSPEND_RESUME = 0x0800;

        // ── COM for UWP suspension ──────────────────────────────────
        [ComImport]
        [Guid("B1AEC16F-2383-4852-B0E9-8F0B1DC66B4D")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IPackageDebugSettings
        {
            int EnableDebugging(
                [MarshalAs(UnmanagedType.LPWStr)] string packageFullName,
                [MarshalAs(UnmanagedType.LPWStr)] string? debuggerPath,
                [MarshalAs(UnmanagedType.LPWStr)] string? environment);

            int DisableDebugging(
                [MarshalAs(UnmanagedType.LPWStr)] string packageFullName);

            int Suspend(
                [MarshalAs(UnmanagedType.LPWStr)] string packageFullName);

            int Resume(
                [MarshalAs(UnmanagedType.LPWStr)] string packageFullName);

            int TerminateAllProcesses(
                [MarshalAs(UnmanagedType.LPWStr)] string packageFullName);
        }

        [ComImport]
        [Guid("F27C3930-8029-4AD1-94E3-3DBA417810C1")]
        private class PackageDebugSettingsClass { }

        public ProcessRestrictor(ILogger<ProcessRestrictor> logger)
        {
            _logger = logger;
            RecoverFromJournalIfNeeded();
        }

        // ─────────────────────────────────────────────────────────────
        // RESTORE JOURNAL — crash-recovery mechanism
        // ─────────────────────────────────────────────────────────────

        private void WriteRestoreJournal()
        {
            var journal = new
            {
                timestamp = DateTime.UtcNow.ToString("o"),
                suspended_pids = _suspendedPids.ToArray(),
                restricted_paths = _originalSddl.ToDictionary(kv => kv.Key, kv => kv.Value),
                hosts_modified = _hostsModified,
            };

            try
            {
                File.WriteAllText(_journalPath, JsonSerializer.Serialize(journal));
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to write restore journal");
            }
        }

        private void DeleteRestoreJournal()
        {
            try { if (File.Exists(_journalPath)) File.Delete(_journalPath); }
            catch { /* best effort */ }
        }

        private void RecoverFromJournalIfNeeded()
        {
            if (!File.Exists(_journalPath)) return;

            _logger.LogWarning("Restore journal found — recovering from previous crash");
            try
            {
                var json = File.ReadAllText(_journalPath);
                using var doc = System.Text.Json.JsonDocument.Parse(json);
                var root = doc.RootElement;

                // Restore DACLs from persisted SDDL
                if (root.TryGetProperty("restricted_paths", out var pathsEl)
                    && pathsEl.ValueKind == System.Text.Json.JsonValueKind.Object)
                {
                    foreach (var prop in pathsEl.EnumerateObject())
                    {
                        var path = prop.Name;
                        var sddl = prop.Value.GetString();
                        if (string.IsNullOrEmpty(sddl) || !Directory.Exists(path)) continue;

                        try
                        {
                            var dirInfo = new DirectoryInfo(path);
                            var restored = new DirectorySecurity();
                            restored.SetSecurityDescriptorSddlForm(sddl, AccessControlSections.Access);
                            dirInfo.SetAccessControl(restored);
                            _logger.LogInformation("Journal recovery: restored DACL for {Path}", path);
                        }
                        catch (Exception ex)
                        {
                            _logger.LogError(ex, "Journal recovery: failed to restore DACL for {Path}", path);
                        }
                    }
                }

                RevertHostsFile();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Journal recovery failed");
            }
            finally
            {
                DeleteRestoreJournal();
            }
        }

        // ─────────────────────────────────────────────────────────────
        // WIN32 PROCESS SUSPENSION
        // ─────────────────────────────────────────────────────────────

        public Task SuspendWin32ProcessAsync(string processName)
        {
            // Skip chrome/edge — use hosts file approach instead
            var baseName = Path.GetFileNameWithoutExtension(processName);
            if (baseName is "chrome" or "msedge")
            {
                ApplyHostsBlock();
                return Task.CompletedTask;
            }

            var procs = Process.GetProcessesByName(baseName);

            foreach (var proc in procs)
            {
                try
                {
                    var handle = OpenProcess(PROCESS_SUSPEND_RESUME, false, proc.Id);
                    if (handle == IntPtr.Zero)
                    {
                        _logger.LogWarning("Cannot open process {Name} (PID {Pid})",
                            processName, proc.Id);
                        continue;
                    }

                    var result = NtSuspendProcess(handle);
                    CloseHandle(handle);

                    if (result == 0)
                    {
                        _suspendedPids.Add(proc.Id);
                        _logger.LogInformation("Suspended {Name} (PID {Pid})",
                            processName, proc.Id);
                    }
                    else
                    {
                        _logger.LogWarning("NtSuspendProcess failed for {Name}: 0x{Result:X}",
                            processName, result);
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Error suspending {Name}", processName);
                }
            }

            WriteRestoreJournal();
            return Task.CompletedTask;
        }

        public Task ResumeWin32ProcessAsync(string processName)
        {
            var baseName = Path.GetFileNameWithoutExtension(processName);
            if (baseName is "chrome" or "msedge")
            {
                RevertHostsFile();
                return Task.CompletedTask;
            }

            var procs = Process.GetProcessesByName(baseName);

            foreach (var proc in procs)
            {
                if (!_suspendedPids.Contains(proc.Id)) continue;

                try
                {
                    var handle = OpenProcess(PROCESS_SUSPEND_RESUME, false, proc.Id);
                    if (handle == IntPtr.Zero) continue;

                    NtResumeProcess(handle);
                    CloseHandle(handle);
                    _suspendedPids.Remove(proc.Id);

                    _logger.LogInformation("Resumed {Name} (PID {Pid})", processName, proc.Id);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Error resuming {Name}", processName);
                }
            }

            return Task.CompletedTask;
        }

        // ─────────────────────────────────────────────────────────────
        // UWP PROCESS SUSPENSION
        // ─────────────────────────────────────────────────────────────

        public Task SuspendUwpAppAsync(string packageFamilyName)
        {
            try
            {
                var settings = (IPackageDebugSettings)new PackageDebugSettingsClass();
                // PackageDebugSettings.Suspend accepts either full name or family name
                var hr = settings.Suspend(packageFamilyName);

                if (hr == 0)
                    _logger.LogInformation("Suspended UWP: {Pkg}", packageFamilyName);
                else
                    _logger.LogWarning("UWP suspend returned HRESULT: 0x{HR:X}", hr);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to suspend UWP: {Pkg}", packageFamilyName);
            }

            return Task.CompletedTask;
        }

        public Task ResumeUwpAppAsync(string packageFamilyName)
        {
            try
            {
                var settings = (IPackageDebugSettings)new PackageDebugSettingsClass();
                settings.Resume(packageFamilyName);
                _logger.LogInformation("Resumed UWP: {Pkg}", packageFamilyName);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to resume UWP: {Pkg}", packageFamilyName);
            }

            return Task.CompletedTask;
        }

        // ─────────────────────────────────────────────────────────────
        // HOSTS FILE BLOCKING (for Chrome/Edge Gmail)
        // ─────────────────────────────────────────────────────────────

        private void ApplyHostsBlock()
        {
            if (_hostsModified) return;

            try
            {
                var entries = new[]
                {
                    $"127.0.0.1 accounts.google.com {HostsMarker}",
                    $"127.0.0.1 mail.google.com {HostsMarker}",
                    $"127.0.0.1 web.whatsapp.com {HostsMarker}",
                    $"127.0.0.1 www.instagram.com {HostsMarker}",
                };

                File.AppendAllLines(_hostsPath, entries);
                _hostsModified = true;
                WriteRestoreJournal();
                _logger.LogInformation("Hosts file block applied for Gmail/WhatsApp/Instagram");

                // Flush DNS cache so changes take effect immediately
                Process.Start(new ProcessStartInfo("ipconfig", "/flushdns")
                    { CreateNoWindow = true, UseShellExecute = false })?.WaitForExit(3000);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to modify hosts file");
            }
        }

        private void RevertHostsFile()
        {
            if (!_hostsModified && !File.Exists(_journalPath)) return;

            try
            {
                if (!File.Exists(_hostsPath)) return;

                var lines = File.ReadAllLines(_hostsPath);
                var cleaned = new List<string>();
                foreach (var line in lines)
                {
                    if (!line.Contains(HostsMarker))
                        cleaned.Add(line);
                }

                File.WriteAllLines(_hostsPath, cleaned);
                _hostsModified = false;
                _logger.LogInformation("Hosts file block reverted");

                Process.Start(new ProcessStartInfo("ipconfig", "/flushdns")
                    { CreateNoWindow = true, UseShellExecute = false })?.WaitForExit(3000);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to revert hosts file");
            }
        }

        // ─────────────────────────────────────────────────────────────
        // DACL FILE PATH RESTRICTION
        // ─────────────────────────────────────────────────────────────

        public void RestrictPath(string path)
        {
            try
            {
                var dirInfo  = new DirectoryInfo(path);
                var security = dirInfo.GetAccessControl();

                _originalDacls[path] = security;
                _originalSddl[path] = security.GetSecurityDescriptorSddlForm(AccessControlSections.Access);

                var currentUser = WindowsIdentity.GetCurrent().User!;
                var denyRule = new FileSystemAccessRule(
                    currentUser,
                    FileSystemRights.ReadData | FileSystemRights.ListDirectory,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
                    PropagationFlags.None,
                    AccessControlType.Deny);

                security.AddAccessRule(denyRule);
                dirInfo.SetAccessControl(security);

                WriteRestoreJournal();
                _logger.LogInformation("Restricted access to: {Path}", path);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Failed to restrict path: {Path}", path);
            }
        }

        public void UnrestrictAllPaths()
        {
            foreach (var (path, originalSecurity) in _originalDacls)
            {
                try
                {
                    var dirInfo = new DirectoryInfo(path);
                    dirInfo.SetAccessControl(originalSecurity);
                    _logger.LogInformation("Restored access to: {Path}", path);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "CRITICAL: Failed to restore DACL for {Path}", path);
                }
            }
            _originalDacls.Clear();
            DeleteRestoreJournal();
        }
    }
}
