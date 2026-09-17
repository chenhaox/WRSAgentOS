import asyncio

from wrs_agent.bindings import load_bindings
from wrs_agent.planner.model import ModelPlanner
from wrs_agent.planner.providers.mock import MockClient
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import GoalRequest, TaskControl


class OfflineNode:
    """Unit-only boundary fixture; never used as network integration evidence."""

    def __init__(self, executor):
        self.executor = executor

    async def snapshot(self, *, control=False):
        return self.executor.snapshot()

    async def hold(self, request):
        return await self.executor.hold(request)


async def test_late_model_revision_is_rejected(make_env):
    env = make_env()
    provider = MockClient(
        '{"kind":"execute","plan":{"steps":['
        '{"step_id":"pick","skill":"pick","args":{"object":"A"}}]}}',
        deferred=True,
    )
    _, bindings = load_bindings()
    runtime = Runtime({"wrs": OfflineNode(env)}, bindings, ModelPlanner(provider))
    try:
        await runtime.goal(GoalRequest(request_id="goal", goal="pick A"))
        await asyncio.wait_for(provider.entered.wait(), 1)
        old_revision = runtime.revision
        await runtime.hold(TaskControl(request_id="stop"))
        provider.gate.set()
        await runtime.planning
        assert runtime.revision > old_revision
        assert runtime.planning_state == "STALE"
        assert env.executions == 0 and env.admission == "HELD"
    finally:
        await runtime.close()
        await env.close()
