# MajestyGuard — Lock Screen & Credential Provider Integration

This document outlines the architecture, specifications, security limits, deployment risks, and onboarding procedures for integrating MajestyGuard with Windows session locking, the custom C++ Credential Provider, and the Windows Hello Companion Device Framework (WHCDF).

---

## 🔒 Lock Mode Comparison

MajestyGuard supports two distinct locking mechanisms, optimized for development flexibility and security posture:

### 1. Soft Lock (User Session Overlay)
* **Description**: A full-screen PyQt6 frosted-glass overlay that covers the active desktop.
* **Purpose**: Blocks local keyboard and mouse interaction while allowing background processes (music playback, compilations, downloads, rendering) to run uninterrupted.
  * *Background behavior during soft lock*:
    - Music/audio: continues
    - Downloads: continues
    - Renders/builds: continues
* **Unlock Trigger**: Pressing Space, clicking the screen, or clicking the floating Dynamic Island triggers a webcam verification check. If the enrolled user is recognized, the overlay dissolves.
* **Watchdog Protection**: Setting the environment variable `MG_OVERLAY_WATCHDOG=1` activates a daemon-level watcher. If a local user attempts to kill the UI overlay process, the daemon detects the exit and instantly reinstates the overlay.
* **Security Scope**: User-space protection. It is a convenience shield, not a kernel-enforced Windows secure desktop. It does not protect against a local administrator who can terminate the python daemon directly.

### 2. Windows Lock Screen (`LockWorkStation`)
* **Description**: Secure OS-enforced lock state delegating authentication control back to Windows LogonUI.
* **Usage**: Invoked instantly during a `HOSTILE_LOCK` state (triggered by suspicious spoofing, camera obstruction, or repeated verification failures), manual hotkeys, or emergency lockdowns.
* **Execution**: Triggered via:
  ```python
  import ctypes
  ctypes.windll.user32.LockWorkStation()
  ```
  *(Note: This call is enabled in the Python daemon only when the environment variable `MG_ENABLE_LOCK=1` is set. If unset or `0`, the lock is suppressed and logged for safety).*

---

## 🏗️ Credential Provider Architecture

To support seamless face-based unlock on the Windows LogonUI (Lock Screen), MajestyGuard implements a custom C++/COM Credential Provider DLL.

```text
Python CV Daemon  <--->  C# Windows Service  <--->  C++ Credential Provider
(Biometric Engine)        (Coordinator / Broker)     (LogonUI Tile Extension)
```

### 1. Security Gated Named Pipe IPC
The C++ Credential Provider does not communicate with the Python daemon directly. The background C# Service acts as the broker, communicating via restricted named pipes:

* **Named Pipes**:
  - `\\.\pipe\MajestyGuard_CV`: Receives real-time recognition results from the Python CV daemon.
  - `\\.\pipe\MajestyGuard_Overlay`: Sends overlay visual commands from the Service to the PyQt UI.
  - `\\.\pipe\MajestyGuard_CredProv`: Transmits authorization decisions from the Service to the C++ LogonUI tile.
* **Access Rights**: Sockets and pipe handles are ACL-restricted to `LocalSystem` and the enrolled user's SID to prevent unprivileged token hijacking.
* **Pipe Server Configuration**: Defined in `Core/IPC/PipeServer.cs` and `AppConfig.cs`. Uses `PipeOptions.Asynchronous` on both ends to prevent blocking execution.

### 2. IPC Message Protocol
Messages are newline-delimited, UTF-8 encoded JSON objects.

#### A. CV Daemon to Service (`MessageType: DetectionResult`)
Sent by the Python daemon to report tracking and liveness metrics:
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

#### B. Service to Credential Provider (`MessageType: AuthDecision`)
Sent by the C# Service to instruct the Credential Provider:
```json
{
  "MessageType": "AuthDecision",
  "Granted": true,
  "Reason": "FaceMatch"
}
```
* **Authorization Gate**: The service emits `AuthDecision: Granted` only when a verified transition (`Verifying -> Unlocked` triggered by `FaceRecognized`) occurs, preventing authentication bypasses on manual state manipulations.

#### C. Credential Provider to Service
The C++ DLL sends a handshake packet upon LogonUI selection:
```json
{
  "cmd": "CredProvConnected"
}
```

### 3. LogonUI Tile Logic
The C++ DLL (CLSID: `{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}`) implements standard Windows COM Credential Provider interfaces supporting `CPUS_LOGON` and `CPUS_UNLOCK_WORKSTATION`:
* Selecting the MajestyGuard logon tile launches a background named pipe reader thread listening for authorization decisions.
* **Security Guardrail**: The CP is a verification gate, not a complete password/PIN bypass. The `GetSerialization()` callback returns `CPGSR_NO_CREDENTIAL_FINISHED` and updates status text to:
  `"Face recognized - enter your password to confirm"`
  This avoids storing plaintext credentials on disk, forcing a manual confirmation before unlocking the user profile.

---

## 💻 Windows Hello Companion Device Framework (WHCDF)

The Windows Hello Companion Device Framework allows companion apps to unlock a PC using external authentication (such as a phone, security key, or local biometric check). It requires the restricted `secondaryAuthenticationFactor` app capability, which must be onboarded and approved by Microsoft.

