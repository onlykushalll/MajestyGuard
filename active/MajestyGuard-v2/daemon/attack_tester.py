"""
Controlled attack-test harness for MajestyGuard.

This tool does not weaken daemon safety settings. It watches daemon.log while
the user performs one physical test scenario, then writes a structured report.

Procedures:
- printed_photo: Print an A4 photo of the owner's face. Hold it in front of
  the camera for 30 seconds. Expected: liveness fails; daemon never reaches
  ACTIVE.
- phone_screen_replay: Play a clear owner-face video on a phone screen in front
  of the camera for 30 seconds. Expected: liveness/replay signals block ACTIVE.
- camera_obstruction: While ACTIVE, cover the camera for 10 seconds. Expected:
  LOCKED within the absence window.
- camera_unplug: While ACTIVE, unplug the USB camera for 15 seconds, then plug
  it back in. Expected: CAMERA_UNAVAILABLE promptly, then recovery.
- virtual_camera_injection: If OBS is installed, start OBS Virtual Camera with a
  static owner image/video. Expected: virtual camera detector blocks it or the
  daemon keeps using the real camera. If OBS is absent, mark SKIPPED.
- second_person_at_laptop: Owner leaves; second person sits at laptop. Expected:
  SOCIAL_LOCK/LOCKED within 5 seconds.
- rapid_face_swap: Owner ACTIVE, then owner leaves and another face enters
  within 1 second. Expected: no one-frame false trigger, but confirmed stranger
  locks quickly.
- low_light_bypass: Turn room lights off, screen light only. Expected: owner can
  still reach ACTIVE without false-locking; failures are usability bugs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Literal


ResultStatus = Literal["BLOCKED", "BYPASSED", "PARTIAL", "PASS", "SKIPPED", "UNTESTED"]
DaemonResponse = Literal["LOCKED", "ACTIVE", "SCANNING", "IDLE", "CAMERA_UNAVAILABLE", "UNKNOWN"]

VALID_RESULTS = {"BLOCKED", "BYPASSED", "PARTIAL", "PASS", "SKIPPED", "UNTESTED"}
VALID_RESPONSES = {"LOCKED", "ACTIVE", "SCANNING", "IDLE", "CAMERA_UNAVAILABLE", "UNKNOWN"}

FRAME_RE = re.compile(
    r"\b(?:Scanning|Active) frame=\d+ .*?\bscore=(?P<score>-?\d+(?:\.\d+)?) "
    r"\bliveness=(?P<liveness>-?\d+(?:\.\d+)?) "
    r"\blive=(?P<live>True|False)\b"
)


@dataclass(frozen=True)
class AttackScenario:
    attack: str
    description: str
    duration_s: float = 30.0


@dataclass(frozen=True)
class AttackResult:
    attack: str
    description: str
    daemon_response: DaemonResponse
    seconds_to_response: float | None
    recognition_score_seen: float | None
    liveness_score_seen: float | None
    liveness_passed: bool | None
    result: ResultStatus
    notes: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


ATTACK_SCENARIOS = [
    AttackScenario("printed_photo", "A4 printed photo of owner held in front of camera"),
    AttackScenario("phone_screen_replay", "Owner-face video replayed on a phone screen"),
    AttackScenario("camera_obstruction", "Camera covered while daemon is ACTIVE", duration_s=15.0),
    AttackScenario("camera_unplug", "USB camera unplugged while daemon is ACTIVE", duration_s=20.0),
    AttackScenario("virtual_camera_injection", "OBS or similar virtual camera feed attempt"),
    AttackScenario("second_person_at_laptop", "Second person actively uses laptop after owner leaves"),
    AttackScenario("rapid_face_swap", "Owner leaves and another face enters within one second"),
    AttackScenario("low_light_bypass", "Owner uses laptop under screen-light-only low light"),
]


def validate_attack_result_schema(data: dict[str, object]) -> None:
    required = {
        "attack",
        "description",
        "daemon_response",
        "seconds_to_response",
        "recognition_score_seen",
        "liveness_score_seen",
        "liveness_passed",
        "result",
        "notes",
    }
    missing = required - set(data)
    if missing:
        raise ValueError(f"Missing attack result fields: {sorted(missing)}")
    if data["result"] not in VALID_RESULTS:
        raise ValueError(f"Invalid result: {data['result']}")
    if data["daemon_response"] not in VALID_RESPONSES:
        raise ValueError(f"Invalid daemon_response: {data['daemon_response']}")


def parse_frame_metrics(line: str) -> dict[str, object] | None:
    match = FRAME_RE.search(line)
    if not match:
        return None
    return {
        "recognition_score": float(match.group("score")),
        "liveness_score": float(match.group("liveness")),
        "liveness_passed": match.group("live") == "True",
    }


def _state_from_line(line: str) -> DaemonResponse | None:
    if "-> ACTIVE" in line:
        return "ACTIVE"
    if "-> LOCKED" in line or "-> SOCIAL_LOCK" in line or "LOCKING WORKSTATION" in line:
        return "LOCKED"
    if "-> CAMERA_UNAVAILABLE" in line:
        return "CAMERA_UNAVAILABLE"
    if "-> SCANNING" in line:
        return "SCANNING"
    if "-> IDLE" in line:
        return "IDLE"
    return None


def classify_attack_result(
    attack: str,
    responses: Iterable[str],
    liveness_passed: bool | None,
) -> ResultStatus:
    states = set(responses)
    if attack in {"printed_photo", "phone_screen_replay"}:
        if "ACTIVE" in states:
            return "BYPASSED"
        if liveness_passed is False or states & {"SCANNING", "IDLE", "LOCKED"}:
            return "BLOCKED"
        return "PARTIAL"
    if attack in {"camera_obstruction", "second_person_at_laptop", "rapid_face_swap"}:
        if "LOCKED" in states:
            return "BLOCKED"
        return "BYPASSED"
    if attack == "camera_unplug":
        if "CAMERA_UNAVAILABLE" in states:
            return "BLOCKED"
        return "BYPASSED"
    if attack == "virtual_camera_injection":
        if "ACTIVE" in states:
            return "BYPASSED"
        return "BLOCKED" if states else "SKIPPED"
    if attack == "low_light_bypass":
        return "PASS" if "ACTIVE" in states else "PARTIAL"
    return "UNTESTED"


class AttackTester:
    def __init__(self, log_path: Path):
        self.log_path = log_path

    def run(self, scenario: AttackScenario, *, notes: str = "") -> AttackResult:
        start = time.monotonic()
        lines = self._read_existing_lines()
        initial_count = len(lines)
        print(f"Running {scenario.attack} for {scenario.duration_s:.0f}s")
        print(f"Procedure: {scenario.description}")
        deadline = start + scenario.duration_s
        while time.monotonic() < deadline:
            time.sleep(0.5)
        new_lines = self._read_existing_lines()[initial_count:]
        return summarize_lines(scenario, new_lines, start_monotonic=start, notes=notes)

    def _read_existing_lines(self) -> list[str]:
        if not self.log_path.exists():
            return []
        return self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()


def summarize_lines(
    scenario: AttackScenario,
    lines: Iterable[str],
    *,
    start_monotonic: float,
    notes: str = "",
) -> AttackResult:
    responses: list[DaemonResponse] = []
    first_response_at: float | None = None
    max_recognition: float | None = None
    max_liveness: float | None = None
    any_liveness_passed: bool | None = None
    for line in lines:
        response = _state_from_line(line)
        if response:
            responses.append(response)
            if first_response_at is None:
                first_response_at = max(0.0, time.monotonic() - start_monotonic)
        metrics = parse_frame_metrics(line)
        if metrics:
            score = float(metrics["recognition_score"])
            liveness = float(metrics["liveness_score"])
            max_recognition = score if max_recognition is None else max(max_recognition, score)
            max_liveness = liveness if max_liveness is None else max(max_liveness, liveness)
            any_liveness_passed = bool(metrics["liveness_passed"]) or bool(any_liveness_passed)

    daemon_response = responses[-1] if responses else "UNKNOWN"
    result = classify_attack_result(scenario.attack, responses, any_liveness_passed)
    attack_result = AttackResult(
        attack=scenario.attack,
        description=scenario.description,
        daemon_response=daemon_response,
        seconds_to_response=first_response_at,
        recognition_score_seen=max_recognition,
        liveness_score_seen=max_liveness,
        liveness_passed=any_liveness_passed,
        result=result,
        notes=notes,
    )
    validate_attack_result_schema(attack_result.to_dict())
    return attack_result


def write_report(results: list[AttackResult], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y%m%d")
    path = report_dir / f"attack_report_{date}.json"
    data = [result.to_dict() for result in results]
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _scenario_by_name(name: str) -> AttackScenario:
    for scenario in ATTACK_SCENARIOS:
        if scenario.attack == name:
            return scenario
    names = ", ".join(s.attack for s in ATTACK_SCENARIOS)
    raise SystemExit(f"Unknown attack {name!r}. Choose one of: {names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a controlled MajestyGuard attack scenario.")
    parser.add_argument("--attack", choices=[s.attack for s in ATTACK_SCENARIOS])
    parser.add_argument("--duration", type=float)
    parser.add_argument("--log-path", default=str(Path(os.environ.get("LOCALAPPDATA", "C:/tmp")) / "MajestyGuard" / "daemon.log"))
    parser.add_argument("--report-dir", default="tests")
    parser.add_argument("--notes", default="")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for scenario in ATTACK_SCENARIOS:
            print(f"{scenario.attack}: {scenario.description}")
        return 0
    if not args.attack:
        parser.error("--attack is required unless --list is used")

    scenario = _scenario_by_name(args.attack)
    if args.duration:
        scenario = AttackScenario(scenario.attack, scenario.description, args.duration)
    result = AttackTester(Path(args.log_path)).run(scenario, notes=args.notes)
    report_path = write_report([result], Path(args.report_dir))
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
