import asyncio
from pathlib import Path

import httpx
import pytest
from conftest import control, eventually

from wrs_agent.bindings import load_bindings
from wrs_agent.planner import ModelPlanner
from wrs_agent.planner.providers.glm import GLMClient, GLMConfig
from wrs_agent.planner.providers.mock import MockClient
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import GoalRequest, Plan, Step, TaskControl, TaskRequest, new_id


class OfflineNode:
    """Unit-only boundary fixture; never used as network integration evidence."""

    def __init__(self, executor):
        self.executor = executor

    async def capabilities(self):
        return self.executor.capabilities()

    async def snapshot(self, *, control=False):
        return self.executor.snapshot()

    async def context(self, *, control=False):
        return self.executor.context()

    async def control(self, kind, request):
        return await self.executor.control(kind, request)

    async def submit(self, request):
        return await self.executor.submit(request)

    async def status(self, action_id):
        return self.executor.status(action_id)


@pytest.mark.parametrize("provider_kind", ["mock", "glm_http_fixture"])
async def test_late_model_after_stop_is_rejected(make_env, provider_kind):
    env = make_env()
    provider = MockClient(
        '{"kind":"execute","plan":{"steps":['
        '{"step_id":"pick","skill":"pick","args":{"object":"A"}}]}}',
        deferred=True,
    )
    entered, gate = provider.entered, provider.gate
    if provider_kind == "glm_http_fixture":
        await provider.aclose()
        entered, gate = asyncio.Event(), asyncio.Event()

        async def respond(request):
            entered.set()
            await gate.wait()
            fixture = Path(__file__).parents[2] / "examples/fixtures/glm_tool_call.json"
            return httpx.Response(200, content=fixture.read_bytes())

        provider = GLMClient(GLMConfig(model="fixture"), transport=httpx.MockTransport(respond))
    _, bindings = load_bindings()
    runtime = Runtime({"wrs": OfflineNode(env)}, bindings, ModelPlanner(provider))
    try:
        await runtime.goal(GoalRequest(request_id="goal", goal="pick A"))
        await asyncio.wait_for(entered.wait(), 1)
        assert runtime.task_id is None
        before = env.epoch
        await env.hold(control(env))
        gate.set()
        await runtime.planning
        assert env.epoch > before
        assert runtime.task_id is None
        assert runtime.planning_state == "STALE"
        assert env.executions == 0 and env.admission == "HELD"
    finally:
        await runtime.close()
        await env.close()
        await provider.aclose()


async def test_invalid_model_plan_finishes_failed_without_actions(make_env):
    env = make_env()
    provider = MockClient(
        '{"kind":"execute","plan":{"steps":[{"step_id":"bad","skill":"unregistered"}]}}'
    )
    runtime = Runtime({"wrs": OfflineNode(env)}, load_bindings()[1], ModelPlanner(provider))
    try:
        await runtime.goal(GoalRequest(request_id="bad-plan", goal="put A in B"))
        await runtime.planning
        assert runtime.state == "FAILED" and runtime.planning_state == "FAILED"
        assert env.executions == 0 and not runtime.cache.entries
    finally:
        await runtime.close()
        await env.close()


async def test_late_model_error_does_not_overwrite_hold(make_env):
    def obsolete(request):
        raise ValueError("obsolete model failure")

    env = make_env()
    provider = MockClient(obsolete, deferred=True)
    runtime = Runtime({"wrs": OfflineNode(env)}, load_bindings()[1], ModelPlanner(provider))
    try:
        await runtime.goal(GoalRequest(request_id="late-error", goal="put A in B"))
        await provider.entered.wait()
        await env.hold(control(env))
        provider.gate.set()
        await runtime.planning
        assert env.admission == "HELD" and runtime.planning_state == "STALE"
        assert runtime.task_id is None and runtime.state == "IDLE"
        assert "obsolete" not in runtime.reason and env.executions == 0
    finally:
        await runtime.close()
        await env.close()


def motion(pose="B"):
    return Plan(steps=[Step(step_id="move", skill="move_named_pose", args={"pose": pose})])


@pytest.mark.parametrize("late_error", [False, True], ids=["reply", "error"])
async def test_old_planner_cannot_change_new_execution(make_env, late_error):
    def respond(request):
        if late_error:
            raise ValueError("obsolete failure")
        return (
            '{"kind":"execute","plan":{"steps":['
            '{"step_id":"old","skill":"pick","args":{"object":"A"}}]}}'
        )

    env = make_env(duration=0.1)
    provider = MockClient(respond, deferred=True)
    runtime = Runtime({"wrs": OfflineNode(env)}, load_bindings()[1], ModelPlanner(provider))
    try:
        ack = await runtime.goal(GoalRequest(request_id="old-goal", goal="pick A"))
        await provider.entered.wait()
        assert ack["request_id"] == "old-goal" and runtime.task_id is None
        newer = await runtime.start(TaskRequest(request_id="new-execution", plan=motion()))
        provider.gate.set()
        await runtime.planning
        await eventually(runtime.snapshot, lambda s: s["state"] == "SUCCEEDED")
        assert runtime.task_id == newer["task_id"]
        assert runtime.planning_state == "IDLE" and runtime.reason == ""
        assert env.executions == 1 and env.world.pose == "B"
        assert all(r[0]["task_revision"] == 0 for r in env.records.values())
    finally:
        await runtime.close()
        await env.close()
        await provider.aclose()


