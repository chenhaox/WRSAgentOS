"""用 fafu_arm_sdk 提供机器人节点。默认只注册能力，不打开串口、不使能。

先停掉同一会话里的虚拟 wrs 节点，再运行本文件。切到实机：

    await system.set_robot_mode("real")
    await system.allow_actions()

SDK 目录用环境变量 FAFU_ARM_SDK，未设置时用本机 Python 3.12 的
/home/fafu_robot2/grt/fafu_arm_sdk_chenhaox。
实测手眼 JSON 用 FAFU_HAND_EYE。没有这份文件时，节点不提供 apply_hand_eye。
"""

import asyncio
import os
from pathlib import Path

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node
from wrs_agent.nodes.wrs.node import WrsNode

ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
SDK = Path(os.environ.get("FAFU_ARM_SDK", "/home/fafu_robot2/grt/fafu_arm_sdk_chenhaox"))
HAND_EYE = os.environ.get("FAFU_HAND_EYE")


async def main():
    if not (SDK / "fafu_robot_python" / "fafu_robot_controller.py").is_file():
        raise SystemExit(f"找不到 fafu_arm_sdk：{SDK}")
    if HAND_EYE and not Path(HAND_EYE).is_file():
        raise SystemExit(f"找不到手眼标定：{HAND_EYE}")
    use_local_token(SESSION)
    options = {
        "backend": "fafu",
        "sdk_root": SDK,
        "allow_hardware": True,
        "port": "auto",
    }
    if HAND_EYE:
        options["grasp_calibration"] = HAND_EYE
    await serve_node(
        WrsNode, node_id="wrs", endpoint=ENDPOINT, env_id=ENV_ID, options=options,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Fafu 机器人节点已关闭。")
