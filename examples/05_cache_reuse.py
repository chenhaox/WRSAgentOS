"""Strict template reuse over real Zenoh. All execution and model replies are Mock."""

import asyncio
import json

from wrs_agent.bindings import load_bindings
from wrs_agent.cache import parse_intent
from wrs_agent.environments.api import ActionClient
from wrs_agent.planner.api import PlanDecision
from wrs_agent.planner.model import ModelPlanner
from wrs_agent.planner.providers.mock import MockClient
from wrs_agent.processes import LocalStack
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import GoalRequest, Plan, Step, TaskRequest, new_id


def transfer(object_name, target):
    return Plan(
        steps=[
            Step(step_id="o", skill="observe"),
            Step(step_id="p", skill="pick", args={"object": object_name}, depends_on=["o"]),
            Step(
                step_id="l",
                skill="place",
                args={"object": object_name, "target": target},
                depends_on=["p"],
            ),
            Step(
                step_id="v",
                skill="verify",
                args={"object": object_name, "target": target},
                depends_on=["l"],
            ),
        ]
    )


def propose(request):
    intent = parse_intent(request.goal)
    return PlanDecision(
        kind="execute", plan=transfer(intent.object, intent.target)
    ).model_dump_json()


async def main():
    async with LocalStack(runtime=False, duration=0.03) as stack:
        client = MockClient(propose)
        nodes = {name: ActionClient(bus) for name, bus in stack.node_transports.items()}
        runtime = Runtime(nodes, load_bindings()[1], ModelPlanner(client))
        try:
            # Establish identical initial conditions without a planner request.
            await runtime.start(TaskRequest(request_id=new_id(), plan=transfer("A", "B")))
            async with asyncio.timeout(5):
                await asyncio.gather(*runtime.workers)
            assert runtime.state == "SUCCEEDED"
            for goal in ["put A in B", "put A in B", "put A in C", "put A in B"]:
                await runtime.goal(GoalRequest(request_id=new_id(), goal=goal))
                await runtime.planning
                assert runtime.state == "SUCCEEDED", runtime.snapshot()
                print(
                    json.dumps(
                        {
                            "profile": "zenoh_mock",
                            "goal": goal,
                            "cache_hit": runtime.cache.last_hit,
                            "reject_reason": runtime.cache.reject_reason,
                            "model_calls": client.calls,
                            "object_location": (await nodes["wrs"].snapshot()).objects["A"],
                        }
                    )
                )
            assert client.calls == 3 and runtime.cache.hits == 1
        finally:
            await runtime.close()
            await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
