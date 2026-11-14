"""FAFU 实机节点（注册为 wrs）。EXECUTE=False 时只规划和动画，不打开串口。

先完成 01 手眼标定。改成 True 前：手放在急停上、速度保持 real.toml 里的低值。
"""

import asyncio

from examples._session import use_local_token
from wrs_agent.nodes.fafu import FafuNode
from wrs_agent.nodes.serve import serve_node

ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
EXECUTE = False


async def main():
    use_local_token(SESSION)
    print("实机执行" if EXECUTE else "dry-run：机械臂不会动", flush=True)
    await serve_node(FafuNode, endpoint=ENDPOINT, env_id=ENV_ID, options={"execute": EXECUTE})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("FAFU 节点已关闭；电机按 release_on_close 处理。")
