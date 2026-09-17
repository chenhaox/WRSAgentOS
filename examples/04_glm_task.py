"""Validate a GLM plan proposal without starting Zenoh or any execution node."""

import argparse
import asyncio
import json
from pathlib import Path

from wrs_agent.planner.api import PlanRequest
from wrs_agent.planner.model import ModelPlanner
from wrs_agent.skills import SPECS

FIXTURE = Path(__file__).parent / "fixtures" / "glm_tool_call.json"


async def run(args):
    try:
        import httpx

        from wrs_agent.planner.providers.glm import GLMClient, GLMConfig, GLMError
    except ImportError:
        raise SystemExit(
            "Install optional dependencies: ./scripts/bootstrap.ps1 -Extra glm"
        ) from None

    try:
        if args.live_model:
            config = GLMConfig.from_env()
            client = GLMClient(config, live_model=True)
        else:
            config = GLMConfig(model="offline-fixture")

            def respond(request):
                return httpx.Response(200, content=FIXTURE.read_bytes())

            client = GLMClient(config, transport=httpx.MockTransport(respond))
        try:
            decision = await ModelPlanner(client).plan(
                PlanRequest(
                    user_goal=args.goal,
                    world={"objects": {"A": "table"}, "held_object": None, "targets": ["B", "C"]},
                    skills=[spec.model_dump(mode="json") for spec in SPECS.values()],
                )
            )
        finally:
            await client.aclose()
    except GLMError as exc:
        raise SystemExit(str(exc)) from None
    except ValueError:
        raise SystemExit(
            "Invalid GLM configuration or model decision; no actions submitted."
        ) from None

    print(
        json.dumps(
            {
                "profile": "glm_live_dry_run" if args.live_model else "glm_offline_http_fixture",
                "network": args.live_model,
                "dry_run": True,
                "actions_submitted": 0,
                "decision": decision.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true", help="Explicit external API opt-in")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Always enabled")
    parser.add_argument("--goal", default="Put A in B and verify.")
    asyncio.run(run(parser.parse_args()))