async def test_task_and_queued_plan_are_detached_and_keep_their_ids(make_env):
    env = make_env(duration=0.05)
    runtime = Runtime({"wrs": OfflineNode(env)}, load_bindings()[1])
    try:
        first, second = motion("B"), motion("C")
        a = await runtime.start(TaskRequest(request_id="first", plan=first))
        b = await runtime.enqueue(TaskRequest(request_id="second", plan=second))
        first.steps[0].args["pose"] = "not-a-pose"
        first.steps.clear()
        second.steps[0].args["pose"] = "not-a-pose"
        second.steps[0].depends_on.append("missing")
        await eventually(
            runtime.snapshot, lambda s: s["task_id"] == b["task_id"] and s["state"] == "SUCCEEDED"
        )
        assert a["task_id"] != b["task_id"]
        assert env.executions == 2 and env.world.pose == "C"
        assert {r[0]["task_id"] for r in env.records.values()} == {a["task_id"], b["task_id"]}
        with pytest.raises(ValueError, match="stale_task"):
            await runtime.hold(TaskControl(request_id="late", task_id=a["task_id"]))
    finally:
        await runtime.close()
        await env.close()


async def test_replacement_new_id_and_old_controls_cannot_affect_it(make_env):
    env = make_env(duration=0.1)
    runtime = Runtime({"wrs": OfflineNode(env)}, load_bindings()[1])
    try:
        a = await runtime.start(TaskRequest(request_id="first", plan=motion()))
        await eventually(
            env.snapshot,
            lambda w: (
                w.active_action is not None and env.status(w.active_action).state == "RUNNING"
            ),
        )
        old_action = env.active
        stop = TaskControl(request_id="hold-a", task_id=a["task_id"])
        receipt = await runtime.hold(stop)
        assert receipt["accepted"]
        request = TaskControl(request_id="replace-a", task_id=a["task_id"], replacement=motion("C"))
        b = await runtime.replace(request)
        assert b["task_id"] != a["task_id"] and b["supersedes"] == a["task_id"]
        assert await runtime.replace(request) == b
        for kind in ("hold", "replace"):
            with pytest.raises(ValueError, match="stale_task"):
                await getattr(runtime, kind)(
                    TaskControl(
                        request_id=new_id(),
                        task_id=a["task_id"],
                        replacement=motion() if kind == "replace" else None,
                    )
                )
        assert await runtime.hold(stop) == receipt  # A duplicate never acts again.
        await eventually(runtime.snapshot, lambda s: s["state"] == "SUCCEEDED")
        assert env.status(old_action).state == "CANCELLED"
        assert env.executions == 2 and env.world.pose == "C"
    finally:
        await runtime.close()
        await env.close()


@pytest.mark.parametrize("replacement_skill", ["move_named_pose", "speak"])
async def test_unconfirmed_stop_cannot_execute_replacement(make_env, tmp_path, replacement_skill):
    from wrs_agent.nodes.tts import make_mock_tts

    env = make_env(duration=0.1, fault="stop_unknown")
    tts = make_mock_tts(tmp_path / "tts.sqlite3", duration=0.1)
    runtime = Runtime({"wrs": OfflineNode(env), "tts": OfflineNode(tts)}, load_bindings()[1])
    try:
        a = await runtime.start(TaskRequest(request_id="first", plan=motion()))
        await eventually(
            env.snapshot,
            lambda w: (
                w.active_action is not None and env.status(w.active_action).state == "RUNNING"
            ),
        )
        await runtime.hold(TaskControl(request_id="hold", task_id=a["task_id"]))
        replacement = (
            motion("C")
            if replacement_skill == "move_named_pose"
            else Plan(steps=[Step(step_id="say", skill="speak", args={"text": "must not start"})])
        )
        request = TaskControl(request_id="replace", task_id=a["task_id"], replacement=replacement)
        if runtime.hold_accepted:
            await runtime.replace(request)
            await eventually(runtime.snapshot, lambda s: s["state"] == "UNKNOWN")
        else:
            with pytest.raises(ValueError, match="unconfirmed"):
                await runtime.replace(request)
        assert env.executions == 1 and env.world.pose != "C"
        assert tts.executions == 0
    finally:
        await runtime.close()
        await env.close()
        await tts.close()


@pytest.mark.parametrize("late_failure", ["timeout", "failed_status"])
async def test_late_action_failure_does_not_fence_or_overwrite_replacement(make_env, late_failure):
    from wrs_agent.schemas import ActionStatus

    env = make_env(duration=0.2)
    node = OfflineNode(env)
    entered, release = asyncio.Event(), asyncio.Event()
    original_status = node.status

    async def delayed(action_id):
        if not entered.is_set():
            entered.set()
            await release.wait()
            if late_failure == "timeout":
                raise TimeoutError("old status timeout")
            return ActionStatus(action_id=action_id, state="FAILED", reason="old failure")
        return await original_status(action_id)

    node.status = delayed
    runtime = Runtime({"wrs": node}, load_bindings()[1])
    try:
        a = await runtime.start(TaskRequest(request_id="a", plan=motion()))
        await entered.wait()
        await runtime.hold(TaskControl(request_id="hold", task_id=a["task_id"]))
        b = await runtime.replace(
            TaskControl(request_id="b", task_id=a["task_id"], replacement=motion("C"))
        )
        await eventually(runtime.snapshot, lambda s: s["state"] == "RUNNING")
        epoch = env.epoch
        release.set()
        await eventually(runtime.snapshot, lambda s: s["state"] == "SUCCEEDED")
        assert runtime.task_id == b["task_id"] and runtime.reason == ""
        assert env.epoch == epoch and env.world.pose == "C"
    finally:
        release.set()
        await runtime.close()
        await env.close()
