import pytest
from pydantic import ValidationError

from wrs_agent.planner import ModelPlanner, PlanRequest
from wrs_agent.planner.providers import ModelReply
from wrs_agent.planner.providers.mock import MockClient
from wrs_agent.policy import decide_event
from wrs_agent.schemas import Interaction


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("vad", "ignore"),
        ("ack", "ignore"),
        ("query", "answer"),
        ("append", "enqueue"),
        ("revise", "update"),
        ("stop", "hold"),
        ("barge_in", "cancel_tts"),
    ],
)
def test_intents(kind, expected):
    assert decide_event(Interaction(event_id="event", kind=kind)) == expected


@pytest.mark.parametrize("flags", [{"quoted": True}, {"negated": True}, {"confidence": 0.3}])
def test_ambiguous_stop(flags):
    assert decide_event(Interaction(event_id="event", kind="stop", **flags)) == "clarify"


async def test_model_client_swappable():
    request = PlanRequest(user_goal="question", world={}, skills=[])

    class AlternateClient:
        async def complete(self, request):
            return ModelReply(text='{"kind":"answer","text":"second"}', finish="complete")

        async def aclose(self):
            return None

    for client in [
        MockClient('{"kind":"answer","text":"first"}'),
        AlternateClient(),
    ]:
        decision = await ModelPlanner(client).plan(request)
        assert decision.kind == "answer"
        await client.aclose()


@pytest.mark.parametrize(
    "reply",
    [
        '{"kind":"execute"}',
        '{"kind":"execute","plan":',
        '{"kind":"execute","authority":"operator","plan":{"steps":[]}}',
    ],
)
async def test_invalid_model_proposal(reply):
    with pytest.raises((ValueError, ValidationError)):
        await ModelPlanner(MockClient(reply)).plan(
            PlanRequest(user_goal="goal", world={}, skills=[])
        )
