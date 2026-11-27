from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_bundle_script_builds_a_fresh_distribution_not_a_copied_venv():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert "PyInstaller" in text
    assert ".venv" not in text
    assert "app\\main.py" in text


def test_build_requirements_pin_pyinstaller():
    text = (ROOT / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")

    assert any(line.startswith("pyinstaller==") for line in text.splitlines())


def test_bundle_script_requires_models_and_builds_all_three_processes():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert "ModelDir" in text
    assert "InsightFaceModelDir" in text
    assert "MajestyGuardDaemon" in text
    assert "MajestyGuardUI" in text
    assert "${Description}:" in text
    assert "[string]$OutputDir," in text


def test_all_frozen_processes_use_onedir_bundles():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert text.count('"--onedir"') == 3
    assert '"--onefile"' not in text
    assert '"MajestyGuard"' in text
    assert '"MajestyGuardUI"' in text


def test_bundle_script_has_an_explicit_no_camera_smoke_test():
    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")

    assert "verify_bundle_smoke.py" in text
    assert "MG_ENABLE_LOCK" in text
    assert '"0"' in text
    assert 'MajestyGuard\\MajestyGuard.exe' in text
    assert "SmokeTimeoutSeconds" in text
    assert "--failure-report" in text


def test_packager_checks_each_entrypoint_without_starting_protection():
    import importlib.util
    import sys

    smoke_tool = ROOT / "packaging" / "verify_bundle_smoke.py"
    spec = importlib.util.spec_from_file_location("mg_verify_bundle_smoke", smoke_tool)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    checks = module.default_checks(ROOT / "dist" / "MajestyGuard")
    assert [(c.name, c.exe.name, c.args) for c in checks] == [
        ("daemon-imports", "MajestyGuardDaemon.exe", ("--self-test",)),
        ("enrollment", "MajestyGuardDaemon.exe", ("--enroll", "--self-test")),
        ("control-center", "MajestyGuard.exe", ("--self-test",)),
        ("overlay-ui", "MajestyGuardUI.exe", ("--self-test",)),
    ]

    text = (ROOT / "packaging" / "build_bundle.ps1").read_text(encoding="utf-8")
    assert '"--collect-all", "onnxruntime"' not in text
    assert '"--exclude-module", "onnx.reference"' in text
    assert '"--hidden-import", "scipy._cyutility"' in text
    for path in ["daemon/main.py", "ui/main.py"]:
        entry = (ROOT / path).read_text(encoding="utf-8").split('if __name__ == "__main__":')[-1]
        assert entry.index('"--self-test"') < entry.rindex("main()")


def test_packaging_powershell_scripts_parse_cleanly_and_crt_script_guards_copies(tmp_path):
    import subprocess
    import sys

    if sys.platform != "win32":
        return

    scripts = [
        ROOT / "packaging" / "build_bundle.ps1",
        ROOT / "packaging" / "install_bundle_crt.ps1",
    ]
    quoted = ", ".join("'" + str(s).replace("'", "''") + "'" for s in scripts)
    parse_cmd = (
        "$errors = $null; "
        f"foreach ($p in @({quoted})) {{ "
        "$null = [System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$null, [ref]$errors); "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 } "
        "}"
    )

    def _run_powershell(*ps_args: str):
        return subprocess.run(
            ["powershell", "-NoProfile", *ps_args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
        )

    parsed = _run_powershell("-Command", parse_cmd)
    assert parsed.returncode == 0, parsed.stderr

    # Unsigned CRT DLLs must be rejected by install_bundle_crt.ps1 before copying.
    daemon_dir = tmp_path / "MajestyGuardDaemon"
    daemon_dir.mkdir()
    (daemon_dir / "MajestyGuardDaemon.exe").write_bytes(b"exe")
    bad_crt = tmp_path / "bad_crt"
    bad_crt.mkdir()
    for dll in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        (bad_crt / dll).write_bytes(b"unsigned")

    rejected = _run_powershell(
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "packaging" / "install_bundle_crt.ps1"),
        "-DaemonDir",
        str(daemon_dir),
        "-VCRedistDir",
        str(bad_crt),
    )
    assert rejected.returncode != 0
    assert "signed Microsoft redistributable" in (rejected.stdout + rejected.stderr)
    crt_script = (ROOT / "packaging" / "install_bundle_crt.ps1").read_text(encoding="utf-8")
    assert "_internal\\PyQt6\\Qt6\\bin" in crt_script

    missing_out = _run_powershell(
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "packaging" / "build_bundle.ps1"),
        "-PythonPath",
        sys.executable,
        "-ModelDir",
        str(tmp_path),
        "-InsightFaceModelDir",
        str(tmp_path),
        "-FinalizeExistingBundle",
    )
    assert missing_out.returncode != 0
    assert "Pass the explicit -OutputDir" in (missing_out.stdout + missing_out.stderr)
