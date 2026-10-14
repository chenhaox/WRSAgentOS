"""Contract drift and operation stage must be data, never inferred from error text."""

from dataclasses import replace

import pytest
from conftest import action, eventually
from pydantic import ValidationError
from test_registry import QueryFixture, registry, speaker
from test_runtime import OfflineNode, motion

from wrs_agent import AgentError
from wrs_agent.bindings import load_bindings
from wrs_agent.cache import PlanCache
from wrs_agent.nodes.tts.backend import make_mock_tts
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import FeatureSnapshot, TaskRequest
from wrs_agent.skills import lookup_skills
from wrs_agent.system import step


@pytest.mark.parametrize("offer", [["speak"], {"speak": 0}, {"speak": True}, {"speak": "1"}])
def test_features_require_explicit_positive_integer_versions(offer):
    with pytest.raises(ValidationError):
        FeatureSnapshot(skills=offer)


async def test_version_mismatch_blocks_entire_plan_before_any_branch(make_env, tmp_path):
    robot = make_env()
    tts = make_mock_tts(tmp_path / "tts.db")
    tts.skills["speak"] = replace(
        tts.skills["speak"], version=2
    )
    runtime = Runtime({"wrs": OfflineNode(robot), "tts": OfflineNode(tts)}, load_bindings()[1])
    try:
        assert tts.features().skills == {"speak": 2}
        task = await runtime.start(
            TaskRequest(
                request_id="different-version",
                plan={
                    "steps": [
                        step("move_named_pose", pose="B"),
                        step("speak", text="must not run").model_copy(update={"version": 1}),
                    ]
                },
            )
        )
        await eventually(runtime.snapshot, lambda s: s["state"] == "FAILED")
        failure = runtime.task_status(task["task_id"])["error"]
        assert failure["code"] == "skill_version_mismatch"
        assert failure["node_id"] == "tts" and failure["task_id"] == task["task_id"]
        assert failure["stage"] == "preflight" and failure["action_id"] is None
        assert robot.executions == tts.executions == 0
        assert not robot.records and not tts.records
        # Direct callers face the same contract check at the node.
        rejected = await tts.submit(action(tts, "speak", {"text": "old version"}))
        assert rejected.error.code == "skill_version_mismatch"
        assert rejected.error.stage == "submit" and not rejected.accepted
        accepted = await tts.submit(action(tts, "speak", {"text": "version two"}, version=2))
        assert accepted.accepted
        await tts.runner
        assert tts.status(accepted.status.action_id).state == "SUCCEEDED"
        assert tts.executions == 1
    finally:
        await runtime.close()
        await robot.close()
        await tts.close()


@pytest.mark.parametrize(
    "failure,state,code,stage",
    [
        ("snapshot", "FAILED", "request_timeout", "preflight"),
        ("context", "FAILED", "request_timeout", "preflight"),
        ("message", "FAILED", "internal_error", "preflight"),
        ("submit", "UNKNOWN", "execution_unknown", "submit"),
        ("status", "UNKNOWN", "execution_unknown", "observe"),
    ],
)
async def test_runtime_failure_classification_depends_on_submit_stage(
    make_env, failure, state, code, stage
):
    env = make_env(duration=0.2)
    node = OfflineNode(env)
    original = node._submit

    async def timeout(*args, **kwargs):
        raise TimeoutError("sensitive details must not be exposed")

    async def internal():
        raise RuntimeError("UNKNOWN text is not an execution state; sensitive details")

    async def lost(request):
        await original(request)
        raise TimeoutError("lost reply after acceptance")

    if failure in {"snapshot", "context", "status"}:
        setattr(node, failure, timeout)
    elif failure == "submit":
        node._submit, node.status = lost, timeout
    else:
        node.context = internal
    runtime = Runtime({"wrs": node}, load_bindings()[1])
    try:
        task = await runtime.start(TaskRequest(request_id="fault", plan=motion()))
        result = await eventually(runtime.snapshot, lambda s: s["state"] == state)
        error = runtime.task_status(task["task_id"])["error"]
        assert error["code"] == code and error["stage"] == stage
        assert error["node_id"] == "wrs" and error["task_id"] == task["task_id"]
        assert "sensitive" not in str(result)
        if state == "FAILED":
            assert env.executions == 0 and not env.records and env.epoch == 0
            assert error["action_id"] is None
        else:
            assert len(env.records) == 1  # No blind retry.
            assert error["action_id"] == next(iter(env.records))
    finally:
        await runtime.close()
        await env.close()


