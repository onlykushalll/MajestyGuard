# MajestyGuard Packaging and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reproducible current-user Windows installer and uninstaller for the active MajestyGuard user-space application.

**Architecture:** A build script produces a relocatable distribution bundle rather than copying the source virtual environment. An Inno Setup installer deploys that bundle, registers the Control Center at user logon, and offers a conservative uninstall that preserves enrollment unless the user explicitly opts to remove it.

**Tech Stack:** Python 3.11, PyInstaller, Inno Setup 6, PowerShell, pytest.

**Spec:** `docs/superpowers/specs/2026-09-19-desktop-app-and-installer-design.md`

## Global Constraints

- Install per user without a Windows service, administrator elevation, SafeBoot changes, firewall changes, or hosts-file changes.
- Do not copy a source virtual environment into the release bundle.
- The installer does not enable real Windows locking.
- Standard uninstall preserves `%LOCALAPPDATA%\MajestyGuard` enrollment data.
- The installer may be unsigned; user-facing documentation must disclose Windows reputation warnings accurately.

---

### Task 1: Bundle Manifest and Dependency Check

**Files:**
- Create: `packaging/requirements-build.txt`
- Create: `packaging/build_bundle.ps1`
- Create: `tests/packaging/test_bundle_script.py`

**Interfaces:**
- `build_bundle.ps1 -PythonPath <path> -OutputDir <path>` produces `dist/MajestyGuard/`.
- The bundle entry point is `MajestyGuard.exe`, generated from `app/main.py`.

- [ ] **Step 1: Write failing bundle-boundary tests**

```python
def test_bundle_script_builds_a_fresh_distribution_not_a_copied_venv():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert "PyInstaller" in text
    assert ".venv" not in text
    assert "app\\main.py" in text


def test_build_requirements_pin_pyinstaller():
    text = (ROOT / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")

    assert any(line.startswith("pyinstaller==") for line in text.splitlines())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/packaging/test_bundle_script.py -q`

Expected: import or file failure because packaging files do not exist.

- [ ] **Step 3: Implement a deterministic bundle script**

```powershell
& $PythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name MajestyGuard `
    --paths $Root `
    --collect-all mediapipe `
    --collect-all insightface `
    --collect-all onnxruntime `
    $EntryPoint
```

Explicitly collect the daemon, UI, model directories, and the application icon/assets. Fail when any required source directory is missing. Do not invoke a fresh dependency resolver during install.

- [ ] **Step 4: Run bundle-boundary tests**

Run: `python -m pytest tests/packaging/test_bundle_script.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packaging/requirements-build.txt packaging/build_bundle.ps1 tests/packaging/test_bundle_script.py
git commit -m "build: add reproducible application bundle script"
```

### Task 2: Inno Setup Installer and Uninstaller

**Files:**
- Create: `packaging/MajestyGuard.iss`
- Create: `tests/packaging/test_inno_installer.py`

**Interfaces:**
- Installer target is `{localappdata}\Programs\MajestyGuard`.
- Startup target is `MajestyGuard.exe --background`.
- Standard uninstall preserves `%LOCALAPPDATA%\MajestyGuard\embeddings_v2.npy` and biometric data.

- [ ] **Step 1: Write failing installer safety tests**

```python
def test_installer_is_current_user_only_and_starts_control_center():
    text = (ROOT / "packaging" / "MajestyGuard.iss").read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in text
    assert "{localappdata}\\Programs\\MajestyGuard" in text
    assert "MajestyGuard.exe --background" in text


def test_installer_does_not_contain_legacy_privileged_integrations():
    text = (ROOT / "packaging" / "MajestyGuard.iss").read_text(encoding="utf-8").lower()

    assert "credential provider" not in text
    assert "safeboot" not in text
    assert "hosts file" not in text
    assert "new-service" not in text


def test_uninstall_preserves_enrollment_unless_explicitly_selected():
    text = (ROOT / "packaging" / "MajestyGuard.iss").read_text(encoding="utf-8")

    assert "Remove enrollment and diagnostic data" in text
    assert "embeddings_v2.npy" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/packaging/test_inno_installer.py -q`

Expected: file failure because the Inno Setup script does not exist.

- [ ] **Step 3: Implement the Inno Setup script**

```ini
[Setup]
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\MajestyGuard

