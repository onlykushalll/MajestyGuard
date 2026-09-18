# MajestyGuard Control Center and Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a local PyQt Control Center that owns the daily-use daemon lifecycle without weakening biometric or named-pipe trust boundaries.

**Architecture:** A small pure-Python runtime layer owns configuration, process launch, health observation, and enrollment detection. A PyQt6 Control Center renders that state and exposes safe lifecycle actions. The daemon continues to own camera, recognition, liveness, policy, and the Dynamic Island process.

**Tech Stack:** Python 3.11, PyQt6, Windows Task Scheduler, pywin32, pytest.

**Spec:** `docs/superpowers/specs/2026-09-19-desktop-app-and-installer-design.md`

## Global Constraints

- Remain a user-space signed-in-session product; never claim Windows-login or Secure Desktop protection.
- Do not modify face recognition, liveness thresholds, or soft-lock implementation in this work.
- `MG_ENABLE_LOCK` remains `0` by default.
- No PID file, command-line substring, or user-writable metadata may authenticate a pipe client.
- The Control Center never reads raw frames, embeddings, recognition scores, or liveness scores.
- Existing enrollment remains intact across normal update and uninstall.

---

### Task 1: Runtime Settings Contract

**Files:**
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `tests/app/test_config.py`

**Interfaces:**
- Produces `RuntimeSettings(idle_timeout_seconds: int, paused: bool)`.
- Produces `load_settings(path: Path) -> RuntimeSettings` and `save_settings(path: Path, settings: RuntimeSettings) -> None`.
- Settings file is `%LOCALAPPDATA%\MajestyGuard\settings.json` by default.

- [ ] **Step 1: Write failing settings tests**

```python
from app.config import RuntimeSettings, load_settings, save_settings


def test_load_settings_uses_safe_defaults_when_file_is_missing(tmp_path):
    assert load_settings(tmp_path / "settings.json") == RuntimeSettings(
        idle_timeout_seconds=60,
        paused=False,
    )


def test_load_settings_rejects_out_of_range_idle_timeout(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"idle_timeout_seconds": 1, "paused": true}', encoding="utf-8")

    assert load_settings(path) == RuntimeSettings(idle_timeout_seconds=60, paused=True)


def test_save_settings_round_trips_valid_values(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(path, RuntimeSettings(idle_timeout_seconds=300, paused=False))

    assert load_settings(path) == RuntimeSettings(idle_timeout_seconds=300, paused=False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/app/test_config.py -q`

Expected: import failure because `app.config` does not exist.

- [ ] **Step 3: Implement the small, validated settings module**

```python
@dataclass(frozen=True)
class RuntimeSettings:
    idle_timeout_seconds: int = 60
    paused: bool = False


def load_settings(path: Path) -> RuntimeSettings:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return RuntimeSettings()
    timeout = data.get("idle_timeout_seconds", 60)
    paused = data.get("paused", False)
    return RuntimeSettings(
        idle_timeout_seconds=timeout if isinstance(timeout, int) and 15 <= timeout <= 1800 else 60,
        paused=paused if isinstance(paused, bool) else False,
    )


def save_settings(path: Path, settings: RuntimeSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(settings), sort_keys=True), encoding="utf-8")
    temporary.replace(path)
```

- [ ] **Step 4: Run the settings tests**

Run: `python -m pytest tests/app/test_config.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/__init__.py app/config.py tests/app/test_config.py
git commit -m "feat: add validated control center settings"
```

### Task 2: Daemon-Owned Display Lifecycle

**Files:**
- Modify: `daemon/main.py: MajestyGuardDaemon.start`
- Modify: `tests/daemon/test_daemon_phase4_resilience.py`

**Interfaces:**
- `MajestyGuardDaemon.start()` launches the owned UI after the UI state pipe is listening and before any state can require UI feedback.
- `MajestyGuardDaemon._owned_overlay_pid() -> Optional[int]` remains the sole PID resolver for UI and command pipes.

- [ ] **Step 1: Write a failing lifecycle test**

```python
def test_start_launches_owned_ui_after_the_state_pipe_is_started(monkeypatch):
    daemon = MajestyGuardDaemon()
    events = []
    monkeypatch.setattr(daemon.ipc, "start", lambda: events.append("pipe"))
    monkeypatch.setattr(daemon, "_launch_ui", lambda: events.append("ui"))
    monkeypatch.setattr(daemon, "_load_cv_engine", lambda: None)
    monkeypatch.setattr(daemon, "_warmup_inference", lambda: None)
    monkeypatch.setattr(daemon, "_run_loop", lambda: daemon._stop.set())

    daemon.start()

    assert events[:2] == ["pipe", "ui"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/daemon/test_daemon_phase4_resilience.py::test_start_launches_owned_ui_after_the_state_pipe_is_started -q`

Expected: FAIL because normal startup does not launch the owned UI.

- [ ] **Step 3: Implement the owned UI launch**

```python
self.ipc.start()
self._launch_ui()
self.command_ipc = CMDServer(
    self._queue_ui_command,
    expected_client_pid=self._owned_overlay_pid,
)
```

