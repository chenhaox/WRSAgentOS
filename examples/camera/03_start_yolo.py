"""独立 YOLO26 节点：先运行 voice/07 和 camera/01；02 预览可选。"""

import asyncio
import hashlib
from pathlib import Path

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node
from wrs_agent.nodes.vision import YoloNode

ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
# 与 01 的三项连接配置保持一致。
# voice/07 已有 Router/Agent/TTS，不再启动本目录 00、04、05。

# Ultralytics YOLO26n 检测权重，官方 GitHub 下载：
# https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt
# 许可：AGPL-3.0 / Ultralytics Enterprise（https://www.ultralytics.com/license）。
# 使用已下载到本地的文件；路径相对于仓库，不受启动目录影响。
WEIGHTS = Path(__file__).resolve().parents[2] / ".local/models/yolo/yolo26n.pt"
DEVICE = "cpu"
TABLE_ROI = None  # 可改为 [0.1, 0.2, 0.9, 0.9]，表示归一化的桌面区域 x1/y1/x2/y2。


async def main():
    if not WEIGHTS.is_file() or WEIGHTS.suffix != ".pt":
        raise SystemExit(f"找不到本地 YOLO 检测权重：{WEIGHTS}。请修改文件顶部的 WEIGHTS。")
    # 自动记录所选文件的身份；这不代替你对模型来源的确认。
    with WEIGHTS.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    use_local_token(SESSION)
    print(f"YOLO 连接：{ENDPOINT}，环境 {ENV_ID}，会话 {SESSION}。", flush=True)
    await serve_node(
        YoloNode, node_id="yolo", endpoint=ENDPOINT, env_id=ENV_ID,
        options={"weights": WEIGHTS, "weights_sha256": checksum,
                 "device": DEVICE, "table_roi": TABLE_ROI},
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("视觉节点已关闭。")
