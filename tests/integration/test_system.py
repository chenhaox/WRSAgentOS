import asyncio

import pytest

from tests.conftest import eventually
from wrs_agent import System, step
from wrs_agent.bindings import DEFAULT
from wrs_agent.nodes.actions import ActionClient
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
        assert type(tts) is type(system.clients["wrs"]) is ActionClient
        assert not hasattr(tts, "hold")
        assert not (await tts.capabilities()).robot_controls
        context = await tts.context()
        assert context.data.kind == "speech"
        assert set(context.data.model_dump()) == {"kind", "completed", "last_text"}
        observed = await system.snapshot("tts")
        assert observed.node_id == "tts" and observed.boot_id == context.boot_id
        assert observed.captured_at_ns >= context.captured_at_ns
        assert "lease_id" not in observed.model_dump()
        say = await system.action("speak", text="first")
        assert say.receipt.status.state == "ACCEPTED"
        assert (await tts.submit(say.request)).accepted
        assert (await say.wait()).state == "SUCCEEDED"
        spoken = await system.snapshot("tts")
        assert spoken.data.completed == 1 and spoken.data.last_text == "first"
        assert spoken.world_version > observed.world_version
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
        agent = system._local_stack.processes[3]
        agent.terminate()
        await asyncio.to_thread(agent.wait, timeout=3)
        await eventually(
            system.registry.snapshot, lambda view: view["agent"]["health"] == "offline"
        )
        assert (await system.nodes())["agent"]["health"] == "offline"
        assert (await system.replay("barge_in"))["accepted"]
        assert (await speech.wait()).state == "CANCELLED"
        assert (await motion.status()).state not in {"CANCELLED", "UNKNOWN"}
        assert (await system.replay("stop"))["accepted"]
        assert (await motion.wait()).state == "CANCELLED"
        tts = system._local_stack.processes[2]
        tts.terminate()
        await asyncio.to_thread(tts.wait, timeout=3)
        await eventually(system.registry.snapshot, lambda view: view["tts"]["health"] == "offline")
        assert (await system.nodes())["tts"]["health"] == "offline"
        with pytest.raises(ValueError, match="node_not_ready"):
            await system.action("speak", text="must not execute")


async def test_runtime_resolves_renamed_node_from_configuration(tmp_path):
    text = DEFAULT.read_text(encoding="utf-8").replace("nodes.wrs", "nodes.wrs_lite6")
    text = text.replace('= "wrs"', '= "wrs_lite6"').replace('type = "wrs_lite6"', 'type = "wrs"')
    path = tmp_path / "nodes.toml"
    path.write_text(text, encoding="utf-8")
    async with LocalStack(bindings=path, duration=0.05) as stack:
        path.write_text("not valid TOML", encoding="utf-8")
        system = stack.system
        assert (await system.nodes())["wrs_lite6"]["ready"]
        await system.start(step("move_named_pose", pose="B"), step("speak", text="parallel"))
        assert (await system.wait())["state"] == "SUCCEEDED"
        world = await system.snapshot()
        assert world.data.pose == "B" and world.node_id == "wrs_lite6"
        task = await system.start(step("move_named_pose", pose="C"))
        result = await system.agent.request(
            "request/task/hold", {"request_id": new_id(), "task_id": task["task_id"]}, control=True
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
    from wrs_agent.schemas import Plan, TaskControl, TaskRequest

    async with LocalStack(bindings="tests/fixtures/actions.toml", duration=1) as stack:
        nodes = stack.system.clients
        bindings = stack.system.bindings
        registry = stack.system.registry
        runtime = Runtime(nodes, bindings, registry=registry)

        async def unavailable_capabilities():
            raise AssertionError("control must not wait on ordinary capability queries")

        for node in nodes.values():
            monkeypatch.setattr(node, "capabilities", unavailable_capabilities)
        try:
            task = await runtime.start(
                TaskRequest(
                    request_id=new_id(), plan=Plan(steps=[step("move_named_pose", pose="B")])
                )
            )
            result = await runtime.hold(TaskControl(request_id=new_id(), task_id=task["task_id"]))
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


async def test_same_async_client_can_cancel_while_waiting_and_planner_is_pending():
    async with LocalStack(deferred=True, duration=2) as stack:
        system = stack.system
        await system.goal("put A in B")
        await eventually(system.status, lambda state: state["planning"] == "WAITING")
        motion = await system.action("move_named_pose", pose="B")
        speech = await system.action("speak", text="working")
        waiting = asyncio.create_task(speech.wait())
        try:
            await asyncio.sleep(0)
            assert not waiting.done()
            assert (await speech.cancel()).accepted
            assert (await waiting).state == "CANCELLED"
            assert (await motion.status()).state in {"ACCEPTED", "RUNNING"}
            assert (await system.status())["planning"] == "WAITING"
            assert (await motion.cancel()).accepted
            assert (await motion.wait()).state == "CANCELLED"
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)


async def test_configuration_cannot_grant_an_unregistered_node_skill(tmp_path):
    bindings = tmp_path / "bindings.toml"
    source = DEFAULT.read_text(encoding="utf-8")
    assert 'pick = "wrs"' in source
    bindings.write_text(source.replace('pick = "wrs"', 'pick = "tts"'), encoding="utf-8")
    async with System.local(bindings=bindings, duration=0.02) as system:
        nodes = await system.nodes()
        assert nodes["tts"]["skills"] == ["speak"]
        assert "pick" not in {skill.name for skill in await system.skills("pick")}
        with pytest.raises(ValueError, match="unsupported_skill"):
            await system.action("pick", object="A")
        health = await system.clients["tts"].transport.request("request/health", {})
        assert health["executions"] == 0
        with pytest.raises(ValueError, match="node_snapshot_unsupported"):
            await system.snapshot("agent")
