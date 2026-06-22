# MajestyGuard — Code Signing Roadmap & Release Blueprint

This document defines the code signing requirements, deployment strategy, public policy drafts, SignPath application details, and the release checklist for MajestyGuard.

---

## 🎯 Purpose & Goals

MajestyGuard integrates with critical operating system authentication paths (services, LogonUI, registry configurations, process lifecycles). Windows Smart App Control (SAC) and Code Integrity blocks self-signed or unsigned COM DLLs and services. MajestyGuard will not instruct users to disable SAC or weaken their baseline Windows security settings. Therefore, obtaining a trusted Authenticode signature is the only production-grade route for public distribution.

### Code Signing Objectives:
* **Origin Integrity**: All release binaries must be compiled directly from the public GitHub repository.
* **Auditable Builds**: Code signing must occur within a public, auditable CI/CD workflow (GitHub Actions).
* **Secret Protection**: Code signing keys must never be stored in the repository.
* **No Local Signing Abuse**: Signing must not be used for private, unverified, or locally altered binaries.

---

## 👥 Roles & Responsibilities
* **Project Maintainer**: Kushal
* **Release Approver**: Kushal
* **Automation Handler**: GitHub Actions (planned)

---

## 📦 What May Be Signed
Only official MajestyGuard release artifacts compiled from the main branch of the repository should be signed:
* C# Windows Service binaries.
* WinUI / PyQt UI overlay binaries.
* C++ COM DLL Credential Provider binaries.
* PowerShell installer scripts (`Install.ps1`, `Build.ps1`).

*Note: Upstream third-party binaries (e.g., ONNX model runners) should not be re-signed as MajestyGuard binaries.*

---

## 🛣️ Signing Roadmap & Provider Assessment

### 1. Primary Route: SignPath.io & SignPath Foundation
* **Description**: SignPath.io provides free code-signing certificates to qualifying open-source projects via the SignPath Foundation.
* **Status**: Main path. MajestyGuard is preparing its repository to qualify for this free program.

### 2. Secondary Route: Azure Trusted Signing (Formerly Azure Artifact Signing)
* **Cost**: ~USD 9.99/month.
* **Geographic Constraint**: Microsoft limits individual developer verification to the USA and Canada (organizations are supported in the USA, Canada, EU, and UK). Because the developer is located in India, individual developer registration is currently blocked/uncertain.

### 3. Tertiary Route: Microsoft Store MSIX Packaging
* **Cost**: Free signing for Store-distributed MSIX packages.
* **Constraint**: MSIX sandboxing restricts custom Windows Service registration and COM DLL LogonUI credential provider integration, making it unsuitable for core system-level components. It remains a potential path only for the standalone user-space PyQt overlay UI.

### 4. Windows Hello Companion Device Framework (WHCDF) Route
* **Description**: WHCDF allows companion device authentication (via Bluetooth/biometrics) but requires the restricted `secondaryAuthenticationFactor` capability which must be onboarded and signed by Microsoft.

---

## 📋 SignPath Eligibility & Gaps

To qualify for SignPath Foundation signing, MajestyGuard must satisfy these requirements:
* **OSI-Approved License**: Must apply a standard license (applied: MIT License).
* **No Proprietary Code**: All code in signed packages must be open-source.
* **Public Repository**: Source code must be hosted publicly on GitHub.
* **MFA Required**: Multi-factor authentication must be enabled on GitHub and SignPath accounts.
* **Build Reproducibility**: Binaries must be compiled via reproducible GitHub Actions workflows.
* **Uninstallation**: Installer must provide clear, automated uninstall paths (provided via `Uninstall.ps1`).
* **Documented Features**: Functionality must be documented on the project's home or download page.
* **Code Signing Policy**: Must publish a public code signing policy referencing the SignPath terms.

---

## 📝 SignPath Application Draft
This application form is submitted via HubSpot reCAPTCHA. The maintainer must review and authorize the submission:

