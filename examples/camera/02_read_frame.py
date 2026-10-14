"""OpenCV 显示相机 RGB 和接收 FPS；按 Q/Esc 或关闭窗口退出，不保存图像。"""

import asyncio
import time

import cv2
import numpy as np

from examples._session import use_local_token
from wrs_agent import System
from wrs_agent.errors import AgentError
from wrs_agent.nodes.camera.client import CameraClient

ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
# 与 01 的三项连接配置保持一致；当前使用 voice/07 提供的 Router。
WINDOW = "Camera RGB - Q / Esc to exit"


async def main():
    use_local_token(SESSION)
    async with System.connect(ENDPOINT, env_id=ENV_ID) as system:
        await system.registry.wait_for("camera")
        transport = system.registry.transport("camera")
        camera = CameraClient(transport)
        observations = transport.subscribe("stream/camera/observation")  # 只保留最新事件。
        frame, previous, received = None, None, 0.0
        source, count, fps = "waiting", 0, 0.0
        began = time.monotonic()
        try:
            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            while True:
                if observations.try_recv() is not None:
                    try:
                        info, arrays, age = await camera.read(components=("rgb",), max_age_s=1.0)
                        if info.observation_id != previous and age < 1.0:
                            frame = cv2.cvtColor(arrays["rgb"], cv2.COLOR_RGB2BGR)
                            previous, received = info.observation_id, time.monotonic() - age
                            source = info.source
                            count += 1
                    except (AgentError, TimeoutError):
                        frame = None
                now = time.monotonic()
                if now - began >= 1.0:
                    fps, count, began = count / (now - began), 0, now
                if frame is None or now - received >= 1.0:
                    canvas = np.zeros((480, 848, 3), dtype=np.uint8)
                    label = "FPS: 0.0 | Waiting for fresh frames"
                else:
                    canvas = frame.copy()
                    label = f"FPS: {fps:.1f} | {source}"  # 不同观测的接收帧率，不是窗口刷新率。
                cv2.putText(canvas, label, (15, 30), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, (0, 255, 0), 2, cv2.LINE_AA)
                cv2.imshow(WINDOW, canvas)
                if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
                    break
                try:
                    if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        break
                except cv2.error:  # 某些后端在窗口关闭后直接报错。
                    break
                await asyncio.sleep(0.01)
        finally:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
