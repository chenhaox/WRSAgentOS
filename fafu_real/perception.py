"""相机、YOLO-World、FastSAM，接 fafu 的同一个路由器。

写法和这三份示例一样，只是地址改成 fafu_config 的 ENDPOINT / ENV_ID：

- examples/grasping/01_start_camera.py
- examples/grasping/02_start_locator.py
- examples/grasping/03_start_fastsam.py

权重不进 Git。GitHub 上的两份（Ultralytics assets v8.4.0，AGPL-3.0）：

- https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8m-worldv2.pt
- https://github.com/ultralytics/assets/releases/download/v8.4.0/FastSAM-x.pt

开放词汇还要 CLIP ``ViT-B-32.pt``。它不在 GitHub，地址和 SHA256 在
``wrs_agent/nodes/segmentation/clip_assets.json``。

    python fafu_real/perception.py camera
    python fafu_real/perception.py yolo
    python fafu_real/perception.py fastsam

``python fafu_real/main.py --mode node`` 会自己拉起这三个进程，并带上同一个
``WRS_AGENT_TOKEN``。单独跑时令牌要和那个 node 一致。

这三个节点只发布画面、检测框和掩膜。厨房物体的世界坐标仍来自 ``at_deg``。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from importlib.resources import files
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fafu_config as C  # noqa: E402

from wrs_agent.nodes.camera import CameraNode  # noqa: E402
from wrs_agent.nodes.segmentation import SegmentationNode  # noqa: E402
from wrs_agent.nodes.serve import serve_node  # noqa: E402
from wrs_agent.nodes.vision import YoloNode  # noqa: E402

RUN_DIR = ROOT / ".local" / "runs" / "fafu-real"
ROLES = ("camera", "yolo", "fastsam")
YOLO_ASSET = json.loads(files("wrs_agent.nodes.vision").joinpath("assets.json").read_text("utf-8"))
FASTSAM_ASSET = json.loads(
    files("wrs_agent.nodes.segmentation").joinpath("fastsam_assets.json").read_text("utf-8")
)


def _token() -> None:
    token = os.environ.get("WRS_AGENT_TOKEN", "")
    if not (16 <= len(token) <= 128) or not token.isascii() or any(c.isspace() for c in token):
        raise SystemExit(
            "WRS_AGENT_TOKEN 缺失或无效。用 python fafu_real/main.py --mode node 启动，"
            "或把和那个 node 相同的令牌设进环境变量。"
        )


def _module(name: str, hint: str) -> None:
    try:
        __import__(name)
    except ImportError as exc:
        raise SystemExit(f"当前解释器没有 {name}：{sys.executable}\n{hint}") from exc


def _weights(*paths: str) -> None:
    missing = [path for path in paths if not os.path.isfile(path)]
    if missing:
        raise SystemExit("找不到本地权重：\n" + "\n".join(missing))


async def serve_camera() -> None:
    _module("pyrealsense2", "用带 RealSense 的解释器，或设置 fafu_config.CAMERA_PYTHON。")
    serial = C.CAMERA_SERIAL or ""
    print(f"[camera] {C.ENDPOINT}  环境 {C.ENV_ID}  序列号 {serial or '默认'}", flush=True)
    await serve_node(
        CameraNode, node_id="camera", endpoint=C.ENDPOINT, env_id=C.ENV_ID,
        journal=str(RUN_DIR / "camera.sqlite3"),
        options={
            "backend": "realsense", "allow_hardware": True, "serial": serial,
            "width": C.CAMERA_WIDTH, "height": C.CAMERA_HEIGHT, "fps": C.CAMERA_FPS,
        },
    )


async def serve_yolo() -> None:
    _module("ultralytics", "先跑 scripts/setup_vision.ps1，再设置 fafu_config.VISION_PYTHON。")
    _weights(C.YOLO_WEIGHTS, C.CLIP_WEIGHTS)
    print(f"[yolo] {C.ENDPOINT}  环境 {C.ENV_ID}  设备 {C.YOLO_DEVICE}", flush=True)
    await serve_node(
        YoloNode, node_id="yolo", endpoint=C.ENDPOINT, env_id=C.ENV_ID,
        journal=str(RUN_DIR / "yolo.sqlite3"),
        options={
            "weights": C.YOLO_WEIGHTS, "weights_sha256": YOLO_ASSET["sha256"],
            "clip_weights": C.CLIP_WEIGHTS, "device": C.YOLO_DEVICE,
            "camera": "camera", "confidence": C.YOLO_CONFIDENCE,
        },
    )


async def serve_fastsam() -> None:
    _module("ultralytics", "先跑 scripts/setup_vision.ps1，再设置 fafu_config.VISION_PYTHON。")
    _weights(C.FASTSAM_WEIGHTS)
    print(f"[fastsam] {C.ENDPOINT}  环境 {C.ENV_ID}  设备 {C.FASTSAM_DEVICE}", flush=True)
    await serve_node(
        SegmentationNode, node_id="segmentation", endpoint=C.ENDPOINT, env_id=C.ENV_ID,
        journal=str(RUN_DIR / "fastsam.sqlite3"),
        options={
            "backend": "fastsam-box", "device": C.FASTSAM_DEVICE, "locator": "yolo",
            "weights": C.FASTSAM_WEIGHTS, "weights_sha256": FASTSAM_ASSET["sha256"],
            "confidence": C.FASTSAM_CONFIDENCE,
        },
    )


def main(argv=None) -> int:
    role = sys.argv[1:] if argv is None else list(argv)
    if len(role) != 1 or role[0] not in ROLES:
        raise SystemExit("用法：python fafu_real/perception.py camera|yolo|fastsam")
    _token()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run({"camera": serve_camera, "yolo": serve_yolo, "fastsam": serve_fastsam}[role[0]]())
    except KeyboardInterrupt:
        print(f"\n[{role[0]}] 已关闭")
    return 0


if __name__ == "__main__":
    sys.exit(main())
