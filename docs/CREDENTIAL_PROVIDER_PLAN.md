# MajestyGuard Credential Provider Plan

Status: read-only preparation. No Credential Provider registration, test signing,
SafeBoot changes, registry hardening, or service install has been performed from
this document.

## 1. Staged Package Contents

Staged package path:

`C:\tmp\MajestyGuard\build\staged`

Important root payloads:

- `Install.ps1` and `Uninstall.ps1`
- `MajestyGuard.CredentialProvider.dll`
- `MajestyGuard.Service.exe`
- `MajestyGuard.Overlay.exe`
- `MajestyGuard.Core.dll`
- `MajestyGuard.DpapiHelper.exe`
- WinUI/.NET self-contained runtime files such as `coreclr.dll`, `hostfxr.dll`,
  `Microsoft.UI.Xaml.*`, `Microsoft.WindowsAppRuntime.*`, `System.*`, and locale
  resource folders.

Staged CV payload:

- `CVEngine\cv_server.py`
- `CVEngine\face_engine.py`
- `CVEngine\liveness_detector.py`
- `CVEngine\attention_detector.py`
- `CVEngine\depth_liveness.py`
- `CVEngine\rppg_detector.py`
- `CVEngine\virtual_camera_detector.py`
- `CVEngine\enrollment.py`
- `CVEngine\enroll_from_jpegs.py`
- `CVEngine\download_models.py`
- `CVEngine\requirements.txt`

The staged package is the original fallback stack, not the newer v2 proving
ground. Before login integration, the safer path is to port or bridge the v2
hardening work into this fallback stack deliberately.

## 2. What The Credential Provider DLL Does

Source:

`C:\tmp\MajestyGuard\src\MajestyGuard.CredentialProvider\MajestyCredentialProvider.cpp`

The DLL implements a Windows COM Credential Provider with CLSID:

`{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}`

Current behavior:

- Registers as an in-process COM server and Windows Credential Provider.
- Supports `CPUS_LOGON` and `CPUS_UNLOCK_WORKSTATION`.
- Connects to `\\.\pipe\MajestyGuard_CredProv`.
- Sends an initial JSON hello:
  `{"cmd":"CredProvConnected"}`
- Starts a background pipe reader when the tile is selected.
- Waits for newline-delimited JSON containing `MessageType:"AuthDecision"`.
- Treats `Granted:true` as face authorization.
- Updates LogonUI status text to "Welcome back, your majesty".
- Calls Credential Provider event methods so LogonUI asks for serialization.

Important security note:

The current CP is a gatekeeper, not a complete passwordless unlock. Its active
`GetSerialization()` path intentionally does not read or store a password. On
face success it returns `CPGSR_NO_CREDENTIAL_FINISHED` with the status text
"Face recognized - enter your password to confirm". There is older password
serialization helper code in the file, but the current `GetSerialization()` path
returns before using it.

## 3. IPC Protocol

The fallback architecture is:

`Python CVEngine <-> Windows Service <-> Credential Provider`

The CP does not directly call the Python daemon. The service is the broker.

Pipe names from `AppConfig.cs`:

- `MajestyGuard_CV`
- `MajestyGuard_Overlay`
- `MajestyGuard_CredProv`

Pipe implementation:

- `MajestyPipeServer` creates restricted named pipes.
- Allowed pipe clients are LocalSystem and the enrolled user SID when configured.
- Messages are UTF-8, newline-delimited JSON.

CVEngine to Service, from `cv_server.py`:

```json
{
  "MessageType": "DetectionResult",
  "FaceCount": 1,
  "PrimaryUserPresent": true,
  "RecognitionScore": 0.88,
  "LivenessScore": 0.81,
  "LivenessPassed": true,
  "VirtualCameraDetected": false,
  "CameraObstructed": false,
  "InferenceMs": 44.2
}
```

Service to Credential Provider, from `IpcMessage.cs`:

```json
{
  "MessageType": "AuthDecision",
  "Granted": true,
  "Reason": "FaceMatch"
}
```

Credential Provider to Service:

- C++ currently sends `{"cmd":"CredProvConnected"}` as a raw hello.
- `IpcMessage.cs` defines a typed `ManualFallbackRequest` message, but the C++
  fallback command link currently sets local fallback UI state rather than
  sending that typed message.

The service sends `AuthDecision` only when the state transition is:

- previous state: `Verifying`
- current state: `Unlocked`
- trigger: `FaceRecognized`

This prevents manual fallback from becoming an auth bypass.

## 4. What `MG_ENABLE_LOCK=1` Does

This environment variable belongs to the v2 Python daemon in:

`C:\tmp\MajestyGuard-v2\daemon\main.py`

When `MG_ENABLE_LOCK` is unset or `0`, `lock_workstation()` logs:

`LOCK SUPPRESSED (set MG_ENABLE_LOCK=1 to enable real locking)`

When `MG_ENABLE_LOCK=1`, the v2 daemon calls:

`ctypes.windll.user32.LockWorkStation()`

The call occurs when the v2 daemon reaches `LOCKED` or `SOCIAL_LOCK`.

