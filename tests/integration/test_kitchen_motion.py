import asyncio
import json

import pytest

from wrs_agent.processes import NO_WINDOW, ROOT, python_command

pytestmark = pytest.mark.wrs


async def test_mesh_kitchen_plays_registered_skills():
    process = await asyncio.create_subprocess_exec(
        *python_command(ROOT / "tests/fixtures/kitchen_motion_probe.py"),
        cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=NO_WINDOW,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(), 300)
        assert process.returncode == 0, (output + error).decode("utf-8", errors="replace")
        report = json.loads(output.decode("utf-8").splitlines()[-1])
        assert report["kitchen_mesh_motion"] == "PASS"
        assert report["collision_rejection"] == "PASS"
        assert report["hardware"] is False
        assert len(report["poured_food"]) == 4
        assert report["grasp_gap"] < 0.02
        assert report["skimmer_in_pot"] is True
        assert report["served_in_bowl"] is True
        assert report["bowl_left_rack"] is True
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()
