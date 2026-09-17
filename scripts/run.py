"""Use the nominated interpreter with project-managed third-party packages."""

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / ".local" / "deps"), str(ROOT)]
if len(sys.argv) < 2:
    raise SystemExit("usage: run.py [-m module | script.py] [arguments]")
sys.argv.pop(0)
if sys.argv[0] == "-m":
    sys.argv.pop(0)
    runpy.run_module(sys.argv[0], run_name="__main__", alter_sys=True)
else:
    runpy.run_path(sys.argv[0], run_name="__main__")
