"""启动 YOLO、SAM3、GraspNet、graspability 四个常驻节点。先运行 00_start_router.py。

模型在第一次技能调用时加载，进程退出前不重复加载。
YOLO / SAM3 使用带 ultralytics 的解释器，默认探测 ~/miniconda3/envs/yolov8。
GraspNet 默认使用 ~/grt/graspnet-baseline 和其中的
checkpoints/checkpoint-rs.tar。可用 WRS_GRASPNET_ROOT、
WRS_GRASPNET_CHECKPOINT 覆盖。

可选：WRS_VISION_PYTHON、WRS_YOLO_WEIGHTS、WRS_SAM3_WEIGHTS、
WRS_ENDPOINT（默认 tcp/127.0.0.1:7448）、WRS_ENV_ID（默认 node-demo）。
"""

import asyncio
import os
from pathlib import Path

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node
from wrs_agent.nodes.vision.nodes import GraspabilityNode, GraspNetNode, Sam3Node, YoloNode

NODES = (
    (YoloNode, "yolo"),
    (Sam3Node, "sam3"),
    (GraspNetNode, "graspnet"),
    (GraspabilityNode, "graspability"),
)


def configure_local_models():
    if not os.environ.get("WRS_VISION_PYTHON"):
        candidate = Path.home() / "miniconda3/envs/yolov8/bin/python"
        if candidate.is_file():
            os.environ["WRS_VISION_PYTHON"] = str(candidate)
    if not os.environ.get("WRS_YOLO_WEIGHTS"):
        for path in (
            Path.home() / "Desktop/raw_image/weights/yolo11m.pt",
            Path.home() / "Desktop/raw_image/yolov8n.pt",
        ):
            if path.is_file():
                os.environ["WRS_YOLO_WEIGHTS"] = str(path)
                break
    if not os.environ.get("WRS_SAM3_WEIGHTS"):
        sam3 = Path.home() / "Desktop/raw_image/weights/sam3.pt"
        if sam3.is_file():
            os.environ["WRS_SAM3_WEIGHTS"] = str(sam3)
    root = Path.home() / "grt/graspnet-baseline"
    if not os.environ.get("WRS_GRASPNET_ROOT") and (root / "models" / "graspnet.py").is_file():
        os.environ["WRS_GRASPNET_ROOT"] = str(root)
    checkpoint = root / "checkpoints" / "checkpoint-rs.tar"
    if not os.environ.get("WRS_GRASPNET_CHECKPOINT") and checkpoint.is_file():
        os.environ["WRS_GRASPNET_CHECKPOINT"] = str(checkpoint)


async def main():
    configure_local_models()
    endpoint = os.environ.get("WRS_ENDPOINT", "tcp/127.0.0.1:7448")
    env_id = os.environ.get("WRS_ENV_ID", "node-demo")
    print(f"视觉节点加入 {endpoint}，env {env_id}", flush=True)
    print(
        "技能：detect（yolo）、segment（sam3）、infer_grasps（graspnet）、"
        "graspability（深度区域分割后的平行夹爪）",
        flush=True,
    )
    print(f"解释器：{os.environ.get('WRS_VISION_PYTHON', '当前 Python')}", flush=True)
    weights = os.environ.get("WRS_YOLO_WEIGHTS", "yolov8n.pt")
    print(f"YOLO 权重：{weights}", flush=True)
    print(f"SAM3：{os.environ.get('WRS_SAM3_WEIGHTS', 'sam3.pt')}", flush=True)
    print(f"GraspNet：{os.environ.get('WRS_GRASPNET_CHECKPOINT', '未找到权重')}", flush=True)
    async with asyncio.TaskGroup() as group:
        for node, node_id in NODES:
            group.create_task(
                serve_node(
                    node,
                    node_id=node_id,
                    endpoint=endpoint,
                    site="local",
                    env_id=env_id,
                )
            )


if __name__ == "__main__":
    use_local_token("nodes", create=True)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("视觉节点已关闭。")
