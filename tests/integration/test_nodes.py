import asyncio
import subprocess

import pytest
from conftest import eventually

from wrs_agent.environments.api import ActionClient
from wrs_agent.processes import NO_WINDOW, ROOT, LocalStack, python_command
from wrs_agent.schemas import ActionRequest, Plan, Step, new_id
from wrs_agent.transport import RemoteError, Transport, decode

pytestmark = pytest.mark.zenoh


async def make_action(client, skill, args):
    world = await client.snapshot()
    return ActionRequest(
        action_id=new_id(),
        task_id="direct",
        task_revision=0,
        boot_id=world.boot_id,
        control_epoch=world.control_epoch,
        lease_id=world.lease_id,
        world_version=world.world_version,
        skill=skill,
        args=args,
    )


async def test_parallel_nodes_dependency_and_resources():
    async with LocalStack(duration=0.25) as stack:
        bus = stack.transport
        wrs = ActionClient(bus)
        tts = ActionClient(stack.node_transports["tts"])
        plan = Plan(
            steps=[
                Step(step_id="speech", skill="speak", args={"text": "working"}),
                Step(step_id="observe", skill="observe"),
                Step(step_id="pick", skill="pick", args={"object": "A"}, depends_on=["observe"]),
                Step(
                    step_id="place",
                    skill="place",
                    args={"object": "A", "target": "B"},
                    depends_on=["pick"],
                ),
                Step(
                    step_id="verify",
                    skill="verify",
                    args={"object": "A", "target": "B"},
                    depends_on=["place"],
                ),
            ]
        )
        request = {"request_id": new_id(), "plan": plan.model_dump()}
        first = await bus.request("request/task/start", request)
        assert first == await bus.request("request/task/start", request)

        async def concurrent():
            return await wrs.snapshot(), await tts.snapshot()

        both = await eventually(concurrent, lambda pair: all(w.active_action for w in pair))
        assert both[0].objects["A"] == "table"  # dependent pick has not run
        await eventually(
            lambda: bus.request("request/task/status", {}),
            lambda s: s["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN"},
        )
        result = await bus.request("request/task/status", {})
        assert result["state"] == "SUCCEEDED", result
        assert (await wrs.snapshot()).objects["A"] == "B"
        assert (await tts.snapshot()).facts["completed"] == 1
        assert (await bus.request("request/health", {}))["executions"] == 4
        assert result["planner_calls"] == 0  # progress does not invoke a model
    assert all(p.poll() is not None for p in stack.processes)


