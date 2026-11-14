import asyncio
import json

import pytest

from wrs_agent.processes import NO_WINDOW, ROOT, python_command

pytestmark = pytest.mark.wrs


async def test_computed_pour_stir_scoop_shake_off_in_the_kitchen_scene():
    process = await asyncio.create_subprocess_exec(
        *python_command(ROOT / "tests/fixtures/wrs_motion_probe.py"),
        cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=NO_WINDOW,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(), 120)
        assert process.returncode == 0, (output + error).decode("utf-8")
        report = json.loads(output.decode("utf-8").splitlines()[-1])
        assert report["wrs_computed_motion"] == "PASS"
        assert report["collision_rejection"] == "PASS"
        assert report["hardware"] is False
        for skill in ("pour", "stir", "scoop", "shake_off"):
            assert report[skill]["steps"] > 0
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
