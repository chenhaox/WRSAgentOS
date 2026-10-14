"""Real nodes and Runtime, with explicitly offline detector/model/speaker fixtures."""

import asyncio
import json

import pytest
from conftest import eventually

from wrs_agent.planner import ModelPlanner
from wrs_agent.processes import ROOT, LocalStack, python_command
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import GoalRequest, Plan, Step, TaskCancelRequest, TaskRequest, new_id

pytestmark = pytest.mark.zenoh


def announcement():
    return Plan(steps=[
        Step(step_id="see", skill="describe_objects"),
        Step(step_id="say", skill="speak", depends_on=["see"],
             inputs={"text": {"step_id": "see", "field": "text"}}),
    ])


async def test_planner_camera_detector_result_binding_tts_and_disconnection(make_llm):
    async with LocalStack(bindings="configs/tts.toml", duration=0.03) as stack:
        camera = stack._spawn("camera", python_command(
            ROOT / "tests/fixtures/camera_node.py", stack.endpoint, stack.env_id,
        ))
        stack._spawn("yolo", python_command(
            ROOT / "tests/fixtures/vision_node.py", stack.endpoint, stack.env_id,
        ))
        registry = stack.system.registry
        await registry.wait_for("yolo", timeout=8)
        nodes = stack.system.clients
        await eventually(nodes["yolo"].snapshot, lambda s: s.data.health == "ready", timeout=8)
        provider = make_llm(json.dumps({"kind": "execute", "plan": announcement().model_dump()}))
        runtime = Runtime(nodes, {"describe_objects": "yolo", "speak": "tts"},
                          ModelPlanner(provider), registry=registry)
        try:
            await runtime.goal(GoalRequest(request_id=new_id(), goal="播报相机看到的物体"))
            result = await eventually(runtime.snapshot, lambda s: s["state"] in
                                      {"SUCCEEDED", "FAILED"}, timeout=8)
            assert result["state"] == "SUCCEEDED", result
            spoken = (await nodes["tts"].snapshot()).data.last_text
            assert spoken == "合成测试画面中识别到：杯子1个。"
            vision_action = next(a for a in runtime.action_history
                                 if a["skill"] == "describe_objects")
            status = await nodes["yolo"].status(vision_action["action_id"])
            detected = await registry.transport("yolo").request(
                "request/vision/result", {"result_id": status.output["result_id"]},
            )
            assert detected["observation_id"] == status.output["observation_id"]
            assert status.output["text"] == spoken
            before = (await nodes["tts"].snapshot()).data.completed
            await registry.transport("camera").request(
                "request/node/camera/shutdown", {}, control=True,
            )
            assert await asyncio.to_thread(camera.wait, 5) == 0
            await eventually(nodes["yolo"].snapshot, lambda s: s.data.health != "ready", timeout=5)
            receipt = await runtime.start(TaskRequest(request_id=new_id(), plan=announcement()))
            await eventually(runtime.snapshot, lambda s: bool(s["active_actions"]))
            await runtime.cancel(TaskCancelRequest(request_id=new_id(), task_id=receipt["task_id"]))
            await eventually(lambda: runtime.task_status(receipt["task_id"]),
                             lambda s: s["state"] == "CANCELLED")
            assert (await nodes["tts"].snapshot()).data.completed == before
        finally:
            await runtime.close()
