"""GraspNet 候选预测；使用 .local/venvs/graspnet 的解释器，先启动 00–03。"""

import asyncio
import json
from importlib.resources import files
from pathlib import Path

from examples._session import use_local_token
from wrs_agent.nodes.grasp import GraspNetNode
from wrs_agent.nodes.serve import serve_node

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "tcp/127.0.0.1:7449"
ENV_ID = "wrs-demo"
SESSION = "wrs"
WEIGHTS = ROOT / ".local/models/graspnet/checkpoint-rs.tar"
SOURCE = ROOT / ".local/src/graspnet-baseline"
MAX_WIDTH_M = .05  # 夹爪最大开口（米）；不是机器人 TCP 或相机外参。
ASSET = json.loads(files("wrs_agent.nodes.grasp").joinpath("assets.json").read_text("utf-8"))


async def main():
    if not WEIGHTS.is_file() or not (SOURCE / "models/graspnet.py").is_file():
        raise SystemExit(
            "缺少 GraspNet 本地权重或源码。请检查 WEIGHTS / SOURCE，准备方法见 README.md。"
        )
    use_local_token(SESSION)
    await serve_node(
        GraspNetNode, node_id="graspnet", endpoint=ENDPOINT, env_id=ENV_ID,
        options={"weights": WEIGHTS, "weights_sha256": ASSET["sha256"],
                 "source": SOURCE, "source_revision": ASSET["source_revision"],
                 "segmentation": "segmentation", "max_width_m": MAX_WIDTH_M},
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("GraspNet 节点已关闭。")