async def test_resource_rejection_does_not_stop_another_clients_action(make_env):
    env = make_env(duration=0.15)
    request = action(env, "move_named_pose", {"pose": "B"})
    await env.submit(request)
    runtime = Runtime({"wrs": OfflineNode(env)}, load_bindings()[1])
    try:
        task = await runtime.start(TaskRequest(request_id="conflict", plan=motion("C")))
        await eventually(runtime.snapshot, lambda s: s["state"] == "FAILED")
        error = runtime.task_status(task["task_id"])["error"]
        assert error["code"] == "resource_busy" and error["stage"] == "submit"
        assert env.epoch == 0
        await env.runner
        assert env.status(request.action_id).state == "SUCCEEDED"
        assert env.world.pose == "B" and env.executions == 1
    finally:
        await runtime.close()
        await env.close()


@pytest.mark.parametrize(
    "situation,code",
    [
        ("unbound", "provider_not_found"),
        ("offline", "node_unavailable"),
        ("held", "node_not_ready"),
        ("ambiguous", "node_ambiguous"),
        ("version", "skill_version_mismatch"),
    ],
)
async def test_provider_failures_remain_distinguishable(situation, code):
    bus = QueryFixture(speaker())
    view = registry(bus)
    if situation == "unbound":
        view.bindings.clear()
    elif situation == "offline":
        bus.emit("first", False)
    elif situation == "held":
        bus.info = speaker(ready=False, health="held")
    elif situation == "ambiguous":
        bus.emit("second", True)
    elif situation == "version":
        bus.info = speaker(skills={"speak": 2})
    await view.refresh()
    with pytest.raises(AgentError) as caught:
        view.node_for("not_advertised" if situation == "unbound" else "speak", 1)
    assert caught.value.code == code
    if situation != "unbound":
        assert caught.value.error.node_id == "speaker"


def test_version_drift_invalidates_lookup_and_cached_plan():
    from test_cache import BINDINGS, context, plan

    worlds, caps = context()
    cache = PlanCache()
    assert cache.remember("put A in B", plan(), worlds, caps, BINDINGS)
    cap = caps["wrs"]
    caps["wrs"] = cap.model_copy(update={"skills": {**cap.skills, "pick": 2}})
    assert cache.lookup("put A in B", worlds, caps, BINDINGS) is None
    assert cache.reject_reason == "skill_version_mismatch"
    assert "pick" not in {s.name for s in lookup_skills(caps, BINDINGS)}


def test_step_defers_version_to_the_discovered_provider():
    assert step("speak", text="new contract").version is None


async def test_planning_error_does_not_leak_inputs_through_result_or_cache(make_env):
    from types import SimpleNamespace

    from wrs_agent.schemas import GoalRequest

    async def fail(request):
        raise RuntimeError("private-planner-input")

    env = make_env()
    runtime = Runtime({"wrs": OfflineNode(env)}, load_bindings()[1], SimpleNamespace(plan=fail))
    try:
        await runtime.goal(GoalRequest(request_id="failed-plan", goal="put A in B"))
        await runtime.planning.worker
        result = runtime.goal_status("failed-plan")
        assert result["state"] == "FAILED"
        assert result["error"]["code"] == "internal_error"
        assert result["error"]["stage"] == "planning"
        assert set(runtime.cache.failures.values()) == {"internal_error"}
        assert "private-planner-input" not in str(result)
        assert "private-planner-input" not in str(runtime.snapshot())
        overview = runtime.snapshot()
        assert overview["planning_error"] == result["error"]
        assert overview["error"] is None and overview["task_id"] is None
        overview["planning_error"]["code"] = "changed-by-caller"
        assert runtime.snapshot()["planning_error"]["code"] == "internal_error"
        result["error"]["code"] = "changed-by-caller"
        assert runtime.goal_status("failed-plan")["error"]["code"] == "internal_error"
        assert env.executions == 0
    finally:
        await runtime.close()
        await env.close()
