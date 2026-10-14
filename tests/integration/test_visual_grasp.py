"""Voice text boundary -> real Planner/Zenoh -> virtual WRS grasp -> Mock TTS."""

import asyncio
import json

import pytest
from conftest import eventually
from llm_fixtures import chat_reply

from wrs_agent.nodes.wrs.node import WrsOptions
from wrs_agent.processes import NO_WINDOW, ROOT, LocalStack, python_command
from wrs_agent.schemas import Plan, Step

pytestmark = [pytest.mark.zenoh, pytest.mark.wrs]


async def test_virtual_grasp_collision_rejection_and_model_restoration():
    process = await asyncio.create_subprocess_exec(
        *python_command(ROOT / "tests/fixtures/wrs_grasp_probe.py"), cwd=ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=NO_WINDOW,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(), 25)
        assert process.returncode == 0, (output + error).decode("utf-8")
        report = json.loads(output.decode("utf-8").splitlines()[-1])
        assert report["collision_rejection"] == report["wrs_virtual_grasp"] == "PASS"
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


def grasp_plan(region=False):
    first = [Step(step_id="segment", skill="segment_object", args={"target": "box"})]
    if region:
        first = [
            Step(step_id="locate", skill="locate_object", args={"target": "box"}),
            Step(step_id="segment", skill="segment_region", depends_on=["locate"],
                 inputs={"location_id": {"step_id": "locate", "field": "location_id"}}),
        ]
    return Plan(steps=[*first,
        Step(step_id="propose", skill="infer_grasps", depends_on=["segment"],
             inputs={"segment_id": {"step_id": "segment", "field": "segment_id"}}),
        Step(step_id="grasp", skill="execute_visual_grasp", depends_on=["propose"],
             inputs={"candidate_set_id": {"step_id": "propose", "field": "candidate_set_id"}}),
        Step(step_id="say", skill="speak", depends_on=["grasp"],
             inputs={"text": {"step_id": "grasp", "field": "text"}}),
    ])


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("region", [False, True])
async def test_voice_planner_virtual_grasp_and_speech(llm_server, cancel, region):
    proposal = {"kind": "execute", "plan": grasp_plan(region).model_dump()}
    llm_server.response = chat_reply(json.dumps(proposal))
    stack = LocalStack(bindings=ROOT / "examples/voice/wrs_bindings.toml",
                       live_model=True, duration=.02, asr_script=["抓取方块并播报结果"])
    stack.options["wrs"] = WrsOptions(
        backend="wrs-grasp", scene=ROOT / "tests/fixtures/grasping/virtual_scene.toml",
        grasp_calibration=ROOT / "tests/fixtures/grasping/virtual_camera.json",
    )
    async with stack:
        perception = ("yolo", "segmentation") if region else ("sam3",)
        kinds = ("camera", *perception, "graspnet")
        segmentation = "segmentation" if region else "sam3"
        for kind in kinds:
            stack._spawn(kind, python_command(ROOT / "tests/fixtures/grasping/virtual_nodes.py",
                                              kind, stack.endpoint, stack.env_id, segmentation))
        registry = stack.system.registry
        for kind in kinds:
            await registry.wait_for(kind, timeout=10)
        await eventually(stack.system.clients["wrs"].features,
                         lambda f: f.backend == "wrs-grasp", timeout=10)
        # The client and Agent discover independently; wait for the planner's providers too.
        await eventually(
            lambda: registry.transport("agent").request("request/nodes", {}),
            lambda nodes: all(nodes.get(name, {}).get("ready")
                              for name in (*kinds, "wrs", "tts")),
            timeout=10,
        )
        press = await stack.system.listen_begin()
        await stack.system.listen_end(press.press_id)
        transcript = await eventually(lambda: stack.system.listen_result(press.press_id),
                                      lambda r: not r.capturing)
        receipt = await stack.system.send_text(transcript.text, input_id=press.press_id)
        assert receipt.accepted, receipt
        planned = await stack.system.planning(receipt.request_id).wait(timeout=15)
        assert planned.task is not None, planned
        if cancel:
            await eventually(lambda: stack.system.snapshot("wrs"),
                             lambda s: s.data.robot.held_object == "box", timeout=15)
            stopped = await stack.system.send_text("停止")
            assert stopped.accepted
        result = await planned.task.wait(timeout=20)
        assert result.state == ("CANCELLED" if cancel else "SUCCEEDED"), result
        scene = (await stack.system.snapshot("wrs")).data
        assert scene.robot.held_object == "box"
        if not cancel:
            assert scene.objects["box"].pos[2] >= .11
        assert scene.facts["collision_checked"] is True
        spoken = (await stack.system.snapshot("tts")).data.last_text
        assert spoken == ("" if cancel else "虚拟场景中已夹持并抬起测试方块。")
        assert len(llm_server.requests) == 1
        if cancel:
            frozen = await stack.system.snapshot("wrs")
            await asyncio.sleep(.15)
            assert (await stack.system.snapshot("wrs")).data == frozen.data
