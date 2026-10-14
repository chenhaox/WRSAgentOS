"""Actual HTTP model adapters with offline responses; action traffic uses real Zenoh."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from conftest import eventually, submit_request

from wrs_agent.bindings import load_bindings
from wrs_agent.nodes.agent.rpc import register_runtime
from wrs_agent.planner import ModelPlanner
from wrs_agent.planner.providers.llm import LLMClient, LLMConfig
from wrs_agent.processes import LocalStack
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import ActionRequest, ControlRequest, new_id

pytestmark = pytest.mark.zenoh
FIXTURES = Path(__file__).parents[1] / "fixtures/models"
FIXTURE = FIXTURES / "openai_chat_tool_call.json"
CONFIG = LLMConfig(model="fixture", base_url="https://model.invalid/v1")
PROTOCOLS = {
    # Protocol -> (reply fixture, where the request carries the goal and context).
    "openai_chat": ("openai_chat_tool_call.json", lambda body: body["messages"][1]["content"]),
    "openai_responses": ("openai_responses_function_call.json", lambda body: body["input"]),
    "anthropic_messages": ("anthropic_tool_use.json", lambda body: body["messages"][0]["content"]),
}


@pytest.mark.parametrize("protocol,output_mode", [
    *[(protocol, "tools") for protocol in PROTOCOLS],
    ("openai_chat", "json_object"), ("openai_chat", "json_schema"),
])
async def test_model_plan_runs_on_remote_mock_nodes(protocol, output_mode):
    fixture, user_content = PROTOCOLS[protocol]
    async with LocalStack(bindings="tests/fixtures/actions.toml", duration=0.03) as stack:

        def reply(request):
            context = json.loads(user_content(json.loads(request.content)))["context"]
            assert all("lease_id" not in state for state in context["world"].values())
            # Each skill carries its own package's instructions; speak lives in speech.
            packages = {s["name"]: s["instructions"].split("\n")[1] for s in context["skills"]}
            assert packages.pop("speak") == "name: speech"
            assert packages and set(packages.values()) == {"name: robot"}
            assert all("node" not in skill for skill in context["skills"])
            body = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
            if output_mode != "tools":
                assert json.loads(request.content)["response_format"]["type"] == output_mode
                arguments = body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
                body["choices"][0].update(
                    message={"role": "assistant", "content": arguments}, finish_reason="stop",
                )
            return httpx.Response(200, json=body)

        client = LLMClient(
            CONFIG.model_copy(update={"protocol": protocol, "output_mode": output_mode}),
            transport=httpx.MockTransport(reply),
        )
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
            assert (await nodes["wrs"].snapshot()).data.objects["A"].location == "B"
            assert runtime.planner_calls == 1
            planning = runtime.snapshot()["last_planning"]
            assert 0 < planning["model_s"] <= planning["total_s"]
            # Chat names these prompt/completion tokens; the overview shows one set of names.
            assert (planning["input_tokens"], planning["output_tokens"]) == (30, 70)
        finally:
            await runtime.close()
            await client.aclose()


async def test_pending_model_does_not_block_queries_or_control():
    gate, entered = asyncio.Event(), asyncio.Event()

    async def respond(request):
        entered.set()
        await gate.wait()
        return httpx.Response(200, content=FIXTURE.read_bytes())

    async with LocalStack(bindings="tests/fixtures/actions.toml", duration=2.0) as stack:
        client = LLMClient(CONFIG, transport=httpx.MockTransport(respond))
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
                    state_version=w.state_version,
                    skill=skill,
                    args=args,
                )
                assert (await submit_request(nodes[name], action)).accepted
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
            await runtime.planning.worker
            assert runtime.planning.state == "STALE"
            assert (await stack.system.clients["wrs"].transport.request("request/health", {}))[
                "executions"
            ] == 1
        finally:
            gate.set()
            await runtime.close()
            await client.aclose()


@pytest.mark.parametrize("fault", ["partial", "authority", "cycle", "direct_skill"])
@pytest.mark.parametrize("output_mode", ["tools", "json_object", "json_schema"])
async def test_invalid_model_proposal_never_reaches_action_nodes(fault, output_mode):
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    function = body["choices"][0]["message"]["tool_calls"][0]["function"]
    if fault == "partial":
        function["arguments"] = '{"kind":"execute","plan":'
    elif fault == "direct_skill":
        function.update(name="describe_objects", arguments="{}")
    else:
        proposal = json.loads(function["arguments"])
        if fault == "authority":
            proposal["control_epoch"] = 999
        else:
            proposal["plan"]["steps"][0]["depends_on"] = ["verify"]
        function["arguments"] = json.dumps(proposal)
    if output_mode != "tools" and fault != "direct_skill":
        body["choices"][0].update(
            message={"role": "assistant", "content": function["arguments"]}, finish_reason="stop",
        )
    async with LocalStack(bindings="tests/fixtures/actions.toml", duration=0.01) as stack:
        model = LLMClient(
            CONFIG.model_copy(update={"output_mode": output_mode}),
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
            expected = {
                "partial": "llm_plan_json_invalid", "direct_skill": "llm_unexpected_tool",
                "authority": "llm_plan_invalid", "cycle": "llm_plan_invalid",
            }[fault]
            assert result["planning_error"]["code"] == expected
            remote = await stack.system.clients["wrs"].transport.request("request/task/status", {})
            assert remote["planning_error"] == result["planning_error"]
            for node in nodes.values():
                assert (await node.transport.request("request/health", {}))["executions"] == 0
        finally:
            await runtime.close()
            await model.aclose()


async def test_model_failure_keeps_safe_provider_code_in_planning_result():
    async with LocalStack(bindings="configs/actions.toml", duration=0.01) as stack:
        model = LLMClient(
            CONFIG,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(401, text="private-provider-body")
            ),
        )
        runtime = Runtime(stack.system.clients, load_bindings()[1], ModelPlanner(model))
        try:
            from wrs_agent.schemas import GoalRequest

            await runtime.goal(GoalRequest(request_id="unauthorized-model", goal="put A in B"))
            await runtime.planning.worker
            result = runtime.goal_status("unauthorized-model")
            assert result["state"] == "FAILED"
            assert result["error"]["code"] == "llm_http_401"
            assert result["error"]["stage"] == "planning"
            assert "private-provider-body" not in str(result)
            assert not runtime.action_history
        finally:
            await runtime.close()
            await model.aclose()


async def test_agent_without_live_opt_in_runs_explicit_tasks_only(llm_server):
    from wrs_agent import step
    from wrs_agent.transport import RemoteError

    async with LocalStack(duration=0.02) as stack:
        system = stack.system
        with pytest.raises(RemoteError, match="planner_unavailable_or_busy"):
            await system.goal("put A in B")
        assert llm_server.requests == []
        task = await system.start(step("move_named_pose", pose="B"))
        assert (await task.wait()).state == "SUCCEEDED"
        assert (await system.status())["planner_calls"] == 0
