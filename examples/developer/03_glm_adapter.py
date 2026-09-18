"""Validate a GLM plan proposal without starting Zenoh or any execution node."""

import argparse
import asyncio
import json
from pathlib import Path

from wrs_agent.planner import ModelPlanner, PlanRequest
from wrs_agent.skills import SKILLS

FIXTURE = Path(__file__).parents[1] / "fixtures" / "glm_tool_call.json"


async def run(args):
    try:
        import httpx

        from wrs_agent.planner.providers.glm import GLMClient, GLMConfig, GLMError
    except ImportError:
        raise SystemExit(
            "Install optional dependencies: ./scripts/bootstrap.ps1 -Extra glm"
        ) from None

    try:
        # 只有显式 --live-model 才访问真实服务；本脚本始终只展示计划，不提交动作。
        if args.live_model:
            config = GLMConfig.from_env()
            client = GLMClient(config, live_model=True)
        else:
            config = GLMConfig(model="offline-fixture")

            def respond(request):
                return httpx.Response(200, content=FIXTURE.read_bytes())

            # 本地 JSON 代替 HTTP 响应，仍走 GLM 响应解析；不会联网或产生模型费用。
            client = GLMClient(config, transport=httpx.MockTransport(respond))
        try:
            # Client 适配 GLM 协议，Planner 校验计划建议；模型输出本身没有动作执行权。
            decision = await ModelPlanner(client).plan(
                PlanRequest(
                    user_goal=args.goal,
                    # 这里手工提供示例世界和技能；真实任务由 Runtime 获取当前环境信息。
                    world={"objects": {"A": "table"}, "held_object": None, "targets": ["B", "C"]},
                    skills=[entry.spec.model_dump(mode="json") for entry in SKILLS.values()],
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
