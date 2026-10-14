"""FastSAM 框提示分割；使用 .local/venvs/vision 的解释器，先启动 00–02。"""

import asyncio
import json
from importlib.resources import files
from pathlib import Path

from examples._session import use_local_token
from wrs_agent.nodes.segmentation import SegmentationNode
from wrs_agent.nodes.serve import serve_node

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "tcp/127.0.0.1:7449"
ENV_ID = "wrs-demo"
SESSION = "wrs"
WEIGHTS = ROOT / ".local/models/fastsam/FastSAM-x.pt"
DEVICE = "cuda"
CONFIDENCE = .4
ASSET = json.loads(files("wrs_agent.nodes.segmentation").joinpath(
    "fastsam_assets.json").read_text("utf-8"))


async def main():
    if not WEIGHTS.is_file():
        raise SystemExit(f"找不到本地 FastSAM 权重：{WEIGHTS}。准备方法见 README.md。")
    use_local_token(SESSION)
    await serve_node(
        SegmentationNode, node_id="segmentation", endpoint=ENDPOINT, env_id=ENV_ID,
        options={"backend": "fastsam-box", "device": DEVICE, "locator": "yolo",
                 "weights": WEIGHTS, "weights_sha256": ASSET["sha256"],
                 "confidence": CONFIDENCE},
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("FastSAM 节点已关闭。")
