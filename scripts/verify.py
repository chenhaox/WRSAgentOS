"""Run real checks and preserve their outputs; never manufactures PASS entries."""

import json
import subprocess
import sys

from wrs_agent.processes import NO_WINDOW, ROOT, python_command

REPORTS = ROOT / "reports"


def main():
    REPORTS.mkdir(exist_ok=True)
    checks = [
        ("unit", ["-m", "pytest", "-q", "tests/unit", "--junitxml=reports/unit.xml"]),
        (
            "zenoh",
            [
                "-m",
                "pytest",
                "-q",
                "tests/integration",
                "-m",
                "zenoh",
                "--junitxml=reports/zenoh.xml",
            ],
        ),
        ("roundtrip", ["examples/01_zenoh_roundtrip.py"]),
        ("parallel_interrupt", ["examples/02_mock_interrupt.py"]),
        ("lint", ["-m", "ruff", "check", "wrs_agent", "tests", "examples", "scripts"]),
        ("doctor", ["scripts/doctor.py", "--probe-wrs", "--output", "reports/doctor.json"]),
    ]
    results = []
    for name, arguments in checks:
        command = python_command(*arguments)
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            creationflags=NO_WINDOW,
        )
        output = result.stdout + result.stderr
        evidence = REPORTS / f"{name}.txt"
        evidence.write_text(output, encoding="utf-8")
        status = "PASS" if result.returncode == 0 else "FAIL"
        results.append(
            {
                "test_id": name,
                "profile": "zenoh_mock"
                if name in {"zenoh", "roundtrip", "parallel_interrupt"}
                else name,
                "status": status,
                "command": command,
                "exit_code": result.returncode,
                "summary": output.strip().splitlines()[-1:] or [],
                "evidence": str(evidence.relative_to(ROOT)),
            }
        )
        print(f"{name}: {status}", flush=True)
    for test_id, reason in {
        "wrs_virtual_runtime": "Import/FK only; continuous virtual Action adapter is M3.",
        "glm_live": "No account/model configured; deferred MockClient tested, no paid call.",
        "audio_live": "Voice replay and Mock TTS only; microphone/ASR/audible output untested.",
        "vision_node": "No independent Vision process in this minimum increment.",
        "hardware": "Hardware backend cannot be selected.",
        "two_machine": "Loopback only; remote authentication/ACL profile is M7.",
        "performance": "No M8 percentile or hardware braking benchmark has been run.",
    }.items():
        results.append(
            {"test_id": test_id, "profile": test_id, "status": "UNVERIFIED", "summary": reason}
        )
    (REPORTS / "acceptance.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    (REPORTS / "benchmark.json").write_text(
        json.dumps({"status": "UNVERIFIED", "reason": "M8 benchmark not run; no timing claims."})
        + "\n",
        encoding="utf-8",
    )
    return int(any(result["status"] == "FAIL" for result in results))


if __name__ == "__main__":
    sys.exit(main())
