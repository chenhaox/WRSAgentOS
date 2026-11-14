import asyncio
import sys

import pytest
from conftest import eventually, submit_request

from wrs_agent import ActionState
from wrs_agent.processes import LocalStack
from wrs_agent.schemas import ActionRequest, ControlRequest, Plan, Step, decode, new_id

pytestmark = [pytest.mark.zenoh, pytest.mark.wrs]


async def request_for(node, *, pose="B", revision=0, skill="move_named_pose"):
    world = await node.context()
    return ActionRequest(
        action_id=new_id(),
        task_id="wrs-task",
        task_revision=revision,
        boot_id=world.boot_id,
        control_epoch=world.control_epoch,
        lease_id=world.lease_id,
        state_version=world.state_version,
        skill=skill,
        args={"pose": pose} if skill == "move_named_pose" else {"object": "A"},
    )


async def test_real_wrs_progress_query_completion_and_unsupported():
    async with LocalStack(backend="wrs", duration=0.5) as stack:
        node = stack.system.clients["wrs"]
        cap = await node.features()
        assert cap.backend == "wrs" and cap.verification == "wrs_fk"
        assert not cap.hardware and not cap.controller_flush
        # No scene: nothing to grasp, so pick/place_on stay unsupported.
        unbound = {"pick", "place", "reorient", "place_on", "seat_in", "set_control", "pour",
                   "stir", "scoop", "shake_off"}
        assert unbound <= set(cap.unsupported) and not unbound & set(cap.skills)
        assert not (
            await submit_request(node, await request_for(node, skill="shake_off"))
        ).accepted
        request = await request_for(node, revision=2)
        subscriber = stack.system.clients["wrs"].transport.subscribe("events/action", capacity=64)
        receipt = await submit_request(node, request)
        assert receipt.accepted and receipt.status.state is ActionState.ACCEPTED
        await eventually(lambda: node.status(request.action_id), lambda s: s.progress > 0)
        during = await node.snapshot()
        assert during.active_action == request.action_id
        assert during.data.robot.kinematics.source == "wrs_fk"
        assert during.data.robot.kinematics.valid
        assert during.data.robot.kinematics.joint_unit == "rad"
        assert (await submit_request(node, request)).accepted
        result = await eventually(
            lambda: node.status(request.action_id), lambda s: s.state == "SUCCEEDED"
        )
        assert result.state is ActionState.SUCCEEDED and result.verification == "PASS"
        final = await node.snapshot()
        assert final.data.robot.pose == "B"
        assert final.data.robot.kinematics.qs == pytest.approx([0.3, -1.2, 1.8, -2.2, -1.57, 0.0])
        events = []
        while (sample := subscriber.try_recv()) is not None:
            events.append(decode(sample.payload.to_bytes()))
        assert any(e["state"] == "RUNNING" for e in events)
        # Status remains available regardless of whether terminal event was received.
        assert (await node.status(request.action_id)).state == "SUCCEEDED"
        assert (await stack.system.clients["wrs"].transport.request("request/health", {}))[
            "executions"
        ] == 1
        stale = await submit_request(node, await request_for(node, revision=1))
        assert not stale.accepted and stale.reason == "stale_revision"
        assert "wrs" not in sys.modules
    assert all(p.poll() is not None for p in stack.processes)


@pytest.mark.parametrize("kind", ["cancel", "hold"])
async def test_real_wrs_cancel_hold_allow_actions_and_old_epoch(kind):
    async with LocalStack(
        backend="wrs", duration=1, bindings="tests/fixtures/actions.toml"
    ) as stack:
        node = stack.system.clients["wrs"]
        request = await request_for(node)
        await submit_request(node, request)
        await eventually(lambda: node.status(request.action_id), lambda s: s.progress > 0)
        old = await request_for(node, pose="C")
        world = await node.snapshot()
        interrupt = ControlRequest(
            interrupt_id=new_id(),
            boot_id=world.boot_id,
            control_epoch=world.control_epoch,
            action_id=request.action_id if kind == "cancel" else None,
        )
        receipt = await node.control(kind, interrupt)
        assert receipt.accepted and receipt.phase in {"STOPPING", "STOPPED"}
        assert await node.control(kind, interrupt) == receipt
        await eventually(lambda: node.snapshot(), lambda s: s.stop_confirmed)
        assert (await node.status(request.action_id)).state == "CANCELLED"
        stopped = await node.snapshot()
        await asyncio.sleep(0.08)
        assert (await node.snapshot()).data.robot.kinematics.qs == stopped.data.robot.kinematics.qs
        assert not (await submit_request(node, old)).accepted
        allow_actions = await node.control(
            "allow_actions",
            ControlRequest(
                interrupt_id=new_id(),
                boot_id=stopped.boot_id,
                control_epoch=stopped.control_epoch,
                state_version=stopped.state_version,
            ),
        )
        assert allow_actions.accepted and (await node.snapshot()).active_action is None
        fresh = await request_for(node, pose="C")
        assert (await submit_request(node, fresh)).accepted
        await eventually(lambda: node.status(fresh.action_id), lambda s: s.state == "SUCCEEDED")