async def test_hung_model_direct_voice_cancel_and_stop():
    async with LocalStack(duration=3, voice=True, deferred=True) as stack:
        bus, voice = stack.transport, stack.node_transports["voice"]
        wrs = ActionClient(bus)
        tts = ActionClient(stack.node_transports["tts"])
        plan = Plan(
            steps=[
                Step(step_id="pick", skill="pick", args={"object": "A"}),
                Step(step_id="speech", skill="speak", args={"text": "long utterance"}),
            ]
        )
        await bus.request(
            "request/task/start",
            {
                "request_id": new_id(),
                "plan": plan.model_dump(),
            },
        )
        state = await eventually(
            lambda: bus.request("request/task/status", {}),
            lambda s: len(s["active_actions"]) == 2,
        )
        ids = state["active_actions"]
        await eventually(lambda: wrs.status(ids["pick"]), lambda s: s.state == "RUNNING")
        await eventually(lambda: tts.status(ids["speech"]), lambda s: s.state == "RUNNING")
        before = await wrs.snapshot()
        await bus.request("request/task/goal", {"request_id": new_id(), "goal": "next goal"})
        await eventually(
            lambda: bus.request("request/task/status", {}), lambda s: s["planner_calls"] == 1
        )
        for kind in ("query", "vad", "ack"):
            result = await voice.request(
                "request/voice/event", {"event_id": new_id(), "kind": kind}
            )
            assert result["disposition"] == ("answer" if kind == "query" else "ignore")
        assert (await wrs.snapshot()).control_epoch == before.control_epoch
        assert (await wrs.status(ids["pick"])).state == "RUNNING"

        interrupt = {"event_id": new_id(), "kind": "barge_in"}
        result = await voice.request("request/voice/control", interrupt, control=True, timeout=1)
        assert result["accepted"]
        assert result == await voice.request("request/voice/control", interrupt, control=True)
        await eventually(lambda: tts.status(ids["speech"]), lambda s: s.state == "CANCELLED")
        assert (await wrs.status(ids["pick"])).state == "RUNNING"
        assert (await wrs.snapshot()).control_epoch == before.control_epoch
        assert (await bus.request("request/task/status", {}))["planning"] == "WAITING"

        stop = {"event_id": new_id(), "kind": "stop"}
        stopped = await voice.request("request/voice/control", stop, control=True, timeout=1)
        assert stopped["accepted"] and stopped["phase"] == "STOPPING"
        await eventually(lambda: wrs.status(ids["pick"]), lambda s: s.state == "CANCELLED")
        assert (await wrs.snapshot()).stop_confirmed
        assert stopped == await voice.request("request/voice/control", stop, control=True)
        late = ActionRequest(
            action_id=new_id(),
            task_id="late",
            task_revision=0,
            boot_id=before.boot_id,
            control_epoch=before.control_epoch,
            lease_id=before.lease_id,
            world_version=before.world_version,
            skill="pick",
            args={"object": "A"},
        )
        assert (await wrs.submit(late)).reason == "stale_epoch"
        await bus.request("request/test/planner/release", {}, control=True)
        await eventually(
            lambda: bus.request("request/task/status", {}), lambda s: s["planning"] == "STALE"
        )
        assert (await bus.request("request/health", {}))["executions"] == 1
        assert (await wrs.snapshot()).admission == "HELD"


async def test_dedup_missed_terminal_and_authentication():
    async with LocalStack(runtime=False, duration=0.08) as stack:
        wrs = ActionClient(stack.transport)
        tts = ActionClient(stack.node_transports["tts"])
        for client, skill, args in [
            (wrs, "pick", {"object": "A"}),
            (tts, "speak", {"text": "hello"}),
        ]:
            action = await make_action(client, skill, args)
            receipt = await client.submit(action)
            assert receipt.accepted and receipt.status.state == "ACCEPTED"
            assert (await client.submit(action)).accepted
            # No subscription at all: status recovers missed progress and terminal events.
            result = await eventually(
                lambda client=client, action=action: client.status(action.action_id),
                lambda s: s.state == "SUCCEEDED",
            )
            assert result.verification == "PASS"
            assert (await client.submit(action)).status == result
            conflicting = action.model_copy(update={"task_revision": 1})
            assert (await client.submit(conflicting)).reason == "action_id_conflict"
            assert (await client.transport.request("request/health", {}))["executions"] == 1

        # Valid name/priority never substitutes for a deployment credential.
        forged = Transport(
            stack.endpoint, stack.site, stack.env_id, "wrong-token-value", "operator"
        )
        try:
            with pytest.raises(RemoteError, match="unauthorized"):
                await forged.request("request/control/snapshot", {}, control=True)
        finally:
            await forged.close()
        wrong_skill = await make_action(wrs, "speak", {"text": "not on robot"})
        assert not (await wrs.submit(wrong_skill)).accepted
        wrong_skill = await make_action(tts, "pick", {"object": "A"})
        assert not (await tts.submit(wrong_skill)).accepted