### WHCDF Technical Constraints
* **Challenge/Response**: The authentication flow must use HMAC challenge/response rather than plaintext secrets.
* **Local Biometrics**: Face templates, AdaFace embeddings, and ONNX models must reside and execute strictly on-device; no cloud transmission is permitted.
* **Fail-Closed Design**: If the companion app, service, or pipe server fails, Windows Hello falls back to standard PIN/password entry.

### Onboarding Application Draft
To request Microsoft onboarding approval for UWP package capabilities:

```text
To: cdfonboard@microsoft.com
Subject: Request for secondaryAuthenticationFactor capability onboarding - MajestyGuard

Hello Microsoft Companion Device Framework onboarding team,

I would like to request guidance and approval for using the restricted secondaryAuthenticationFactor capability for a Windows Hello companion device app.

Project name: MajestyGuard
Developer/applicant: [your full legal name]
Developer account / Partner Center account: [your Microsoft developer account email]
Publisher name: [individual / company name]
Target platform: Windows 11 (UWP companion app plus local Windows service/CV components)
Capability requested: secondaryAuthenticationFactor

Project summary:
MajestyGuard is a local-only Windows security system that uses face recognition, passive liveness checks, and a companion-device style authentication flow to protect a user's Windows profile. The intended WHCDF companion app would use the Windows.Security.Authentication.Identity.Provider SecondaryAuthenticationFactor APIs and HMAC-based challenge/response to authorize unlock only when the enrolled user is freshly recognized with liveness.

Security notes:
- All face recognition and liveness inference is local-only.
- Camera frames are not uploaded.
- The companion flow uses HMAC challenge/response, not plaintext secrets.
- The app is being developed with explicit rollback and recovery procedures before any lock-screen integration.

Could you please confirm whether WHCDF / secondaryAuthenticationFactor onboarding is still available for new apps, and what documentation, Partner Center account details, package identity, hardware/security review materials, or Store submission requirements are needed for approval?

Thank you,
[your full legal name]
```

---

## 🛠️ Installation, Registration & Rollback

### Staged Installation (Developer Verification)
Do not install or register the Credential Provider on your primary system without verification. Perform a staged install first:

1. **Staged User-Space Only Install** (safe, no service or logon changes):
   ```powershell
   .\Install.ps1 -AcknowledgeLoginRisk -SkipPythonSetup -SkipModelDownload
   ```
2. **Windows Service Install** (runs background broker without LogonUI hooks):
   ```powershell
   .\Install.ps1 -AcknowledgeLoginRisk -InstallService -StartServiceAfterInstall -SkipPythonSetup -SkipModelDownload
   ```
3. **Full Credential Provider Registration** (registers tile, requires reboot):
   ```powershell
   .\Install.ps1 -AcknowledgeLoginRisk -EnableCredentialProvider -InstallService -StartServiceAfterInstall -SkipPythonSetup -SkipModelDownload
   ```

*Note: For unsigned binary validation, `-EnableTestSigning` enables Windows test-signing mode globally (`bcdedit /set testsigning on`), creates a local self-signed certificate, and signs the DLL.*

### Safe Rollback Procedure
If LogonUI behavior degrades or fails, run the uninstaller directly from the command line:
```powershell
& "C:\Program Files\MajestyGuard\Uninstall.ps1"
```
Or if test signing or registry hardening was enabled during installation:
```powershell
& "C:\Program Files\MajestyGuard\Uninstall.ps1" -DisableTestSigning -RestoreScreensaver
```

#### Uninstall Actions:
* Stops and deletes the `MajestyGuardService`.
* Unregisters `MajestyGuard.CredentialProvider.dll` via `regsvr32 /s /u`.
* Deletes Credential Provider registry keys and CLSID COM entries.
* Removes scheduled task watchdogs and Startup folder launchers.
* Restores default SafeBoot and screensaver registry settings.
* Deletes the installation directory and local AppData caches.

---

## ⚠️ Safety & Risk Assessment

Before enabling LogonUI integrations on your physical machine, evaluate the following risks:

### 1. Test-Signing Risk
Enabling globally (`-EnableTestSigning`) weakens kernel integrity checks. This allows any unsigned driver to run, altering the machine's baseline security posture. Keep test-signing disabled on daily-use machines.

### 2. Credential Provider Context Risk
The Credential Provider DLL loads directly inside the high-privilege `LogonUI.exe` system process. Any unhandled crash or deadlocks in the C++ pipe reader could crash the lock screen, requiring recovery console boot or Safe Mode rollback.

### 3. Registry Hardening & ACLs
Modifying system ACLs under the HKLM Credential Providers registry root can block cleanup if registry permissions are corrupted. Keep registry hardening disabled until baseline stability is verified in a Virtual Machine.

### 4. SafeBoot Lockout Risk
Adding the MajestyGuard Service under SafeBoot startup lists ensures presence locking runs even in diagnostics modes. However, if the camera driver fails to load in Safe Mode, you could lock yourself out of the recovery environment. Ensure standard PIN/password fallbacks are always functional.

### 5. Smart App Control Risk
Windows Smart App Control blocks self-signed COM DLLs and services. MajestyGuard will not bypass or disable Smart App Control.
