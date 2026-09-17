"""Headless real WRS model motion through Zenoh; no hardware or contact claim."""

import argparse
import asyncio
import json

from wrs_agent.environments.api import ActionClient
from wrs_agent.processes import LocalStack
from wrs_agent.schemas import ActionRequest, ControlRequest, Plan, Step, new_id


async def run(cancel):
    async with LocalStack(backend="wrs_virtual", duration=1.0) as stack:
        node = ActionClient(stack.transport)
        print(json.dumps((await node.capabilities()).model_dump()))
        world = await node.snapshot()
        request = ActionRequest(
            action_id=new_id(),
            task_id=new_id(),
            task_revision=0,
            boot_id=world.boot_id,
            control_epoch=world.control_epoch,
            lease_id=world.lease_id,
            world_version=world.world_version,
            skill="move_named_pose",
            args={"pose": "B"},
        )
        print("receipt", (await node.submit(request)).model_dump())
        cancelled = False
        async with asyncio.timeout(5):
            while True:
                status = await node.status(request.action_id)
                print("status", status.model_dump())
                if cancel and status.progress > 0.1 and not cancelled:
                    world = await node.snapshot(control=True)
                    print(
                        "cancel",
                        (
                            await node.cancel(
                                ControlRequest(
                                    interrupt_id=new_id(),
                                    boot_id=world.boot_id,
                                    control_epoch=world.control_epoch,
                                    action_id=request.action_id,
                                )
                            )
                        ).model_dump(),
                    )
                    cancelled = True
                if status.state in {"SUCCEEDED", "CANCELLED", "UNKNOWN", "FAILED"}:
                    break
                await asyncio.sleep(0.1)
        world = await node.snapshot()
        print("snapshot", json.dumps(world.model_dump()))
        assert status.state == ("CANCELLED" if cancel else "SUCCEEDED")
        if cancelled:
            assert world.stop_confirmed
            assert (
                await node.resume(
                    ControlRequest(
                        interrupt_id=new_id(),
                        boot_id=world.boot_id,
                        control_epoch=world.control_epoch,
                        world_version=world.world_version,
                    )
                )
            ).accepted
        plan = Plan(steps=[Step(step_id="home", skill="move_named_pose", args={"pose": "home"})])
        await stack.transport.request(
            "request/task/start", {"request_id": new_id(), "plan": plan.model_dump()}
        )
        async with asyncio.timeout(5):
            while True:
                task = await stack.transport.request("request/task/status", {})
                if task["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
                    break
                await asyncio.sleep(0.05)
        assert task["state"] == "SUCCEEDED"
        print(
            json.dumps(
                {
                    "profile": "wrs_virtual_fk",
                    "runtime": task["state"],
                    "hardware": False,
                    "pick_place": "unsupported",
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cancel", action="store_true")
    asyncio.run(run(parser.parse_args().cancel))
