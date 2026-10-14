"""先启动 voice/07，再采集图像供语音 viewer 使用；相机配置在下方。"""

import asyncio

from examples._session import use_local_token
from wrs_agent.nodes.camera import CameraNode
from wrs_agent.nodes.serve import serve_node

ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
# voice/07 已包含 Router，无需启动本目录 00。
# 独立相机模式：01/02/03 一起改回 "tcp/127.0.0.1:7449"、"wrs-demo"、"wrs"。

# 选择 "realsense" 就会打开真实相机；"synthetic" 使用合成图像。
BACKEND = "realsense"
SERIAL = ""  # 留空使用默认相机，多台设备时可指定序列号。
WIDTH, HEIGHT, FPS = 848, 480, 30  # 仅调整相机采集，YOLO/分割/抓姿独立调度。


async def main():
    use_local_token(SESSION)
    print(f"相机连接：{ENDPOINT}，环境 {ENV_ID}，会话 {SESSION}。", flush=True)
    await serve_node(
        CameraNode, endpoint=ENDPOINT, env_id=ENV_ID,
        options={"backend": BACKEND, "serial": SERIAL, "allow_hardware": BACKEND == "realsense",
                 "width": WIDTH, "height": HEIGHT, "fps": FPS},
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("相机节点已关闭。")