[Run]
Filename: "{app}\MajestyGuard.exe"; Parameters: "--background"; Flags: nowait postinstall skipifsilent
```

Use `[Run]` or `[Registry]` only for current-user startup, stop known child processes by image path on uninstall, and include a custom uninstall checkbox named `RemoveUserData`. The checkbox alone permits removal of `%LOCALAPPDATA%\MajestyGuard`; default uninstall never removes it.

- [ ] **Step 4: Run installer safety tests**

Run: `python -m pytest tests/packaging/test_inno_installer.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packaging/MajestyGuard.iss tests/packaging/test_inno_installer.py
git commit -m "build: add per-user MajestyGuard installer"
```

### Task 3: Packaging Build Validation

**Files:**
- Modify: `packaging/build_bundle.ps1`
- Modify: `tests/packaging/test_bundle_script.py`

**Interfaces:**
- Script detects `ISCC.exe` only when creating an installer and prints an actionable error when unavailable.
- A bundle smoke test can run `MajestyGuard.exe --help` without opening a camera.

- [ ] **Step 1: Write failing validation tests**

```python
def test_bundle_script_has_an_explicit_no_camera_smoke_test():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert "--help" in text
    assert "MG_ENABLE_LOCK" in text
    assert "0" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/packaging/test_bundle_script.py::test_bundle_script_has_an_explicit_no_camera_smoke_test -q`

Expected: FAIL because the smoke test is absent.

- [ ] **Step 3: Implement the post-build smoke test**

```powershell
$env:MG_ENABLE_LOCK = "0"
& $BundleExe --help
if ($LASTEXITCODE -ne 0) { throw "Bundle help smoke test failed." }
```

The entry point must support `--help` and `--background`; help must exit before creating a QApplication, starting a daemon, or opening the camera.

- [ ] **Step 4: Run validation tests and build if toolchain is installed**

Run: `python -m pytest tests/packaging -q`

Expected: PASS.

Then run: `powershell -ExecutionPolicy Bypass -File packaging/build_bundle.ps1`

Expected: a bundle, or a precise dependency/toolchain error with no partial installer reported as successful.

- [ ] **Step 5: Commit**

```bash
git add packaging/build_bundle.ps1 tests/packaging/test_bundle_script.py app/main.py
git commit -m "build: validate packaged MajestyGuard startup"
```

### Task 4: Installer Documentation and Release Gate

**Files:**
- Modify: `README.md`
- Create: `docs/guides/INSTALL_AND_UPDATE.md`
- Create: `tests/packaging/test_install_docs.py`

**Interfaces:**
- Documentation distinguishes installing an unsigned local build from having trusted signing.
- Documentation lists physical stranger and spoof tests as release gates.

- [ ] **Step 1: Write failing release-documentation tests**

```python
def test_install_guide_discloses_unsigned_build_and_user_data_behavior():
    text = (ROOT / "docs" / "guides" / "INSTALL_AND_UPDATE.md").read_text(encoding="utf-8")

    assert "unsigned" in text.lower()
    assert "preserves enrollment" in text.lower()
    assert "Smart App Control" in text


def test_install_guide_lists_manual_security_release_gates():
    text = (ROOT / "docs" / "guides" / "INSTALL_AND_UPDATE.md").read_text(encoding="utf-8")

    assert "stranger" in text.lower()
    assert "spoof" in text.lower()
    assert "video" in text.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/packaging/test_install_docs.py -q`

Expected: file failure because the installation guide does not exist.

- [ ] **Step 3: Write user-facing installation and update documentation**

Document installed paths, first run, startup, pause, update behavior, standard versus data-removal uninstall, unsigned-build disclosure, trusted-signing future path, and all live security gates. Do not provide bypass instructions for Smart App Control or Windows sign-in.

- [ ] **Step 4: Run documentation tests**

Run: `python -m pytest tests/packaging/test_install_docs.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/guides/INSTALL_AND_UPDATE.md tests/packaging/test_install_docs.py
git commit -m "docs: add installation and update guide"
```

### Task 5: Release Regression Gate

**Files:**
- Modify: no source files.

**Interfaces:**
- Verifies packaging work has not changed daemon/UI security behavior.

- [ ] **Step 1: Run all automated tests**

Run: `python -m pytest tests daemon -q --tb=short`

Expected: PASS with no camera opened.

- [ ] **Step 2: Validate source-tree hygiene**

Run: `git status --short`

Expected: only intended packaging build outputs, which must be ignored; no copied virtual environment, package build cache, or user enrollment files are tracked.

- [ ] **Step 3: Record manual release gates in the install guide**

The guide must require clean install, update, normal uninstall/reinstall, first-run enrollment, daily-use soft-lock loop, stranger test, printed photo test, and video replay test before a public release claim.
