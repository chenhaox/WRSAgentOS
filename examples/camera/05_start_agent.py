"""启动播报任务的 Agent；自动发现 YOLO/TTS，默认只调度固定步骤，不调用大模型。"""

import asyncio

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node

ENDPOINT = "tcp/127.0.0.1:7449"
ENV_ID = "wrs-demo"
SESSION = "wrs"
LIVE_MODEL = False  # 只有使用自然语言 GOAL 时才需开启，并先配置 LLM_* 环境变量。


async def main():
    use_local_token(SESSION)
    await serve_node("agent", endpoint=ENDPOINT, env_id=ENV_ID,
                     options={"live_model": LIVE_MODEL})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Agent 已关闭。")