```text
Project name:
MajestyGuard

Repository:
https://github.com/onlykushalll/MajestyGuard

Short description:
MajestyGuard is an open-source Windows 11 biometric presence-security project. It combines local webcam-based face recognition, multi-layer passive liveness checks, presence-aware lock behavior, and a Dynamic Island-style desktop UI. The project is intended to protect the user's own Windows session without cloud biometric processing.

What the project does:
MajestyGuard monitors the enrolled user's presence using local camera frames, performs on-device face recognition and liveness checks, and can trigger protective UI/lock states when the user leaves or when an unrecognized face is present. The design goal is a Windows-native Face ID-like experience with local-only biometric processing.

Why signing is needed:
Windows Smart App Control and Code Integrity block locally self-signed service and Credential Provider binaries. Trusted signing is required for safe production validation and eventual distribution without weakening Windows security.

Privacy statement:
MajestyGuard processes biometric camera frames on-device. It does not upload camera frames or biometric templates to cloud services. Runtime frames are used for inference and are not intentionally written to disk. Enrollment artifacts are intended to be encrypted and tied to the Windows user profile.

System changes:
MajestyGuard may install a Windows service, firewall rule, scheduled task, and, in advanced builds, Credential Provider registration. These operations are opt-in, require administrator approval, and are paired with an uninstall script. Main-machine development does not disable Smart App Control.

Uninstall/recovery:
The repository includes Uninstall.ps1, which removes the service, Credential Provider registration, scheduled task, firewall rule, autostart entries, SafeBoot entries, hosts-file entries, and installed files while preserving user data unless explicitly requested.

Security posture:
MajestyGuard is a defensive personal security project. It is not a vulnerability scanner, exploitation framework, malware tool, or bypass toolkit. It is designed to strengthen user-session privacy and presence protection.

Project status:
Active development. The current local repository contains a Python CV/liveness engine, C# service/overlay experiments, C++ Credential Provider work, installer/uninstaller scripts, and architecture documentation. Before application submission, the public repository needs a root license, README, privacy/code-signing policy, release, and verifiable build workflow.
```

---

## 📜 Public Policy & Release Drafts

### 1. Code Signing Policy (Draft)
```markdown
# Code Signing Policy

MajestyGuard uses code signing to help users verify that release binaries were built from the public open-source repository and were not modified after release. Free code signing is provided by SignPath.io, with certificates issued by the SignPath Foundation.

## Scope
This policy applies to all official MajestyGuard release binaries, installers, and DLLs published under the GitHub Releases page.

## Build Integrity
Release binaries must be compiled directly from public source commits using the documented GitHub Actions CI/CD workflows. No private, unverified, or locally altered binaries will be signed.

## Release Approval
Each signing request must be manually approved by the release approver (Kushal) after verifying that all tests pass, the installer rollback scripts are functional, and the privacy policy is current.
```

### 2. Privacy Policy (Draft)
```markdown
# Privacy Policy

MajestyGuard is designed to process biometric data locally on the user's Windows device.

## Biometric Processing
* Camera frames acquired from the webcam are processed locally in RAM for face recognition and liveness detection. Frames are never written to disk or transmitted to remote servers.
* Face embeddings generated during enrollment are encrypted using Windows DPAPI (tied to the local user's security identifier/SID) and stored locally.

## Diagnostic Logs
Troubleshooting logs must not include raw camera frames, biometric templates, passwords, PINs, or private keys.
```

---

## 📋 Required Release Checklist

The release approver must execute and complete this checklist before tagging a new release or requesting code signing:

### 1. Source & Document Readiness
- [ ] All code modifications are committed and pushed to `main`.
- [ ] The root `README.md` is updated to reflect the current build version.
- [ ] The `PRIVACY.md` policy is current.
- [ ] The `CODE_SIGNING_POLICY.md` is current and matches SignPath criteria.
- [ ] Third-party dependencies and open-source licenses are documented.

### 2. Build & Test Gates
- [ ] All C# unit tests pass successfully (`dotnet test`).
- [ ] All Python daemon unit tests pass successfully (`pytest daemon/`).
- [ ] Installer copy-only and deployment smoke tests pass cleanly.
- [ ] Uninstaller rollback validation executes without throwing registry or file access errors.
- [ ] No local logs, biometric templates, ONNX model weights, or local configs are checked in.

### 3. Safety Review
- [ ] All machine-level changes (services, task schedulers, registry) are fully documented.
- [ ] The custom Credential Provider registration is strictly opt-in.
- [ ] OS-level workstation locking (`LockWorkStation`) is disabled by default.
- [ ] Recovery and uninstallation procedures are verified.
- [ ] Smart App Control is not disabled or bypassed during setup.

---

## 📦 Downloads & Release Notes Skeleton (v0.1.0-RC)

Official MajestyGuard downloads are published via GitHub Releases:
[https://github.com/onlykushalll/MajestyGuard/releases](https://github.com/onlykushalll/MajestyGuard/releases)

> [!WARNING]
> MajestyGuard is currently an active research prototype. Until a signed production build is approved and released, it should be treated as an experimental utility and run in evaluation mode rather than a primary secure login provider.

### Release Notes (v0.1.0-RC)
* **Summary**: Initial public release candidate for MajestyGuard.
* **Included**: Python CV/liveness daemon, enrollment flows, PyQt6 floating pill UI, and experimental C# service/Credential Provider modules.
* **Not Included**: Automatic LogonUI unlock. The Credential Provider tile runs as a validation gate and requires password verification upon face match to confirm identity.
* **Safety note**: Do not disable Windows Smart App Control or enable test-signing mode on daily-use machines. Do not turn Smart App Control off on daily-use machines.
