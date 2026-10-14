"""启动感知任务调度；06 提交固定步骤，无需在线模型、WRS 或语音节点。"""

import asyncio

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node

ENDPOINT = "tcp/127.0.0.1:7449"
ENV_ID = "wrs-demo"
SESSION = "wrs"


async def main():
    use_local_token(SESSION)
    await serve_node("agent", endpoint=ENDPOINT, env_id=ENV_ID, options={"live_model": False})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Agent 已关闭。")
