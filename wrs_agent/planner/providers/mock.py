"""Deterministic offline model fixture; deferred mode models a hung API request."""

import asyncio

from wrs_agent.planner.providers.api import ModelReply


class MockClient:
    def __init__(self, reply, *, deferred=False):
        self.reply = reply
        self.gate = asyncio.Event()
        self.calls = 0
        self.entered = asyncio.Event()
        if not deferred:
            self.gate.set()

    async def complete(self, request):
        self.calls += 1
        self.entered.set()
        await self.gate.wait()
        return ModelReply(
            text=self.reply(request) if callable(self.reply) else self.reply,
            finish="complete",
            metadata={"provider": "mock"},
        )

    async def aclose(self):
        self.gate.set()
