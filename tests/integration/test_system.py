import asyncio

import pytest

from tests.conftest import eventually
from wrs_agent import System, step
from wrs_agent.bindings import DEFAULT
from wrs_agent.nodes.api import ActionClient
from wrs_agent.processes import LocalStack
from wrs_agent.schemas import ControlRequest, new_id

pytestmark = pytest.mark.zenoh


async def test_system_task_parallel_query_and_scoped_voice_controls():
    async with System.local(duration=2) as system:
        nodes = await system.nodes()
        assert {n for n, s in nodes.items() if s["ready"]} == {"wrs", "tts", "agent", "voice"}
        assert nodes["vision"]["health"] == "unsupported" and nodes["vision"]["boot_id"] is None
        move = step("move_named_pose", pose="B")
        speak = step("speak", text="working")
        await system.start(move, speak)
        state = await eventually(system.status, lambda s: len(s["active_actions"]) == 2)
        wrs, tts = system.clients["wrs"], system.clients["tts"]
        before = await system.snapshot()
        assert (await system.replay("query"))["task"]["state"] == "RUNNING"
        assert (await system.snapshot()).control_epoch == before.control_epoch
        assert (await system.replay("barge_in"))["accepted"]
        await eventually(
            lambda: tts.status(state["active_actions"][speak.step_id]),
            lambda s: s.state == "CANCELLED",
        )
        assert (await system.snapshot()).active_action == before.active_action
        assert (await system.snapshot()).control_epoch == before.control_epoch
        assert (await system.replay("stop"))["accepted"]
        await eventually(
            lambda: wrs.status(state["active_actions"][move.step_id]),
            lambda s: s.state == "CANCELLED",
        )
        assert (await system.wait())["planner_calls"] == 0
        assert (await system.snapshot()).stop_confirmed


async def test_action_handle_dedup_status_and_tts_without_robot_controls():
    async with System.local(duration=0.3) as system:
        tts = system.clients["tts"]
        assert isinstance(tts, ActionClient) and not hasattr(tts, "hold")
        assert not (await tts.capabilities()).robot_controls
        assert "objects" not in (await tts.context()).model_dump()
        say = await system.action("speak", text="first")
        assert say.receipt.status.state == "ACCEPTED"
        assert (await tts.submit(say.request)).accepted
        assert (await say.wait()).state == "SUCCEEDED"
        health = await tts.transport.request("request/health", {})
        assert health["executions"] == 1
        for key in ("hold", "resume"):
            with pytest.raises(TimeoutError):
                await tts.transport.request(f"request/control/{key}", {}, control=True, timeout=0.1)
        cancelled = await system.action("speak", text="cancel me")
        context = await tts.context(control=True)
        stop = ControlRequest(
            interrupt_id=new_id(),
            boot_id=context.boot_id,
            control_epoch=context.control_epoch,
            action_id=cancelled.id,
        )
        first = await tts.cancel(stop)
        assert await tts.cancel(stop) == first
        assert (await cancelled.wait()).state == "CANCELLED"
        next_speech = await system.action("speak", text="new request")
        assert (await next_speech.wait()).state == "SUCCEEDED"
        assert next_speech.request.control_epoch > cancelled.request.control_epoch
        assert (await cancelled.status()).state == "CANCELLED"


async def test_direct_nodes_survive_agent_exit_and_registry_rejects_offline_provider():
    async with System.local(duration=2) as system:
        await system.nodes()
        motion = await system.action("move_named_pose", pose="B")
        speech = await system.action("speak", text="direct")
        # Only this test's owned Agent is terminated, proving there is no Agent hop.
        agent = system.stack.processes[3]
        agent.terminate()
        await asyncio.to_thread(agent.wait, timeout=3)
        assert (await system.nodes())["agent"]["health"] == "offline"
        assert (await system.replay("barge_in"))["accepted"]
        assert (await speech.wait()).state == "CANCELLED"
        assert (await motion.status()).state not in {"CANCELLED", "UNKNOWN"}
        assert (await system.replay("stop"))["accepted"]
        assert (await motion.wait()).state == "CANCELLED"
        tts = system.stack.processes[2]
        tts.terminate()
        await asyncio.to_thread(tts.wait, timeout=3)
        assert (await system.nodes())["tts"]["health"] == "offline"
        with pytest.raises(ValueError, match="provider_not_ready"):
            await system.action("speak", text="must not execute")


async def test_runtime_resolves_renamed_provider_from_configuration(tmp_path):
    text = DEFAULT.read_text(encoding="utf-8").replace("nodes.wrs", "nodes.wrs_lite6")
    text = text.replace('= "wrs"', '= "wrs_lite6"').replace('type = "wrs_lite6"', 'type = "wrs"')
    path = tmp_path / "nodes.toml"
    path.write_text(text, encoding="utf-8")
    async with LocalStack(bindings=path, duration=0.05, voice=True) as stack:
        system = System(stack)
        assert (await system.nodes())["wrs_lite6"]["ready"]
        await system.start(step("move_named_pose", pose="B"), step("speak", text="parallel"))
        assert (await system.wait())["state"] == "SUCCEEDED"
        assert (await system.snapshot()).pose == "B"
        result = await system.agent.request(
            "request/task/hold", {"request_id": new_id()}, control=True
        )
        assert result["accepted"]


async def test_public_action_recovers_lost_receipt_and_repeated_cancel(monkeypatch):
    async with System.local(duration=0.2) as system:
        tts = system.clients["tts"]
        submit = tts.submit

        async def lose_receipt(request):
            await submit(request)
            raise TimeoutError("injected response loss after real Zenoh acceptance")

        monkeypatch.setattr(tts, "submit", lose_receipt)
        action = await system.action("speak", text="one effect")
        assert (await action.wait()).state == "SUCCEEDED"
        assert (await tts.transport.request("request/health", {}))["executions"] == 1
        monkeypatch.setattr(tts, "submit", submit)
        cancelled = await system.action("speak", text="cancel")
        first = await cancelled.cancel()
        assert first.accepted
        assert (await cancelled.wait()).state == "CANCELLED"
        assert await cancelled.cancel() == first


async def test_runtime_hold_has_no_normal_capability_query(monkeypatch):
    from wrs_agent.runtime import Runtime
    from wrs_agent.schemas import TaskControl

    async with LocalStack(runtime=False, duration=1) as stack:
        from wrs_agent.bindings import load_nodes
        from wrs_agent.nodes.api import RobotClient
        from wrs_agent.registry import NodeRegistry

        definitions, bindings = load_nodes()
        nodes = {
            "wrs": RobotClient(stack.transport),
            "tts": ActionClient(stack.node_transports["tts"]),
        }
        registry = NodeRegistry(stack.node_transports, definitions, bindings)
        runtime = Runtime(nodes, bindings, registry=registry)

        async def unavailable_capabilities():
            raise AssertionError("control must not wait on ordinary capability queries")

        for node in nodes.values():
            monkeypatch.setattr(node, "capabilities", unavailable_capabilities)
        try:
            result = await runtime.hold(TaskControl(request_id=new_id()))
            assert result["accepted"] and result["phase"] == "STOPPED"
        finally:
            await runtime.close()


async def test_unready_provider_prevents_partial_task_effects():
    async with System.local(duration=0.1) as system:
        await system.replay("stop")
        await system.start(step("speak", text="must not start"), step("move_named_pose", pose="B"))
        assert (await system.wait())["state"] == "FAILED"
        for node in ("wrs", "tts"):
            health = await system.clients[node].transport.request("request/health", {})
            assert health["executions"] == 0