async def test_real_pubsub_timeout_cancel_callback_threads_and_duplicate_owner():
    async with LocalStack(runtime=False, tts=False) as stack:
        bus = stack.transport
        events = bus.subscribe("events/action")
        wrs = ActionClient(bus)
        action = await make_action(wrs, "pick", {"object": "A"})
        await wrs.submit(action)
        sample = await eventually(events.try_recv, lambda s: s is not None)
        assert decode(sample.payload.to_bytes())["action_id"] == action.action_id
        health = await bus.request("request/health", {})
        assert health["callbacks_off_loop"]
        with pytest.raises(TimeoutError):
            await bus.request("request/missing", {}, timeout=0.1)
        slow = stack._spawn(
            "slow-test",
            python_command(
                "tests/fixtures/slow_query.py", stack.endpoint, stack.site, stack.env_id
            ),
        )
        await stack._ready("request/test/slow_status")
        waiting = asyncio.create_task(bus.request("request/test/slow", {}, timeout=10))
        await eventually(
            lambda: bus.request("request/test/slow_status", {}, control=True),
            lambda state: state["entered"],
        )
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiting, 0.2)
        slow.terminate()
        await asyncio.to_thread(slow.wait, timeout=3)
        duplicate = await asyncio.to_thread(
            subprocess.run,
            stack.node_command("environment"),
            cwd=ROOT,
            capture_output=True,
            timeout=5,
            creationflags=NO_WINDOW,
        )
        assert duplicate.returncode != 0
        assert b"environment_already_running" in duplicate.stderr
        assert (await wrs.snapshot()).boot_id == action.boot_id


async def test_query_reconnect_without_resubmitting_actions():
    async with LocalStack(runtime=False, tts=False, duration=0.03) as stack:
        client = ActionClient(stack.transport)
        action = await make_action(client, "pick", {"object": "A"})
        await client.submit(action)
        await eventually(
            lambda client=client, action=action: client.status(action.action_id),
            lambda s: s.state == "SUCCEEDED",
        )
        router = stack.processes.pop(0)
        router.terminate()
        await asyncio.to_thread(router.wait, timeout=3)
        replacement = await asyncio.to_thread(stack.start_router)
        stack.processes.remove(replacement)
        stack.processes.insert(0, replacement)
        await stack._ready("request/capabilities")
        assert (await client.status(action.action_id)).state == "SUCCEEDED"
        assert (await stack.transport.request("request/health", {}))["executions"] == 1


async def test_cancel_tts_allows_robot_branch_to_finish_and_queue_runs_after_success():
    async with LocalStack(duration=0.2, voice=True) as stack:
        bus = stack.transport
        wrs = ActionClient(bus)
        tts = ActionClient(stack.node_transports["tts"])
        plan = Plan(
            steps=[
                Step(step_id="speech", skill="speak", args={"text": "cancel this"}),
                Step(step_id="pick", skill="pick", args={"object": "A"}),
                Step(
                    step_id="place",
                    skill="place",
                    args={"object": "A", "target": "B"},
                    depends_on=["pick"],
                ),
            ]
        )
        await bus.request("request/task/start", {"request_id": new_id(), "plan": plan.model_dump()})
        status = await eventually(
            lambda: bus.request("request/task/status", {}),
            lambda s: "speech" in s["active_actions"] and "pick" in s["active_actions"],
        )
        aid = status["active_actions"]["speech"]
        old_epoch = (await wrs.snapshot()).control_epoch
        await stack.node_transports["voice"].request(
            "request/voice/control",
            {
                "event_id": new_id(),
                "kind": "barge_in",
            },
            control=True,
        )
        await eventually(lambda: tts.status(aid), lambda s: s.state == "CANCELLED")
        await eventually(
            lambda: bus.request("request/task/status", {}),
            lambda s: s["state"] == "CANCELLED",
        )
        assert (await wrs.snapshot()).objects["A"] == "B"
        assert (await wrs.snapshot()).control_epoch == old_epoch

        # Only the cancelled resource remains held. A robot-only new task works.
        first = Plan(steps=[Step(step_id="observe", skill="observe")])
        await bus.request(
            "request/task/start", {"request_id": new_id(), "plan": first.model_dump()}
        )
        appended = Plan(steps=[Step(step_id="pick_D", skill="pick", args={"object": "D"})])
        queued = await bus.request(
            "request/task/enqueue",
            {
                "request_id": new_id(),
                "plan": appended.model_dump(),
            },
        )
        assert queued["queued"] == 1
        await eventually(
            lambda: bus.request("request/task/status", {}),
            lambda s: s["state"] == "SUCCEEDED" and s["steps"].get("pick_D") == "SUCCEEDED",
        )
        assert (await wrs.snapshot()).held_object == "D"