async def test_runtime_schedules_real_wrs_node():
    async with LocalStack(backend="wrs", duration=0.1) as stack:
        plan = Plan(steps=[Step(step_id="home", skill="move_named_pose", args={"pose": "home"})])
        await stack.system.clients["wrs"].transport.request(
            "request/task/start", {"request_id": new_id(), "plan": plan.model_dump()}
        )
        done = await eventually(
            lambda: stack.system.clients["wrs"].transport.request("request/task/status", {}),
            lambda s: s["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN"},
        )
        assert done["state"] == "SUCCEEDED"
        assert (await stack.system.clients["wrs"].features()).backend == "wrs"


async def test_unsupported_wrs_step_prevents_partial_tts_side_effect():
    async with LocalStack(backend="wrs", duration=0.1) as stack:
        plan = Plan(
            steps=[
                Step(step_id="say", skill="speak", args={"text": "starting"}),
                Step(step_id="drain", skill="shake_off", args={"object": "A"}),
            ]
        )
        await stack.system.clients["wrs"].transport.request(
            "request/task/start", {"request_id": new_id(), "plan": plan.model_dump()}
        )
        await eventually(
            lambda: stack.system.clients["wrs"].transport.request("request/task/status", {}),
            lambda s: s["state"] == "FAILED",
        )
        assert set(stack.system.clients) == {"wrs", "tts"}
        for client in stack.system.clients.values():
            assert (await client.transport.request("request/health", {}))["executions"] == 0


async def test_gripper_open_close_is_read_back_and_cancellable():
    async with LocalStack(backend="wrs", duration=0.4) as stack:
        system = stack.system
        before = (await system.snapshot()).data.robot
        assert before.kinematics.gripper_width == pytest.approx(0.05)
        closing = await system.action("set_gripper", command="close")
        result = await closing.wait()
        assert result.state == "SUCCEEDED" and result.verification == "PASS"
        closed = (await system.snapshot()).data.robot
        assert closed.kinematics.gripper_width == pytest.approx(0.0, abs=1e-6)
        # Jaw motion leaves the arm, its named pose and any held object untouched.
        assert closed.kinematics.qs == before.kinematics.qs
        assert closed.pose == before.pose == "home" and closed.held_object is None

        opening = await system.action("set_gripper", command="open")
        await eventually(opening.status, lambda s: s.progress > 0)
        assert (await opening.cancel()).accepted
        assert (await opening.wait()).state == "CANCELLED"
        await eventually(system.snapshot, lambda s: s.stop_confirmed)
        stopped = (await system.snapshot()).data.robot.kinematics
        assert 0.0 < stopped.gripper_width < 0.05
        await asyncio.sleep(0.2)
        assert (await system.snapshot()).data.robot.kinematics == stopped


async def test_pick_and_place_on_move_the_named_box_and_track_holding():
    from wrs_agent.processes import ROOT

    async with LocalStack(
        backend="wrs", duration=0.2, scene=ROOT / "examples/wrs/scene.toml"
    ) as stack:
        system = stack.system
        cap = await system.clients["wrs"].features()
        assert {"pick", "place_on"} <= set(cap.skills)
        assert not {"pick", "place_on"} & set(cap.unsupported) and "place" in cap.unsupported
        start = (await system.snapshot()).data.objects["A"].pos
        assert (await (await system.action("place_on", object="A", surface="plate")).wait()
                ).reason == "object_not_held"
        too_wide = await (await system.action("pick", object="plate")).wait()
        assert too_wide.state == "FAILED" and too_wide.reason == "object_exceeds_dh50_opening"

        picked = await (await system.action("pick", object="A")).wait(timeout=20)
        assert picked.state == "SUCCEEDED" and picked.verification == "PASS"
        scene = (await system.snapshot()).data
        assert scene.robot.held_object == "A" and scene.robot.pose is None
        assert scene.objects["A"].pos[2] == pytest.approx(start[2] + 0.08, abs=0.005)
        assert scene.robot.kinematics.gripper_width < 0.05
        jaw = await (await system.action("set_gripper", command="open")).wait()
        assert jaw.state == "FAILED" and jaw.reason == "object_held"
        carried = await (await system.action("move_relative", dz=0.02)).wait()
        assert carried.state == "SUCCEEDED"
        lifted = (await system.snapshot()).data.objects["A"].pos[2]
        assert lifted == pytest.approx(scene.objects["A"].pos[2] + 0.02, abs=1e-3)

        placed = await (await system.action("place_on", object="A", surface="plate")).wait(
            timeout=20
        )
        assert placed.state == "SUCCEEDED" and placed.verification == "PASS"
        scene = (await system.snapshot()).data
        plate = scene.objects["plate"]
        assert scene.robot.held_object is None
        assert scene.robot.kinematics.gripper_width == pytest.approx(0.05)
        # Resting 1 mm above the plate top, centered on it.
        assert scene.objects["A"].pos == pytest.approx(
            [plate.pos[0], plate.pos[1], plate.pos[2] + 0.005 + 0.001 + 0.02], abs=1e-6
        )
        repeat = await (await system.action("place_on", object="A", surface="table")).wait()
        assert repeat.reason == "object_not_held"

        # The next pick starts from the plate, not from the configured position.
        on_plate = scene.objects["A"].pos
        again = await (await system.action("pick", object="A")).wait(timeout=20)
        assert again.state == "SUCCEEDED"
        lifted = (await system.snapshot()).data.objects["A"].pos
        assert lifted == pytest.approx([on_plate[0], on_plate[1], on_plate[2] + 0.08], abs=1e-3)
        back = await (await system.action("place_on", object="A", surface="table")).wait(
            timeout=20
        )
        assert back.state == "SUCCEEDED"
        table = scene.objects["table"]
        assert (await system.snapshot()).data.objects["A"].pos == pytest.approx(
            [table.pos[0], table.pos[1], table.pos[2] + 0.03 + 0.001 + 0.02], abs=1e-6
        )