Keep `_launch_ui()` idempotent. Do not allow an externally launched UI or a Control Center PID to bypass the current named-pipe client identity check.

- [ ] **Step 4: Run daemon lifecycle and pipe tests**

Run: `python -m pytest tests/daemon/test_daemon_phase4_resilience.py tests/daemon/test_cmd_pipe.py tests/daemon/test_soft_lock_ipc.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add daemon/main.py tests/daemon/test_daemon_phase4_resilience.py
git commit -m "fix: launch the owned UI during normal daemon startup"
```

### Task 3: Pure Supervisor and Health Model

**Files:**
- Create: `app/runtime.py`
- Create: `tests/app/test_runtime.py`

**Interfaces:**
- Produces `RuntimeStatus` with `STOPPED`, `STARTING`, `RUNNING`, and `DEGRADED` values.
- Produces `Supervisor(root: Path, python_executable: Path, settings_path: Path)`.
- `Supervisor.start() -> bool`, `Supervisor.stop(timeout_seconds: float = 5.0) -> None`, `Supervisor.status() -> RuntimeStatus`, and `Supervisor.enrollment_present() -> bool`.

- [ ] **Step 1: Write failing supervisor tests**

```python
def test_supervisor_launches_daemon_with_safe_daily_use_environment(tmp_path, monkeypatch):
    launched = {}
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    monkeypatch.setattr(supervisor, "_spawn", lambda command, env: launched.update(command=command, env=env))

    assert supervisor.start() is True
    assert launched["env"]["MG_ENABLE_LOCK"] == "0"
    assert launched["env"]["MG_MAX_SECONDS"] == "0"
    assert launched["env"]["MG_OVERLAY_WATCHDOG"] == "1"


def test_supervisor_reports_degraded_when_child_exits_unexpectedly(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    monkeypatch.setattr(supervisor, "_process", FakeProcess(returncode=1))

    assert supervisor.status() is RuntimeStatus.DEGRADED


def test_supervisor_detects_existing_enrollment_from_current_user_data(tmp_path):
    enrollment = tmp_path / "local" / "MajestyGuard" / "embeddings_v2.npy"
    enrollment.parent.mkdir(parents=True)
    enrollment.write_bytes(b"gallery")

    assert Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json", local_app_data=tmp_path / "local").enrollment_present()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/app/test_runtime.py -q`

Expected: import failure because `app.runtime` does not exist.

- [ ] **Step 3: Implement the isolated supervisor**

```python
class RuntimeStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"


class Supervisor:
    def start(self) -> bool:
        if self.status() in (RuntimeStatus.STARTING, RuntimeStatus.RUNNING):
            return False
        settings = load_settings(self._settings_path)
        env = dict(os.environ, MG_ENABLE_LOCK="0", MG_MAX_SECONDS="0", MG_OVERLAY_WATCHDOG="1",
                   MG_IDLE_TIMEOUT=str(settings.idle_timeout_seconds), MG_PASSIVE_FPS="0")
        self._process = self._spawn([str(self._python), str(self._root / "daemon" / "main.py")], env)
        return True
```

The process command must use `daemon/main.py`, set `MG_ENABLE_LOCK=0`, set `MG_MAX_SECONDS=0`, set `MG_OVERLAY_WATCHDOG=1`, set the validated idle timeout, and set `MG_PASSIVE_FPS=0`. It must use the installed root as `cwd` and never write a trusted PID file.

- [ ] **Step 4: Run supervisor tests**

Run: `python -m pytest tests/app/test_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/runtime.py tests/app/test_runtime.py
git commit -m "feat: add local daemon supervisor"
```

### Task 4: Control Center User Interface

**Files:**
- Create: `app/control_center.py`
- Create: `app/main.py`
- Create: `tests/app/test_control_center.py`

**Interfaces:**
- `ControlCenter(supervisor: Supervisor, settings_path: Path)` owns no biometric data.
- `ControlCenter.refresh() -> None` updates status and menu actions.
- `app.main.main() -> int` starts one notification-area process.

- [ ] **Step 1: Write failing interaction tests**

```python
def test_control_center_start_action_starts_stopped_supervisor(qapp, tmp_path):
    supervisor = FakeSupervisor(status=RuntimeStatus.STOPPED)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view.start_protection()

    assert supervisor.start_calls == 1


def test_control_center_never_labels_a_dead_daemon_as_protected(qapp, tmp_path):
    supervisor = FakeSupervisor(status=RuntimeStatus.DEGRADED)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    view.refresh()

    assert "Degraded" in view.status_text()
    assert "Protected" not in view.status_text()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/app/test_control_center.py -q`

Expected: import failure because `app.control_center` does not exist.

- [ ] **Step 3: Implement the restrained PyQt6 surface**

```python
class ControlCenter(QMainWindow):
    def start_protection(self) -> None:
        self._supervisor.start()
        self.refresh()

    def refresh(self) -> None:
        status = self._supervisor.status()
        self._status_label.setText({
            RuntimeStatus.STOPPED: "Protection stopped",
            RuntimeStatus.STARTING: "Starting local protection",
            RuntimeStatus.RUNNING: "Protection running",
            RuntimeStatus.DEGRADED: "Protection degraded - restart required",
        }[status])
        self._start_action.setEnabled(status in (RuntimeStatus.STOPPED, RuntimeStatus.DEGRADED))
```

