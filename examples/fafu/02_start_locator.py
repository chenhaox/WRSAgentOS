"""YOLO-World 定位，词表来自 classes.toml；替代 grasping/02，两者不要同时运行。"""

import asyncio
import json
from importlib.resources import files
from pathlib import Path

from examples._session import use_local_token
from wrs_agent.nodes.fafu.collision import load_catalog
from wrs_agent.nodes.serve import serve_node
from wrs_agent.nodes.vision import YoloNode

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
WEIGHTS = ROOT / ".local/models/yolo/yolov8m-worldv2.pt"
CLIP_WEIGHTS = ROOT / ".local/models/fastsam/ViT-B-32.pt"
DEVICE = "0"
CONFIDENCE = .35
ASSET = json.loads(files("wrs_agent.nodes.vision").joinpath("assets.json").read_text("utf-8"))


async def main():
    for path in (WEIGHTS, CLIP_WEIGHTS):
        if not path.is_file():
            raise SystemExit(f"找不到本地权重：{path}。准备方法见 examples/grasping/README.md。")
    vocabulary = load_catalog().vocabulary
    print(f"检测词表：{', '.join(vocabulary)}", flush=True)
    use_local_token(SESSION)
    await serve_node(
        YoloNode, node_id="yolo", endpoint=ENDPOINT, env_id=ENV_ID,
        options={"weights": WEIGHTS, "weights_sha256": ASSET["sha256"],
                 "clip_weights": CLIP_WEIGHTS, "device": DEVICE,
                 "camera": "camera", "confidence": CONFIDENCE, "vocabulary": vocabulary},
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("目标定位节点已关闭。")
