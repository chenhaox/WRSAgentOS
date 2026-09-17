import json
from pathlib import Path

import httpx
import pytest

from wrs_agent.bindings import load_bindings
from wrs_agent.environments.api import ActionClient
from wrs_agent.planner.model import ModelPlanner
from wrs_agent.planner.providers.glm import GLMClient, GLMConfig
from wrs_agent.processes import LocalStack
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import GoalRequest, new_id

pytestmark = pytest.mark.zenoh
FIXTURE = Path(__file__).parents[2] / "examples/fixtures/glm_tool_call.json"


@pytest.mark.parametrize("fault", [None, "grasp"])
async def test_verified_cache_hits_have_new_ids_and_failures_are_not_cached(fault):
    calls = []

    def reply(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, content=FIXTURE.read_bytes())

    async with LocalStack(runtime=False, duration=0.03, fault=fault) as stack:
        model = GLMClient(GLMConfig(model="fixture"), transport=httpx.MockTransport(reply))
        nodes = {name: ActionClient(bus) for name, bus in stack.node_transports.items()}
        runtime = Runtime(nodes, load_bindings()[1], ModelPlanner(model))
        try:
            for _ in range(3):
                await runtime.goal(GoalRequest(request_id=new_id(), goal="put A in B"))
                await runtime.planning
            if fault is None:
                assert runtime.state == "SUCCEEDED"
                assert runtime.cache.last_hit and runtime.cache.hits == 1
                assert runtime.planner_calls == len(calls) == 2
                history = runtime.snapshot()["action_history"]
                assert len(history) == len({a["action_id"] for a in history}) == 12
                assert (await stack.transport.request("request/health", {}))["executions"] == 12
            else:
                assert runtime.state == "FAILED"
                assert len(calls) == 3 and not runtime.cache.entries
                assert runtime.cache.hits == 0
        finally:
            await runtime.close()
            await model.aclose()
