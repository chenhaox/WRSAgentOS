"""Official router, a separate capability process and two event subscribers."""

import asyncio
import json

from wrs_agent.environments.api import ActionClient
from wrs_agent.processes import LocalStack
from wrs_agent.schemas import ActionRequest, new_id
from wrs_agent.transport import decode


async def main():
    async with LocalStack(runtime=False, tts=False) as stack:
        bus = stack.transport
        subscribers = [bus.subscribe("events/action", capacity=8) for _ in range(2)]
        client = ActionClient(bus)
        world = await client.snapshot()
        request = ActionRequest(
            action_id=new_id(),
            task_id="roundtrip",
            task_revision=0,
            boot_id=world.boot_id,
            control_epoch=world.control_epoch,
            lease_id=world.lease_id,
            world_version=world.world_version,
            skill="observe",
        )
        assert (await client.submit(request)).accepted
        for subscriber in subscribers:
            async with asyncio.timeout(2):
                while True:
                    sample = subscriber.try_recv()
                    if sample is not None:
                        assert decode(sample.payload.to_bytes())["action_id"] == request.action_id
                        break
                    await asyncio.sleep(0.01)
        try:
            await bus.request("request/absent", {}, timeout=0.15)
        except TimeoutError:
            timed_out = True
        else:
            raise AssertionError("Absent service did not time out")
        print(
            json.dumps(
                {
                    "profile": "real_zenoh_loopback",
                    "query": "PASS",
                    "pubsub_fanout": 2,
                    "timeout": timed_out,
                    "health": await bus.request("request/health", {}),
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
