"""Run real checks and preserve their outputs; never manufactures PASS entries."""

import argparse
import json
import subprocess
import sys

from scripts.example_catalog import WRS, check_catalog
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
                "wrs_runtime",
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
        ]
    check_catalog(ROOT / "examples")
    examples = WRS if args.wrs else {}
    expectations = {}
    for path, expected in examples.items():
        name = "example_" + path.removesuffix(".py").replace("/", "_")
        checks.append((name, ["examples/" + path]))
        expectations[name] = expected
    results = []
    for name, arguments in checks:
        command = python_command(*arguments)
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=360,
            creationflags=NO_WINDOW,
        )
        output = result.stdout + result.stderr
        evidence = REPORTS / f"{name}.txt"
        evidence.write_text(output, encoding="utf-8")
        missing = [line for line in expectations.get(name, []) if line not in output]
        status = "PASS" if result.returncode == 0 and not missing else "FAIL"

        results.append(
            {
                "test_id": name,
                "profile": name,
                "status": status,
                "command": command,
                "exit_code": result.returncode,
                "missing_output": missing,
                "summary": output.strip().splitlines()[-1:] or [],
                "evidence": str(evidence.relative_to(ROOT)),
            }
        )
        print(f"{name}: {status}", flush=True)
    for test_id, reason in {
        **({} if args.wrs else {"wrs_runtime": "Opt in with scripts/verify.py --wrs."}),
        "wrs_pick_place": "Unsupported in bare UR7E profile; no validated grasp/contact scene.",
        "llm_live": "Online examples excluded from automated checks; no live provider call.",
        "audio_live": "Qwen model/audio runs require opt-in; excluded from default checks.",
        "yolo_live": "Local YOLO weights are opt-in; tests use a named detector fixture.",
        "sam3_graspnet_live": "Trained weights/inference unverified; tests use explicit fixtures.",
        "fastsam_text": "Local probe ran; semantic mask review failed. Real grasp is unverified.",
        "visual_grasp_hardware": "Only configured-box WRS virtual grasps verified; no real grasp.",
        "camera_hardware": "RealSense D405 acquisition/disconnection requires hardware opt-in.",
        "hardware": "Physical actuation is excluded from automated verification.",
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
