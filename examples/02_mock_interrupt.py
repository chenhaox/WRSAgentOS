"""Parallel WRS/TTS, hung mock planner, scoped interruption, then A->C."""

import asyncio
import json

from wrs_agent.environments.api import ActionClient
from wrs_agent.processes import LocalStack
from wrs_agent.schemas import ActionRequest, Plan, Step, new_id


async def wait_for(call, predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while True:
            value = await call()
            if predicate(value):
                return value
            await asyncio.sleep(0.01)


async def main():
    async with LocalStack(duration=0.8, voice=True, deferred=True) as stack:
        bus = stack.transport
        voice = stack.node_transports["voice"]
        env = ActionClient(bus)
        tts = ActionClient(stack.node_transports["tts"])
        plan = Plan(
            steps=[
                Step(step_id="speech", skill="speak", args={"text": "Moving A to B"}),
                Step(step_id="pick", skill="pick", args={"object": "A"}),
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
        await bus.request("request/task/start", {"request_id": new_id(), "plan": plan.model_dump()})
        running = await wait_for(
            lambda: bus.request("request/task/status", {}),
            lambda s: len(s["active_actions"]) == 2,
        )
        speech_id = running["active_actions"]["speech"]
        await wait_for(lambda: tts.status(speech_id), lambda s: s.state == "RUNNING")
        await bus.request("request/task/goal", {"request_id": new_id(), "goal": "next task"})
        await wait_for(
            lambda: bus.request("request/task/status", {}), lambda s: s["planner_calls"] == 1
        )
        await voice.request(
            "request/voice/control",
            {
                "event_id": new_id(),
                "kind": "barge_in",
            },
            control=True,
        )
        await wait_for(lambda: tts.status(speech_id), lambda s: s.state == "CANCELLED")
        world = await wait_for(
            env.snapshot, lambda w: w.held_object == "A" and w.active_action is not None
        )
        query = await voice.request("request/voice/event", {"event_id": new_id(), "kind": "query"})
        assert query["task"]["state"] == "RUNNING"
        assert (await env.snapshot()).control_epoch == world.control_epoch
        held = await bus.request("request/task/hold", {"request_id": new_id()}, control=True)
        assert held["accepted"]
        stopped = await wait_for(env.snapshot, lambda w: w.stop_confirmed)
        assert stopped.held_object == "A"
        stale = ActionRequest(
            action_id=new_id(),
            task_id=running["task_id"],
            task_revision=running["revision"],
            boot_id=world.boot_id,
            control_epoch=world.control_epoch,
            lease_id=world.lease_id,
            world_version=world.world_version,
            skill="place",
            args={"object": "A", "target": "B"},
        )
        rejected = await env.submit(stale)
        assert not rejected.accepted and rejected.reason == "stale_epoch"
        await bus.request("request/test/planner/release", {}, control=True)
        await wait_for(
            lambda: bus.request("request/task/status", {}), lambda s: s["planning"] == "STALE"
        )
        remaining = Plan(
            steps=[
                Step(step_id="place", skill="place", args={"object": "A", "target": "C"}),
                Step(
                    step_id="verify",
                    skill="verify",
                    args={"object": "A", "target": "C"},
                    depends_on=["place"],
                ),
            ]
        )
        await bus.request(
            "request/task/replace",
            {
                "request_id": new_id(),
                "replacement": remaining.model_dump(),
            },
            control=True,
        )
        final = await wait_for(
            lambda: bus.request("request/task/status", {}),
            lambda s: s["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN"},
        )
        state = await env.snapshot()
        assert final["state"] == "SUCCEEDED", final
        assert state.objects["A"] == "C" and state.held_object is None
        print(
            json.dumps(
                {
                    "profile": "real_zenoh_mock_nodes",
                    "parallel_nodes": ["wrs", "tts"],
                    "model": "deferred_mock_no_API_call",
                    "tts_cancelled_only": True,
                    "query_during_motion": True,
                    "held_after_stop": stopped.held_object,
                    "old_action_rejected": rejected.reason,
                    "late_model": final["planning"],
                    "final_task": final["state"],
                    "final_A_location": state.objects["A"],
                    "verification": "virtual_state",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
