import asyncio
import time
from dataclasses import replace

import pytest
from conftest import eventually
from test_runtime import OfflineNode

from wrs_agent.executor import ActionExecutor
from wrs_agent.nodes.tts.backend import make_mock_tts
from wrs_agent.nodes.vision.node import VisionState
from wrs_agent.nodes.vision.results import Objects2D
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import ActionRequest, Plan, Step, TaskCancelRequest, TaskRequest, new_id
from wrs_agent.skills.vision import DESCRIBE, Description


def plan(field="text", parameter="text"):
    return Plan(steps=[
        Step(step_id="see", skill="describe_objects"),
        Step(step_id="say", skill="speak", depends_on=["see"],
             inputs={parameter: {"step_id": "see", "field": field}}),
    ])


@pytest.fixture
async def pipeline(tmp_path):
    state = VisionState(latest=Objects2D(
        source="replay", result_id="r1", observation_id="s.1", source_stream_id="s",
        captured_at_ns=1, width=10, height=10, model="fixture", weights_sha256="0" * 64,
    ), observed_at=time.monotonic(), health="ready")
    vision = ActionExecutor(tmp_path / "vision.sqlite3", state=state, skills=[DESCRIBE],
                            backend="fixture", features_extra={"robot_controls": False})
    vision.node_id = "yolo"
    tts = make_mock_tts(tmp_path / "tts.sqlite3", duration=0.01)
    runtime = Runtime({"yolo": OfflineNode(vision), "tts": OfflineNode(tts)},
                      {"describe_objects": "yolo", "speak": "tts"})
    try:
        yield runtime, vision, tts
    finally:
        await runtime.close()
        await vision.close()
        await tts.close()


@pytest.mark.parametrize("field,parameter,code", [
    ("missing", "text", "incompatible_step_output"),
    ("text", "unsafe", "result_input_not_allowed"),
])
async def test_invalid_binding_is_rejected_before_any_action(pipeline, field, parameter, code):
    runtime, vision, tts = pipeline
    await runtime.start(TaskRequest(request_id=new_id(), plan=plan(field, parameter)))
    failure = await eventually(runtime.snapshot, lambda s: s["state"] == "FAILED")
    assert failure["error"]["code"] == code
    assert vision.executions == tts.executions == 0


def test_reference_requires_dependency_and_excludes_literal_argument():
    for update in ({"depends_on": []}, {"args": {"text": "invented"}}):
        value = plan().model_dump()
        value["steps"][1].update(update)
        with pytest.raises(ValueError):
            Plan.model_validate(value)


async def test_success_output_is_retained_by_action_and_survives_latest_change(pipeline):
    runtime, vision, tts = pipeline
    await runtime.start(TaskRequest(request_id=new_id(), plan=plan()))
    await eventually(runtime.snapshot, lambda s: s["state"] == "SUCCEEDED")
    status = vision.status(runtime.action_history[0]["action_id"])
    vision.world.latest = None
    assert status.output["observation_id"] == "s.1"
    assert status.output["text"] == tts.world.last_text == "画面中未识别到物体。"
    original = vision.records[status.action_id][0]
    duplicate = await vision.submit(ActionRequest.model_validate(original))
    assert duplicate.status.output == status.output and vision.executions == 1


async def test_late_description_after_cancel_cannot_reach_speaker(pipeline):
    runtime, vision, tts = pipeline
    entered, gate = asyncio.Event(), asyncio.Event()

    async def delayed(*_):
        entered.set()
        await gate.wait()
        return Description(text="late", result_id="r", observation_id="s.1")

    vision.skills["describe_objects"] = replace(DESCRIBE, handler=delayed)
    receipt = await runtime.start(TaskRequest(request_id=new_id(), plan=plan()))
    await entered.wait()
    await runtime.cancel(TaskCancelRequest(request_id=new_id(), task_id=receipt["task_id"]))
    await eventually(vision.stop_signal.is_set, bool)
    gate.set()
    await eventually(runtime.snapshot, lambda s: s["state"] == "CANCELLED")
    assert tts.executions == 0
    assert not vision.status(runtime.action_history[0]["action_id"]).output
