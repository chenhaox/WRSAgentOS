"""Deterministic offline model fixture; deferred mode models a hung API request."""

import asyncio

from wrs_agent.cache import parse_intent
from wrs_agent.planner import PlanDecision
from wrs_agent.planner.providers import ModelReply
from wrs_agent.schemas import Plan, Step


def _default_reply(request):
    """Scripted transfer fixture; other input keeps the historical home response."""
    intent = parse_intent(request.goal)
    if intent is None:
        steps = [Step(step_id="home", skill="move_named_pose", args={"pose": "home"})]
    else:
        steps = [
            Step(step_id="observe", skill="observe"),
            Step(
                step_id="pick", skill="pick",
                args={"object": intent.object}, depends_on=["observe"],
            ),
            Step(
                step_id="place", skill="place",
                args={"object": intent.object, "target": intent.target}, depends_on=["pick"],
            ),
            Step(
                step_id="verify", skill="verify",
                args={"object": intent.object, "target": intent.target}, depends_on=["place"],
            ),
        ]
    return PlanDecision(kind="execute", plan=Plan(steps=steps)).model_dump_json()


class MockClient:
    def __init__(self, reply=None, *, deferred=False):
        self.reply = _default_reply if reply is None else reply
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
