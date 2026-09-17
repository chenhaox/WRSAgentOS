"""Separate process used to verify cancellation of an outstanding real query."""

import asyncio
import os
import sys

from wrs_agent.transport import Transport


async def main():
    endpoint, site, env_id = sys.argv[1:]
    bus = Transport(endpoint, site, env_id, os.environ["WRS_AGENT_TOKEN"], "slow-fixture")
    pending = asyncio.Event()
    entered = asyncio.Event()

    async def slow(payload):
        entered.set()
        await pending.wait()
        return {}

    async def status(payload):
        return {"entered": entered.is_set()}

    bus.register_handler("request/test/slow", slow)
    bus.register_handler("request/test/slow_status", status, control=True)
    try:
        await asyncio.Event().wait()
    finally:
        await bus.close()


asyncio.run(main())
