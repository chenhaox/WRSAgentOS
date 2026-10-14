import asyncio
import json
import time
from dataclasses import replace

import httpx
import pytest
from conftest import eventually
from llm_fixtures import chat_reply
from test_runtime import OfflineNode

from wrs_agent.executor import ActionExecutor
from wrs_agent.nodes.tts.backend import make_mock_tts
from wrs_agent.nodes.vision.node import VisionState
from wrs_agent.nodes.vision.results import Objects2D
from wrs_agent.planner import ModelPlanner
from wrs_agent.planner.providers.wire import SYSTEM_PROMPT
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import (
    ActionRequest,
    GoalRequest,
    Plan,
    Step,
    TaskCancelRequest,
    TaskRequest,
    new_id,
)
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


@pytest.mark.parametrize("tool_name", ["describe_objects", "speak", "private-model-input"])
async def test_direct_skill_call_fails_then_valid_visual_plan_speaks(pipeline, make_llm, tool_name):
    """Reproduce the observed reply shape; only a validated proposal can reach either node."""
    runtime, vision, tts = pipeline
    calls = []
    # The actual example sent to models must obey the current plan and skill contracts.
    proposal, _ = json.JSONDecoder().raw_decode(SYSTEM_PROMPT[SYSTEM_PROMPT.index('{"kind":'):])

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            body = chat_reply('{"private-model-input":"not-for-the-viewer"}')
            body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = tool_name
        else:
            body = chat_reply(json.dumps(proposal))
        return httpx.Response(200, json=body)

    runtime.planner = ModelPlanner(make_llm(respond))
    await runtime.goal(GoalRequest(request_id="bad-tool", goal="桌子上有哪些物体？"))
    await runtime.planning.worker
    result = runtime.snapshot()
    assert result["planning"] == "FAILED" and result["task_id"] is None
    assert result["planning_error"]["code"] == "llm_unexpected_tool"
    assert result["planning_error"]["stage"] == "planning" and result["error"] is None
    assert result["planning_error"] == runtime.goal_status("bad-tool")["error"]
    assert "private-model-input" not in str(result)
    assert "not-for-the-viewer" not in str(result)
    assert vision.executions == tts.executions == 0
    assert len(calls) == 1  # No automatic paid retry or attempt to execute the unoffered call.
    result["planning_error"]["code"] = "changed-by-caller"
    assert runtime.snapshot()["planning_error"]["code"] == "llm_unexpected_tool"

    await runtime.goal(GoalRequest(request_id="valid-tool", goal="桌子上有哪些物体？"))
    assert runtime.snapshot()["planning_error"] is None
    await runtime.planning.worker
    assert runtime.snapshot()["state"] == "SUCCEEDED"
    assert runtime.snapshot()["planning_error"] is None
    assert vision.executions == tts.executions == 1
    assert tts.world.last_text == "画面中未识别到物体。"
    assert len(calls) == 2


@pytest.mark.parametrize("arguments,code", [
    ('{"kind":"execute","private-model-input":', "llm_plan_json_invalid"),
    ('{"kind":"execute","plan":"private-model-input"}', "llm_plan_invalid"),
    (json.dumps({"kind": "execute", "plan": plan().model_dump() | {"text": ""}}),
     "llm_plan_invalid"),
])
@pytest.mark.parametrize("output_mode", ["tools", "json_object", "json_schema"])
async def test_malformed_visual_plan_reports_safe_error_without_actions(
    pipeline, make_llm, arguments, code, output_mode,
):
    runtime, vision, tts = pipeline

    def respond(_):
        body = chat_reply(arguments)
        if output_mode != "tools":
            body["choices"][0].update(
                message={"role": "assistant", "content": arguments}, finish_reason="stop",
            )
        return httpx.Response(200, json=body)

    runtime.planner = ModelPlanner(make_llm(respond, output_mode=output_mode))
    await runtime.goal(GoalRequest(request_id="bad-json", goal="桌子上有哪些物体？"))
    await runtime.planning.worker
    result = runtime.snapshot()
    assert result["planning"] == "FAILED" and result["task_id"] is None
    assert result["planning_error"]["code"] == code
    assert result["planning_error"]["stage"] == "planning"
    assert "private-model-input" not in str(result)
    assert vision.executions == tts.executions == 0 and runtime.planner_calls == 1


@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
async def test_json_visual_proposal_describes_then_speaks_observed_output(pipeline, make_llm, mode):
    runtime, vision, tts = pipeline
    proposal = json.dumps({"kind": "execute", "text": "", "plan": plan().model_dump()})

    def respond(request):
        body = json.loads(request.content)
        assert body["response_format"]["type"] == mode and "tools" not in body
        assert json.loads(body["messages"][1]["content"])["goal"] == "看看画面里有什么。"
        reply = chat_reply("")
        reply["choices"][0].update(
            message={"role": "assistant", "content": proposal}, finish_reason="stop",
        )
        return httpx.Response(200, json=reply)

    runtime.planner = ModelPlanner(make_llm(respond, output_mode=mode))
    await runtime.goal(GoalRequest(request_id="json-visual", goal="看看画面里有什么。"))
    await runtime.planning.worker
    assert runtime.snapshot()["state"] == "SUCCEEDED"
    assert runtime.snapshot()["planning_error"] is None
    assert vision.executions == tts.executions == runtime.planner_calls == 1
    assert tts.world.last_text == "画面中未识别到物体。"
    description = vision.status(runtime.action_history[0]["action_id"]).output
    assert description["text"] == tts.world.last_text
    assert description["observation_id"] == "s.1"


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
