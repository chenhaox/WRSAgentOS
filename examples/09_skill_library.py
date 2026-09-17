"""Local skill lookup filtered by real capability queries; no model calls."""

import asyncio
import json

from wrs_agent.environments.api import ActionClient
from wrs_agent.processes import LocalStack
from wrs_agent.skills import lookup_skills


async def main():
    async with LocalStack(runtime=False) as stack:
        caps = {
            name: await ActionClient(bus).capabilities()
            for name, bus in stack.node_transports.items()
        }
        for goal in ["把 A 放到 B", "播报当前状态"]:
            print(
                json.dumps(
                    {
                        "goal": goal,
                        "candidates": [
                            {"skill": s.name, "node": s.node, "resources": s.resources}
                            for s in lookup_skills(goal, caps)
                        ],
                        "model_calls": 0,
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
