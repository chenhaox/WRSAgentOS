"""Read-only audit; WRS probing is explicit and never connects hardware."""

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def command(args, **kwargs):
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        creationflags=NO_WINDOW,
        **kwargs,
    )
    return {
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip()[-1200:],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-wrs", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ["eclipse-zenoh", "pydantic", "pytest", "pytest-asyncio", "ruff"]
        },
        "wrs_root": str(ROOT / "third_party/wrs"),
        "wrs_commit": command(["git", "-C", "third_party/wrs", "rev-parse", "HEAD"]),
        "wrs_gitlink": command(["git", "ls-files", "--stage", "third_party/wrs"]),
        "wrs_worktree": command(["git", "-C", "third_party/wrs", "status", "--short"]),
        "router": command([str(ROOT / ".local/zenoh-1.9.0/zenohd.exe"), "--version"]),
        "wrs_probe": {
            "status": "UNVERIFIED",
            "reason": "Use --probe-wrs for virtual import/FK only",
        },
        "hardware": "UNVERIFIED",
        "glm_live": "UNVERIFIED",
        "audio_live": "UNVERIFIED",
    }
    if args.probe_wrs:
        # WRS uses the nominated user's existing scientific packages. No package install.
        code = (
            "import sys,json; "
            "sys.path[:0]=[sys.argv[1],sys.argv[2]]; "
            "from wrs_agent.env.wrs import probe_virtual; "
            "print(json.dumps(probe_virtual()))"
        )
        report["wrs_probe"] = command(
            [sys.executable, "-X", "utf8", "-c", code, str(ROOT / "third_party/wrs"), str(ROOT)]
        )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