Use `QSystemTrayIcon` with Start or Restart, Pause for this session, Re-enroll, Open Diagnostics, and Quit. Use native Windows typography and a quiet light/dark-neutral design. Do not add decorative gradients, biometric meters, or threshold controls.

- [ ] **Step 4: Run Control Center tests**

Run: `python -m pytest tests/app/test_control_center.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/control_center.py app/main.py tests/app/test_control_center.py
git commit -m "feat: add MajestyGuard control center"
```

### Task 5: First-Run Enrollment and Diagnostics Actions

**Files:**
- Modify: `app/runtime.py`
- Modify: `app/control_center.py`
- Modify: `tests/app/test_runtime.py`
- Modify: `tests/app/test_control_center.py`

**Interfaces:**
- `Supervisor.start_enrollment() -> bool` starts `daemon/enroll_v2.py` only when no enrollment subprocess is active.
- `Supervisor.open_log_directory() -> Path` returns `%LOCALAPPDATA%\MajestyGuard`.

- [ ] **Step 1: Write failing action tests**

```python
def test_start_enrollment_refuses_a_second_concurrent_enrollment(tmp_path, monkeypatch):
    supervisor = Supervisor(tmp_path, Path("python.exe"), tmp_path / "settings.json")
    monkeypatch.setattr(supervisor, "_enrollment_process", FakeProcess(returncode=None))

    assert supervisor.start_enrollment() is False


def test_control_center_offers_enrollment_when_profile_is_missing(qapp, tmp_path):
    supervisor = FakeSupervisor(status=RuntimeStatus.STOPPED, enrollment_present=False)
    view = ControlCenter(supervisor, tmp_path / "settings.json")

    assert view.primary_action_text() == "Enroll face"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/app/test_runtime.py tests/app/test_control_center.py -q`

Expected: FAIL because enrollment behavior has not been implemented.

- [ ] **Step 3: Implement the guarded first-run actions**

```python
def start_enrollment(self) -> bool:
    if self._enrollment_process and self._enrollment_process.poll() is None:
        return False
    self._enrollment_process = self._spawn_enrollment()
    return True
```

Open the existing enrollment tool; do not duplicate camera capture or store biometric data in the Control Center. The diagnostics action may open the local log directory but must not display raw biometric values.

- [ ] **Step 4: Run the action tests**

Run: `python -m pytest tests/app/test_runtime.py tests/app/test_control_center.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/runtime.py app/control_center.py tests/app/test_runtime.py tests/app/test_control_center.py
git commit -m "feat: add guarded enrollment and diagnostics actions"
```

### Task 6: Active Runtime Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/design/ARCHITECTURE_AND_DESIGN.md`
- Create: `docs/guides/DAILY_USE.md`
- Test: `tests/app/test_release_docs.py`

**Interfaces:**
- README documents only the active Python/PyQt user-space path as current.
- Legacy service, Credential Provider, and WHCDF work is clearly marked historical or unavailable.

- [ ] **Step 1: Write failing documentation assertions**

```python
def test_readme_describes_current_user_space_runtime():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "user-space" in text.lower()
    assert "Control Center" in text
    assert "not a Windows login replacement" in text


def test_daily_use_guide_keeps_real_lock_opt_in():
    text = (ROOT / "docs" / "guides" / "DAILY_USE.md").read_text(encoding="utf-8")

    assert "MG_ENABLE_LOCK=0" in text
    assert "stranger" in text.lower()
    assert "spoof" in text.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/app/test_release_docs.py -q`

Expected: FAIL because the active runtime docs are not yet present.

- [ ] **Step 3: Rewrite active documentation**

Document local processing, the Control Center, first run, ordinary use, pause and diagnostics behavior, safe lock defaults, physical release gates, legacy experiment status, and unsigned-build disclosure. Keep historical C# material in its own clearly labeled section; do not delete source history.

- [ ] **Step 4: Run documentation tests**

Run: `python -m pytest tests/app/test_release_docs.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/design/ARCHITECTURE_AND_DESIGN.md docs/guides/DAILY_USE.md tests/app/test_release_docs.py
git commit -m "docs: document active MajestyGuard runtime"
```

### Task 7: Runtime Regression Gate

**Files:**
- Modify: no source files.

**Interfaces:**
- Verifies the Control Center additions preserve existing daemon/UI behavior.

- [ ] **Step 1: Run static and targeted tests**

Run: `python -m py_compile app/config.py app/runtime.py app/control_center.py app/main.py daemon/main.py`

Expected: PASS.

- [ ] **Step 2: Run full non-camera test suite**

Run: `python -m pytest tests daemon -q --tb=short`

Expected: PASS with no camera opened.

- [ ] **Step 3: Commit final runtime verification note only if a source change was required**

```bash
git status --short
```

Expected: no uncommitted runtime changes apart from deliberately excluded local artifacts.
