"""Actual GLM HTTP adapter with offline responses; action traffic uses real Zenoh."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from conftest import eventually

from wrs_agent.bindings import load_bindings
from wrs_agent.nodes.agent import register_runtime
from wrs_agent.planner import ModelPlanner
from wrs_agent.planner.providers.glm import GLMClient, GLMConfig
from wrs_agent.processes import LocalStack
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import ActionRequest, ControlRequest, new_id

pytestmark = pytest.mark.zenoh
FIXTURE = Path(__file__).parents[2] / "examples/fixtures/glm_tool_call.json"


async def test_glm_plan_runs_on_remote_mock_nodes():
    async with LocalStack(bindings="tests/fixtures/actions.toml", duration=0.03) as stack:

        def reply(request):
            import json

            body = json.loads(request.content)
            context = json.loads(body["messages"][1]["content"])["context"]
            assert all("lease_id" not in state for state in context["world"].values())
            assert context["skills"]
            assert all("name: robot" in skill["instructions"] for skill in context["skills"])
            assert all("node" not in skill for skill in context["skills"])
            return httpx.Response(200, content=FIXTURE.read_bytes())

        client = GLMClient(GLMConfig(model="fixture"), transport=httpx.MockTransport(reply))
        nodes = {
            "wrs": stack.system.clients["wrs"],
            "tts": stack.system.clients["tts"],
        }
        runtime = Runtime(nodes, load_bindings()[1], ModelPlanner(client))
        register_runtime(stack.system.clients["wrs"].transport, runtime)
        try:
            await stack.system.clients["wrs"].transport.request(
                "request/task/goal", {"request_id": new_id(), "goal": "put A in B"}
            )
            await eventually(lambda: runtime.snapshot(), lambda s: s["state"] == "SUCCEEDED")
            assert (await nodes["wrs"].snapshot()).data.objects["A"] == "B"
            assert runtime.planner_calls == 1
        finally:
            await runtime.close()
            await client.aclose()


async def test_pending_glm_does_not_block_queries_or_control():
    gate, entered = asyncio.Event(), asyncio.Event()

    async def respond(request):
        entered.set()
        await gate.wait()
        return httpx.Response(200, content=FIXTURE.read_bytes())

    async with LocalStack(bindings="tests/fixtures/actions.toml", duration=2.0) as stack:
        client = GLMClient(GLMConfig(model="fixture"), transport=httpx.MockTransport(respond))
        nodes = {
            "wrs": stack.system.clients["wrs"],
            "tts": stack.system.clients["tts"],
        }
        runtime = Runtime(nodes, load_bindings()[1], ModelPlanner(client))
        register_runtime(stack.system.clients["wrs"].transport, runtime)
        try:
            actions = {}
            for name, skill, args in [
                ("wrs", "move_named_pose", {"pose": "B"}),
                ("tts", "speak", {"text": "still listening"}),
            ]:
                w = await nodes[name].context()
                action = ActionRequest(
                    action_id=new_id(),
                    task_id=new_id(),
                    task_revision=0,
                    boot_id=w.boot_id,
                    control_epoch=w.control_epoch,
                    lease_id=w.lease_id,
                    world_version=w.world_version,
                    skill=skill,
                    args=args,
                )
                assert (await nodes[name].submit(action)).accepted
                actions[name] = action
                await eventually(
                    lambda n=name: nodes[n].status(actions[n].action_id),
                    lambda status: status.state == "RUNNING",
                )
            await stack.system.clients["wrs"].transport.request(
                "request/task/goal", {"request_id": new_id(), "goal": "put A in B"}
            )
            await asyncio.wait_for(entered.wait(), 2)
            state = await stack.system.clients["wrs"].transport.request("request/task/status", {})
            assert state["planning"] == "WAITING"
            tts_world = await nodes["tts"].context(control=True)
            assert (
                await nodes["tts"].cancel(
                    ControlRequest(
                        interrupt_id=new_id(),
                        boot_id=tts_world.boot_id,
                        control_epoch=tts_world.control_epoch,
                        action_id=actions["tts"].action_id,
                    )
                )
            ).accepted
            await eventually(
                lambda: nodes["tts"].status(actions["tts"].action_id),
                lambda status: status.state == "CANCELLED",
            )
            assert (await nodes["wrs"].snapshot()).active_action == actions["wrs"].action_id
            world = await nodes["wrs"].snapshot(control=True)
            assert (
                await nodes["wrs"].control(
                    "hold",
                    ControlRequest(
                        interrupt_id=new_id(),
                        boot_id=world.boot_id,
                        control_epoch=world.control_epoch,
                    ),
                )
            ).accepted
            gate.set()
            await runtime.planning
            assert runtime.planning_state == "STALE"
            assert (await stack.system.clients["wrs"].transport.request("request/health", {}))[
                "executions"
            ] == 1
        finally:
            gate.set()
            await runtime.close()
            await client.aclose()


@pytest.mark.parametrize("fault", ["partial", "authority", "cycle"])
async def test_invalid_glm_proposal_never_reaches_action_nodes(fault):
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    function = body["choices"][0]["message"]["tool_calls"][0]["function"]
    if fault == "partial":
        function["arguments"] = '{"kind":"execute","plan":'
    else:
        proposal = json.loads(function["arguments"])
        if fault == "authority":
            proposal["control_epoch"] = 999
        else:
            proposal["plan"]["steps"][0]["depends_on"] = ["verify"]
        function["arguments"] = json.dumps(proposal)
    async with LocalStack(bindings="tests/fixtures/actions.toml", duration=0.01) as stack:
        model = GLMClient(
            GLMConfig(model="fixture"),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        )
        nodes = {
            "wrs": stack.system.clients["wrs"],
            "tts": stack.system.clients["tts"],
        }
        runtime = Runtime(nodes, load_bindings()[1], ModelPlanner(model))
        register_runtime(stack.system.clients["wrs"].transport, runtime)
        try:
            await stack.system.clients["wrs"].transport.request(
                "request/task/goal", {"request_id": new_id(), "goal": "put A in B"}
            )
            result = await eventually(runtime.snapshot, lambda s: s["planning"] == "FAILED")
            assert result["state"] == "FAILED" and result["task_id"] is None
            assert result["active_actions"] == {} and result["action_history"] == []
            for node in nodes.values():
                assert (await node.transport.request("request/health", {}))["executions"] == 0
        finally:
            await runtime.close()
            await model.aclose()
