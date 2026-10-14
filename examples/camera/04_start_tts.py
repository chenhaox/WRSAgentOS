"""启动独立 TTS；默认 Mock 无声，准备好 qwen 环境后切换后端即可播放。"""

import asyncio

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node

ENDPOINT = "tcp/127.0.0.1:7449"
ENV_ID = "wrs-demo"
SESSION = "wrs"
BACKEND = "qwen"  # 使用 "qwen" 时，先准备语音环境和本地模型，再用该环境的 Python 运行。


async def main():
    use_local_token(SESSION)
    await serve_node("tts", endpoint=ENDPOINT, env_id=ENV_ID, options={"backend": BACKEND})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("TTS 节点已关闭。")
