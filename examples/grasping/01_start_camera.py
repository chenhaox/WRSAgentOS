"""采集 RealSense D405 的 RGB、深度和点云；运行本文件会打开真实相机。"""

import asyncio

from examples._session import use_local_token
from wrs_agent.nodes.camera import CameraNode
from wrs_agent.nodes.serve import serve_node

ENDPOINT = "tcp/127.0.0.1:7449"
ENV_ID = "wrs-demo"
SESSION = "wrs"
SERIAL = ""  # 留空使用默认设备；多台相机时填写 D405 的序列号。
WIDTH, HEIGHT, FPS = 848, 480, 15


async def main():
    use_local_token(SESSION)
    await serve_node(
        CameraNode, node_id="camera", endpoint=ENDPOINT, env_id=ENV_ID,
        options={"backend": "realsense", "allow_hardware": True, "serial": SERIAL,
                 "width": WIDTH, "height": HEIGHT, "fps": FPS},
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("RealSense 相机节点已关闭。")
