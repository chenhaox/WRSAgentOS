import asyncio
import sys

import pytest
from conftest import eventually

from wrs_agent.environments.api import ActionClient
from wrs_agent.processes import LocalStack
from wrs_agent.schemas import ActionRequest, ControlRequest, Plan, Step, decode, new_id

pytestmark = [pytest.mark.zenoh, pytest.mark.wrs]


async def request_for(node, *, pose="B", revision=0, skill="move_named_pose"):
    world = await node.snapshot()
    return ActionRequest(
        action_id=new_id(),
        task_id="wrs-task",
        task_revision=revision,
        boot_id=world.boot_id,
        control_epoch=world.control_epoch,
        lease_id=world.lease_id,
        world_version=world.world_version,
        skill=skill,
        args={"pose": pose} if skill == "move_named_pose" else {"object": "A"},
    )


async def test_real_wrs_progress_query_completion_and_unsupported():
    async with LocalStack(backend="wrs_virtual", duration=0.5) as stack:
        node = ActionClient(stack.transport)
        cap = await node.capabilities()
        assert cap.backend == "wrs_virtual" and cap.verification == "wrs_fk"
        assert not cap.hardware and not cap.controller_flush
        assert "pick" in cap.unsupported and "pick" not in cap.skills
        assert not (await node.submit(await request_for(node, skill="pick"))).accepted
        request = await request_for(node, revision=2)
        subscriber = stack.transport.subscribe("events/action", capacity=64)
        receipt = await node.submit(request)
        assert receipt.accepted and receipt.status.state == "ACCEPTED"
        await eventually(lambda: node.status(request.action_id), lambda s: s.progress > 0)
        during = await node.snapshot()
        assert during.active_action == request.action_id
        assert during.kinematics.source == "wrs_fk" and during.kinematics.valid
        assert during.kinematics.joint_unit == "rad"
        assert (await node.submit(request)).accepted
        result = await eventually(
            lambda: node.status(request.action_id), lambda s: s.state == "SUCCEEDED"
        )
        assert result.verification == "PASS"
        final = await node.snapshot()
        assert final.pose == "B"
        assert final.kinematics.joints == pytest.approx([0.3, 0.2, 0.5, 0.0, 0.2, 0.0])
        events = []
        while (sample := subscriber.try_recv()) is not None:
            events.append(decode(sample.payload.to_bytes()))
        assert any(e["state"] == "RUNNING" for e in events)
        # Status remains available regardless of whether terminal event was received.
        assert (await node.status(request.action_id)).state == "SUCCEEDED"
        assert (await stack.transport.request("request/health", {}))["executions"] == 1
        stale = await node.submit(await request_for(node, revision=1))
        assert not stale.accepted and stale.reason == "stale_revision"
        assert "wrs" not in sys.modules
    assert all(p.poll() is not None for p in stack.processes)


@pytest.mark.parametrize("kind", ["cancel", "hold"])
async def test_real_wrs_cancel_hold_resume_and_old_epoch(kind):
    async with LocalStack(backend="wrs_virtual", duration=1, runtime=False) as stack:
        node = ActionClient(stack.transport)
        request = await request_for(node)
        await node.submit(request)
        await eventually(lambda: node.status(request.action_id), lambda s: s.progress > 0)
        old = await request_for(node, pose="C")
        world = await node.snapshot()
        interrupt = ControlRequest(
            interrupt_id=new_id(),
            boot_id=world.boot_id,
            control_epoch=world.control_epoch,
            action_id=request.action_id if kind == "cancel" else None,
        )
        receipt = await getattr(node, kind)(interrupt)
        assert receipt.accepted and receipt.phase in {"STOPPING", "STOPPED"}
        assert await getattr(node, kind)(interrupt) == receipt
        await eventually(lambda: node.snapshot(), lambda s: s.stop_confirmed)
        assert (await node.status(request.action_id)).state == "CANCELLED"
        stopped = await node.snapshot()
        await asyncio.sleep(0.08)
        assert (await node.snapshot()).kinematics.joints == stopped.kinematics.joints
        assert not (await node.submit(old)).accepted
        resume = await node.resume(
            ControlRequest(
                interrupt_id=new_id(),
                boot_id=stopped.boot_id,
                control_epoch=stopped.control_epoch,
                world_version=stopped.world_version,
            )
        )
        assert resume.accepted and (await node.snapshot()).active_action is None
        fresh = await request_for(node, pose="C")
        assert (await node.submit(fresh)).accepted
        await eventually(lambda: node.status(fresh.action_id), lambda s: s.state == "SUCCEEDED")


async def test_runtime_schedules_real_wrs_node():
    async with LocalStack(backend="wrs_virtual", duration=0.1) as stack:
        plan = Plan(steps=[Step(step_id="home", skill="move_named_pose", args={"pose": "home"})])
        await stack.transport.request(
            "request/task/start", {"request_id": new_id(), "plan": plan.model_dump()}
        )
        done = await eventually(
            lambda: stack.transport.request("request/task/status", {}),
            lambda s: s["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN"},
        )
        assert done["state"] == "SUCCEEDED"
        assert (await ActionClient(stack.transport).capabilities()).backend == "wrs_virtual"


async def test_unsupported_wrs_step_prevents_partial_tts_side_effect():
    async with LocalStack(backend="wrs_virtual", duration=0.1) as stack:
        plan = Plan(
            steps=[
                Step(step_id="say", skill="speak", args={"text": "starting"}),
                Step(step_id="pick", skill="pick", args={"object": "A"}),
            ]
        )
        await stack.transport.request(
            "request/task/start", {"request_id": new_id(), "plan": plan.model_dump()}
        )
        await eventually(
            lambda: stack.transport.request("request/task/status", {}),
            lambda s: s["state"] == "FAILED",
        )
        for bus in stack.node_transports.values():
            assert (await bus.request("request/health", {}))["executions"] == 0
