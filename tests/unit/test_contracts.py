import asyncio
import math
import threading

import pytest
from pydantic import ValidationError

from wrs_agent.schemas import Envelope, Plan, Step, decode
from wrs_agent.skills import validate_skill
from wrs_agent.transport import Inbox, loopback_config


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("priority", "CONTROL"),
        ("source", "../operator"),
        ("auth", "short"),
        ("env_id", "*"),
        ("authority", "operator"),
    ],
)
def test_boundary_rejects_untrusted_fields(field, value):
    data = dict(source="input", session="one", env_id="arm", auth="a" * 32, payload={})
    data[field] = value
    with pytest.raises(ValidationError):
        Envelope.model_validate(data)


@pytest.mark.parametrize(
    "steps",
    [
        [Step(step_id="a", skill="pick"), Step(step_id="a", skill="pick")],
        [Step(step_id="a", skill="pick", depends_on=["missing"])],
        [
            Step(step_id="a", skill="pick", depends_on=["b"]),
            Step(step_id="b", skill="pick", depends_on=["a"]),
        ],
    ],
)
def test_bad_dag(steps):
    with pytest.raises(ValueError):
        Plan(steps=steps)


@pytest.mark.parametrize(
    "skill,args",
    [
        ("python.eval", {}),
        ("pick", {"object": 1}),
        ("pick", {"object": "A", "duration": 5}),
        ("move_named_pose", {"pose": "unknown"}),
        ("pick", {"object": math.nan}),
    ],
)
def test_invalid_skill(skill, args):
    with pytest.raises(ValueError):
        validate_skill(skill, 1, args)


@pytest.mark.parametrize(
    "data", [b'{"n":NaN}', b"[]", b"x" * 65537], ids=["nonfinite", "array", "oversize"]
)
def test_payload_bounds(data):
    with pytest.raises(ValueError):
        decode(data)


def test_remote_endpoint_rejected():
    with pytest.raises(ValueError):
        loopback_config("tcp/0.0.0.0:7447")


async def test_callback_bridge_bounded_and_ordered():
    inbox = Inbox(16, asyncio.get_running_loop())

    def flood():
        for i in range(10000):
            inbox.put(i)

    worker = threading.Thread(target=flood)
    worker.start()
    await asyncio.to_thread(worker.join)
    assert inbox.high_water == 16
    assert inbox.rejected == 9984
    assert [await inbox.get() for _ in range(16)] == list(range(16))
    assert inbox.put(10001)
    assert await asyncio.wait_for(inbox.get(), 0.2) == 10001
    inbox.close()
    assert not inbox.put(10002)