async def test_cancelled_pick_stops_with_consistent_holding_state():
    from wrs_agent.processes import ROOT

    async with LocalStack(
        backend="wrs", duration=0.2, scene=ROOT / "examples/wrs/scene.toml"
    ) as stack:
        system = stack.system
        start = (await system.snapshot()).data.objects["A"].pos
        picking = await system.action("pick", object="A")
        await eventually(picking.status, lambda s: s.progress > 0.9, timeout=20)
        assert (await picking.cancel()).accepted
        assert (await picking.wait()).state == "CANCELLED"
        stopped = await eventually(system.snapshot, lambda s: s.stop_confirmed)
        await asyncio.sleep(0.2)
        after = await system.snapshot()
        assert after.data.robot.kinematics == stopped.data.robot.kinematics
        assert after.data.objects["A"] == stopped.data.objects["A"]
        # Cancelled during the lift: the box is held and has left the table.
        assert after.data.robot.held_object == "A"
        assert after.data.objects["A"].pos[2] > start[2]


async def test_relative_motion_uses_actual_ik_fk_and_stops_at_task_boundary():
    from wrs_agent import TaskState, step

    async with LocalStack(backend="wrs", duration=0.2) as stack:
        system = stack.system
        ready = await system.action("move_named_pose", pose="B")
        assert (await ready.wait()).state == "SUCCEEDED"
        original = (await system.snapshot()).data.robot.kinematics.tcp_pos
        for delta in ({"dz": 0.02}, {"dz": -0.02}, {"dy": 0.02}, {"dy": -0.02}):
            before_state = (await system.snapshot()).data.robot.kinematics
            before = before_state.tcp_pos
            movement = await system.action("move_relative", **delta)
            assert (await movement.wait()).state == "SUCCEEDED"
            snapshot = await system.snapshot()
            expected = [
                value + delta.get(axis, 0)
                for value, axis in zip(before, ("dx", "dy", "dz"), strict=True)
            ]
            assert snapshot.data.robot.kinematics.tcp_pos == pytest.approx(expected, abs=1e-4)
            assert snapshot.data.robot.kinematics.tcp_name == "flange"
            for row, old_row in zip(
                snapshot.data.robot.kinematics.tcp_rotmat, before_state.tcp_rotmat, strict=True
            ):
                assert row == pytest.approx(old_row, abs=1e-4)
            assert snapshot.data.robot.pose is None
        assert (await system.snapshot()).data.robot.kinematics.tcp_pos == pytest.approx(original)

        moving = step("move_relative", dz=0.02)
        dependent = step("move_relative", dz=-0.02, after=moving)
        task = await system.start(moving, dependent)
        await eventually(task.status, lambda s: bool(s.active_actions))
        assert (await task.cancel()).accepted
        assert (await task.wait()).state is TaskState.CANCELLED
        stopped = await system.snapshot()
        await asyncio.sleep(0.3)
        assert (await system.snapshot()).data.robot.kinematics == stopped.data.robot.kinematics
        assert stopped.stop_confirmed
        fresh = await system.start(step("move_named_pose", pose="B"))
        assert (await fresh.wait()).state is TaskState.SUCCEEDED