This is separate from the original fallback stack. The original service uses
overlay states for `InactivityLock`, `HostileLock`, and `SocialLock`; comments in
`Worker.cs` state that regular lock states are overlay-only and do not stop
background tasks. Therefore, while CP integration is being validated, keep v2
`MG_ENABLE_LOCK=0` and avoid running two independent lock controllers.

## 5. Minimum Dev Registration Steps

Do not run these until rollback has been manually verified and the user confirms.

Safe staging only:

```powershell
cd C:\tmp\MajestyGuard\build\staged
.\Install.ps1 -AcknowledgeLoginRisk -SkipPythonSetup -SkipModelDownload
```

Service-only dev test after staging and rollback verification:

```powershell
cd C:\tmp\MajestyGuard\build\staged
.\Install.ps1 -AcknowledgeLoginRisk -InstallService -StartServiceAfterInstall -SkipPythonSetup -SkipModelDownload
```

Credential Provider registration step, final-risk phase only:

```powershell
cd C:\tmp\MajestyGuard\build\staged
.\Install.ps1 -AcknowledgeLoginRisk -EnableCredentialProvider -InstallService -StartServiceAfterInstall -SkipPythonSetup -SkipModelDownload
```

If the CP DLL is unsigned and Windows requires a signed CP, the installer has an
explicit `-EnableTestSigning` switch. That switch runs `bcdedit /set testsigning on`,
creates a local code-signing certificate, trusts it, signs the CP DLL, and
requires reboot. This must be treated as a separate risk decision.

High-risk switches that should remain off until specifically confirmed:

- `-EnableTestSigning`
- `-AutoStartOverlay`
- `-EnableRegistryHardening`
- `-EnableSafeBoot`

## 6. Rollback Plan

Rollback script copied by the installer:

`C:\Program Files\MajestyGuard\Uninstall.ps1`

Primary rollback command:

```powershell
& "C:\Program Files\MajestyGuard\Uninstall.ps1"
```

If test signing was enabled:

```powershell
& "C:\Program Files\MajestyGuard\Uninstall.ps1" -DisableTestSigning
```

If screensaver settings were altered by registry hardening:

```powershell
& "C:\Program Files\MajestyGuard\Uninstall.ps1" -RestoreScreensaver
```

Rollback actions implemented in `Uninstall.ps1`:

- Stops and deletes `MajestyGuardService`.
- Runs `regsvr32 /s /u` on `MajestyGuard.CredentialProvider.dll`.
- Removes the CP registry key under Windows Credential Providers.
- Removes the COM CLSID key.
- Removes overlay autostart.
- Removes scheduled task `MajestyGuard_ServiceGuard`.
- Removes MajestyGuard SafeBoot entries for Minimal and Network modes.
- Removes outbound firewall rule.
- Optionally disables test signing.
- Optionally restores screensaver.
- Removes install directory and MajestyGuard AppData.

Before final CP integration, verify rollback from a harmless staged install and
keep one recovery path available that does not depend on the custom CP.

## 7. What Changes When CP Is Active

The CP should not directly trust the v2 daemon. The safer architecture is:

1. Python CV component produces `DetectionResult`.
2. Service state machine validates liveness, identity, profile SID, and state.
3. Service emits `AuthDecision`.
4. Credential Provider updates LogonUI based on that decision.

For production-level integration, choose one of these before CP registration:

- Port v2 CV hardening into `C:\tmp\MajestyGuard\src\MajestyGuard.CVEngine`.
- Or build a compatibility bridge so the v2 daemon emits the same
  `DetectionResult` protocol consumed by the service.

Do not run the v2 daemon's `LockWorkStation()` logic at the same time as the
service/CP stack. CP handles login-screen gating; the service/overlay handles
local screen privacy states.

## 8. Risk Assessment

Test signing risks:

- Enables Windows test-signing mode globally until disabled.
- Requires reboot.
- May show a test mode watermark.
- Weakens normal driver/code-signing expectations while enabled.
- If left enabled, it is a machine-wide security posture change.

Credential Provider registration risks:

- Writes HKLM Credential Provider registration keys.
- Loads custom code in LogonUI context.
- Bad CP behavior can degrade login UX.
- If the CP DLL crashes or hangs, Windows should still have other providers, but
  recovery must be tested before relying on that assumption.
- Current CP returns success when its service pipe is absent, so it should not
  hard-block login solely because the service is unavailable; still, this needs
  real login-screen testing only after rollback is proven.

Registry hardening risks:

- Changes ACLs under the Windows Credential Providers registry root.
- Misconfigured ACLs could make CP cleanup harder.
- Keep `-EnableRegistryHardening` off until CP behavior is proven.

SafeBoot risks:

- Adds MajestyGuard service and CP entries under SafeBoot modes.
- A mistake here could complicate recovery.
- Keep `-EnableSafeBoot` off until the normal rollback path is proven.

Service risks:

- Runs as LocalSystem.
- Owns secure named pipes and launches CV/overlay processes.
- Must not accept unauthenticated state changes.
- Must be tested in dev mode before login-screen integration.

Current blocker before CP work:

- v2 live validation is incomplete because the last owner preflight found zero
  usable face samples. Camera/liveness/daemon validation must pass with
  `MG_ENABLE_LOCK=0` before any Credential Provider registration.
