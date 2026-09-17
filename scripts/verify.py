"""Run real checks and preserve their outputs; never manufactures PASS entries."""

import argparse
import json
import subprocess
import sys

from wrs_agent.processes import NO_WINDOW, ROOT, python_command

REPORTS = ROOT / "reports"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wrs", action="store_true", help="Include real WRS virtual node checks")
    args = parser.parse_args()
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
                "zenoh and not wrs",
                "--junitxml=reports/zenoh.xml",
            ],
        ),
        ("roundtrip", ["examples/01_zenoh_roundtrip.py"]),
        ("parallel_interrupt", ["examples/02_mock_interrupt.py"]),
        ("glm_fixture", ["examples/04_glm_task.py", "--dry-run"]),
        ("skill_library", ["examples/09_skill_library.py"]),
        ("plan_cache", ["examples/05_cache_reuse.py"]),
        ("system_nodes", ["examples/10_system_nodes.py"]),
        ("lint", ["-m", "ruff", "check", "wrs_agent", "tests", "examples", "scripts"]),
        (
            "doctor",
            ["scripts/doctor.py", "--output", "reports/doctor.json"]
            + (["--probe-wrs"] if args.wrs else []),
        ),
    ]
    if args.wrs:
        checks += [
            (
                "wrs_virtual_runtime",
                [
                    "-m",
                    "pytest",
                    "-q",
                    "tests/integration",
                    "-m",
                    "wrs",
                    "--junitxml=reports/wrs.xml",
                ],
            ),
            ("wrs_complete", ["examples/03_wrs_scene.py"]),
            ("wrs_cancel", ["examples/03_wrs_scene.py", "--cancel"]),
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
                if name
                in {"zenoh", "roundtrip", "parallel_interrupt", "skill_library", "plan_cache"}
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
        **({} if args.wrs else {"wrs_virtual_runtime": "Opt in with scripts/verify.py --wrs."}),
        "wrs_pick_place": "Unsupported in bare Lite6 profile; no validated grasp/contact scene.",
        "glm_live": "GLM HTTP fixtures only; no account model/use authorization, no live call.",
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
