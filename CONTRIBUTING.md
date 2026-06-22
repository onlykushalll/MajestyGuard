# Contributing to MajestyGuard

Thank you for contributing to MajestyGuard! This project implements local-first biometrics, Windows services, and lock-screen security integration, so contributions require diligence around security, performance, and platform safety.

---

## 🛠️ Developer Environment Setup

MajestyGuard is divided into C#/.NET components (Core, Service, Overlay, Companion) and Python components (CVEngine).

### Prerequisites
* Windows 11
* .NET SDK 8.0
* Python 3.11
* Visual Studio Build Tools (with C++ Desktop development workloads for Credential Provider compile tasks)

### C# Setup & Build
1. Open the solution in Visual Studio or build from CLI:
   ```powershell
   dotnet restore .\MajestyGuard.sln
   dotnet build .\MajestyGuard.sln --configuration Debug
   ```
2. Run .NET Unit Tests:
   ```powershell
   dotnet test .\src\MajestyGuard.Tests\MajestyGuard.Tests.csproj
   ```

### Python CVEngine Setup
1. Create and activate a Python virtual environment under the CVEngine directory:
   ```powershell
   cd src\MajestyGuard.CVEngine
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. Run Python Unit Tests:
   ```powershell
   # From repository root (with virtual environment active)
   pytest daemon/
   ```

---

## 📐 Coding Style Standards

### Python (CVEngine / Daemon / UI)
* Follow **PEP 8** style guidelines.
* Maintain clean variable and function naming conventions (use `snake_case`).
* Use type hints for all public functions where possible.
* Use `black` and `flake8` for linting before submitting pull requests.

### C# / .NET
* Follow standard **Microsoft C# Coding Conventions**.
* Use PascalCase for class and method names, camelCase for local variables, and `_camelCase` for private fields.
* Ensure code is cleanly formatted using standard Visual Studio rules (`dotnet format`).

---

## 🚀 Pull Request Guidelines

Before opening a pull request, please run all unit tests and ensure your code complies with local development guidelines.

### PR Requirements
1. **No telemetry/remote logging**: MajestyGuard is built to keep all biometric and authentication data strictly local. Do not add cloud uploading or external API requests for private data.
2. **No committed biometric data**: Never commit face embeddings, local logs, or personal photos to Git. Use the test stubs and mock faces in existing unit tests.
3. **No hardcoded secrets**: Ensure no personal configuration secrets or passwords are left in source code or local settings files.
4. **Matched Installers**: Changes to installation paths or scripts must maintain matching uninstall/cleanup tasks.

### Checklist
- [ ] Code compiles and builds cleanly.
- [ ] All C# and Python tests pass.
- [ ] Code styles match standards (PEP-8, Microsoft C# standards).
- [ ] Security boundaries (DPAPI configurations, file permissions) are not weakened.
