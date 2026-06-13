from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INSTALLERS = [
    ROOT / "Install.ps1",
    ROOT / "build" / "staged" / "Install.ps1",
]


def test_installer_verifies_service_creation_instead_of_printing_unchecked_success():
    for script in INSTALLERS:
        text = script.read_text(encoding="utf-8")
        assert "function Invoke-ScExe" in text
        assert "New-Service -Name $SERVICE_NAME" in text
        assert "Get-Service -Name $SERVICE_NAME -ErrorAction Stop" in text
        assert "sc.exe @serviceCreateArgs | Out-Null" not in text
        assert "Service installed (auto-start, LocalSystem)" in text


def test_installer_copies_user_config_for_localsystem_service_profile():
    for script in INSTALLERS:
        text = script.read_text(encoding="utf-8")
        assert "systemprofile\\AppData\\Roaming\\MajestyGuard\\config.json" in text
        assert "Copy-Item -LiteralPath $userConfigPath" in text
        assert "EnrolledUserSid" in text
        assert "CvPythonPath" in text
        assert "ModelDirectory" in text
        assert "Resolve-CvPythonPath" in text
        assert "ConvertTo-Json -Depth 8" in text
        assert "HKLM:\\SOFTWARE\\MajestyGuard" in text


def test_installer_stops_runtime_before_copying_locked_files():
    for script in INSTALLERS:
        text = script.read_text(encoding="utf-8")
        assert "function Stop-InstalledMajestyGuardRuntime" in text
        assert "Get-CimInstance -ClassName Win32_Process" in text
        assert "MajestyGuard.Service.Host\\MajestyGuard.Service.dll" in text
        assert "Stop-Process -Id $proc.ProcessId -Force" in text
        assert text.find("Stop-InstalledMajestyGuardRuntime") < text.find('Copy-Item "$source\\*"')


def test_build_restages_core_project_after_dependent_publish_outputs():
    build_script = ROOT / "Build.ps1"
    text = build_script.read_text(encoding="utf-8")
    core_copy = 'Copy-RequiredItems -SourcePattern (Join-Path $OutDir "MajestyGuard.Core\\*") -Destination $StageDir'
    dpapi_copy = 'Copy-RequiredItems -SourcePattern (Join-Path $OutDir "MajestyGuard.DpapiHelper\\*") -Destination $StageDir'

    assert core_copy in text
    assert text.rfind(core_copy) > text.rfind(dpapi_copy)


def test_service_is_published_self_contained_for_localsystem_runtime():
    build_script = ROOT / "Build.ps1"
    build_text = build_script.read_text(encoding="utf-8")
    service_csproj = ROOT / "src" / "MajestyGuard.Service" / "MajestyGuard.Service.csproj"
    service_text = service_csproj.read_text(encoding="utf-8")

    assert '@("MajestyGuard.Service", "MajestyGuard.Overlay") -contains $projectName' in build_text
    assert "<SelfContained>true</SelfContained>" in service_text
    assert "<PublishSingleFile>true</PublishSingleFile>" in service_text


def test_build_stages_framework_dependent_dotnet_service_host_fallback():
    build_script = ROOT / "Build.ps1"
    text = build_script.read_text(encoding="utf-8")

    assert "MajestyGuard.Service.Host" in text
    assert "--self-contained false" in text
    assert 'Join-Path $StageDir "MajestyGuard.Service.Host"' in text
    assert "MajestyGuard.Service.Host\\MajestyGuard.Service.dll" in text


def test_phase3_runner_requires_explicit_test_signing_opt_in():
    runner = ROOT / "run_phase3_admin.ps1"
    text = runner.read_text(encoding="utf-8")

    assert "[switch]$EnableTestSigningIfNeeded" in text
    assert "TEST_SIGNING_NOT_ENABLED" in text
    assert "bcdedit /set testsigning on" in text
    assert text.find("if (-not $EnableTestSigningIfNeeded)") < text.find("bcdedit /set testsigning on")


def test_installer_dev_signs_all_majestyguard_user_mode_binaries():
    for script in INSTALLERS:
        text = script.read_text(encoding="utf-8")
        assert "[switch]$EnableDevSigning" in text
        assert "function Enable-DevCodeSigning" in text
        assert "function Add-CertificateToLocalMachineStore" in text
        assert '"MajestyGuard*.exe"' in text
        assert '"MajestyGuard*.dll"' in text
        assert "-Recurse" in text
        assert "Set-AuthenticodeSignature -FilePath $binary.FullName" in text
        assert "Copy-Item -LiteralPath \"Cert:\\LocalMachine\\My" not in text
        assert "bcdedit /set testsigning on" in text
        assert text.find("if ($EnableTestSigning)") < text.find("bcdedit /set testsigning on")


def test_phase3_runner_dev_signing_is_explicit_before_service_install():
    runner = ROOT / "run_phase3_admin.ps1"
    text = runner.read_text(encoding="utf-8")

    assert "[switch]$EnableDevSigningIfNeeded" in text
    assert "SERVICE_SIGNING_REQUIRED" in text
    assert '$signingNeeded = "DevSigning"' in text
    assert "$installArgs.EnableDevSigning = $true" in text
    assert text.find("Get-AuthenticodeSignature $ServiceExe") < text.find("& $InstallScript @installArgs")


def test_installer_can_use_microsoft_signed_dotnet_service_host():
    for script in INSTALLERS:
        text = script.read_text(encoding="utf-8")
        assert "[switch]$UseDotnetServiceHost" in text
        assert "$DOTNET_SERVICE_DLL" in text
        assert "Get-Command dotnet" in text
        assert 'New-Service -Name $SERVICE_NAME -BinaryPathName $serviceBinaryPath' in text
        assert '$serviceBinaryPath = "`"$dotnetExe`" `"$DOTNET_SERVICE_DLL`""' in text


def test_phase3_service_only_uses_dotnet_host_fallback():
    runner = ROOT / "run_phase3_admin.ps1"
    text = runner.read_text(encoding="utf-8")

    assert "[switch]$UseDotnetServiceHost" in text
    assert "$installArgs.UseDotnetServiceHost = $true" in text
    assert "-UseDotnetServiceHost" in text


def test_phase3_runner_uses_hashtable_splatting_for_installer_switches():
    runner = ROOT / "run_phase3_admin.ps1"
    text = runner.read_text(encoding="utf-8")

    assert "$installArgs = @{" in text
    assert "$cpInstallArgs = @{" in text
    assert "$finalInstallArgs = @{" in text
    assert '"-AcknowledgeLoginRisk"' not in text
    assert '$installArgs = @(' not in text


def test_phase3_runner_has_service_only_checkpoint_before_cp_registration():
    runner = ROOT / "run_phase3_admin.ps1"
    text = runner.read_text(encoding="utf-8")

    assert "[switch]$ServiceOnly" in text
    assert "SERVICE_ONLY_RESULT" in text
    assert "LOCK_SCREEN_TILE: SERVICE_ONLY_SKIPPED" in text
    assert "$registration = @(Get-CpRegistration)" in text
    assert text.find("if ($ServiceOnly)") < text.find('Write-Section "Section 4: CP behavior analysis"')
    assert text.find("if ($ServiceOnly)") < text.find('Write-Section "Section 6: CP registration"')
