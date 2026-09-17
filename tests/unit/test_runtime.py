import asyncio
from pathlib import Path

import httpx
import pytest

from wrs_agent.bindings import load_bindings
from wrs_agent.planner.model import ModelPlanner
from wrs_agent.planner.providers.glm import GLMClient, GLMConfig
from wrs_agent.planner.providers.mock import MockClient
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import GoalRequest, TaskControl


class OfflineNode:
    """Unit-only boundary fixture; never used as network integration evidence."""

    def __init__(self, executor):
        self.executor = executor

    async def capabilities(self):
        return self.executor.capabilities()

    async def snapshot(self, *, control=False):
        return self.executor.snapshot()

    async def hold(self, request):
        return await self.executor.hold(request)


@pytest.mark.parametrize("provider_kind", ["mock", "glm_http_fixture"])
async def test_late_model_revision_is_rejected(make_env, provider_kind):
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
        old_revision = runtime.revision
        await runtime.hold(TaskControl(request_id="stop"))
        gate.set()
        await runtime.planning
        assert runtime.revision > old_revision
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
        await runtime.hold(TaskControl(request_id="stop"))
        provider.gate.set()
        await runtime.planning
        assert runtime.state == "HELD" and runtime.planning_state == "STALE"
        assert "obsolete" not in runtime.reason and env.executions == 0
    finally:
        await runtime.close()
        await env.close()
