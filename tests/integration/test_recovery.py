import pytest
from conftest import eventually

from wrs_agent.processes import LocalStack
from wrs_agent.schemas import Plan, Step, new_id

pytestmark = pytest.mark.zenoh


def transfer():
    return Plan(
        steps=[
            Step(step_id="observe", skill="observe"),
            Step(step_id="pick", skill="pick", args={"object": "A"}, depends_on=["observe"]),
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


@pytest.mark.parametrize(
    "fault,state,recoveries,executions",
    [
        ("grasp_once", "SUCCEEDED", 1, 6),
        ("localization_once", "SUCCEEDED", 1, 6),
        ("grasp", "FAILED", 1, 4),
        ("unknown", "UNKNOWN", 0, 1),
        ("inconclusive", "UNKNOWN", 0, 1),
    ],
)
async def test_one_recovery_only_with_fresh_ids(fault, state, recoveries, executions):
    async with LocalStack(duration=0.03, fault=fault) as stack:
        await stack.system.clients["wrs"].transport.request(
            "request/task/start", {"request_id": new_id(), "plan": transfer().model_dump()}
        )
        final = await eventually(
            lambda: stack.system.clients["wrs"].transport.request("request/task/status", {}),
            lambda s: s["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN"},
        )
        assert final["state"] == state, final
        assert final["recoveries"] == recoveries
        history = final["action_history"]
        ids = [action["action_id"] for action in history]
        assert len(ids) == len(set(ids)) == executions
        assert (await stack.system.clients["wrs"].transport.request("request/health", {}))[
            "executions"
        ] == executions
        world = await stack.system.clients["wrs"].snapshot()
        assert world.data.held_object is None
        assert world.data.objects["A"] == ("B" if state == "SUCCEEDED" else "table")


async def test_stop_during_reobserve_prevents_retry():
    async with LocalStack(duration=0.3, fault="grasp_once") as stack:
        await stack.system.clients["wrs"].transport.request(
            "request/task/start", {"request_id": new_id(), "plan": transfer().model_dump()}
        )
        await eventually(
            lambda: stack.system.clients["wrs"].transport.request("request/task/status", {}),
            lambda s: s["recoveries"] == 1 and len(s["action_history"]) >= 3,
        )
        current = await stack.system.clients["wrs"].transport.request("request/task/status", {})
        await stack.system.clients["wrs"].transport.request(
            "request/task/hold",
            {"request_id": new_id(), "task_id": current["task_id"]},
            control=True,
        )
        node = stack.system.clients["wrs"]
        await eventually(node.snapshot, lambda w: w.stop_confirmed)
        final = await stack.system.clients["wrs"].transport.request("request/task/status", {})
        assert final["state"] == "HELD"
        assert len(final["action_history"]) == 3
        assert (await stack.system.clients["wrs"].transport.request("request/health", {}))[
            "executions"
        ] <= 3


async def test_invalid_precondition_never_retried():
    async with LocalStack(duration=0.03) as stack:
        plan = Plan(steps=[Step(step_id="pick", skill="pick", args={"object": "missing"})])
        await stack.system.clients["wrs"].transport.request(
            "request/task/start", {"request_id": new_id(), "plan": plan.model_dump()}
        )
        final = await eventually(
            lambda: stack.system.clients["wrs"].transport.request("request/task/status", {}),
            lambda s: s["state"] == "FAILED",
        )
        assert final["recoveries"] == 0
        assert len(final["action_history"]) == 1
