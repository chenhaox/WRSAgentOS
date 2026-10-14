"""最后启动：同时显示 01 的 RGB 与 02 的 YOLO 结果；Q/Esc 退出，不保存图像。"""

import asyncio
import contextlib
import time

import cv2
import numpy as np

from examples._session import use_local_token
from wrs_agent import System
from wrs_agent.errors import AgentError
from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.nodes.camera.frames import unpack_frame
from wrs_agent.nodes.vision.results import Objects2D

ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
# 与 01 的三项连接配置保持一致；当前使用 voice/07 提供的 Router。
WINDOW = "Camera RGB + YOLO - Q / Esc to exit"
YOLO_MAX_AGE = 2.0


async def read_yolo(system, view):
    """只读已有结果；独立于 RGB 刷新，YOLO 未启动时窗口也能使用。"""
    cached, detected_frame = None, None
    while True:
        try:
            data = (await system.snapshot("yolo")).data
            if data.health != "ready" or not data.result_id:
                raise AgentError("camera_or_detection_unavailable")
            if cached is None or cached.result_id != data.result_id:
                payload = await system.registry.transport("yolo").request(
                    "request/vision/result", {"result_id": data.result_id}, timeout=0.5,
                )
                result = Objects2D.model_validate(payload)
                if (result.result_id != data.result_id
                        or result.observation_id != data.observation_id):
                    raise ValueError("vision_result_mismatch")
                began = time.monotonic()
                packet = await system.registry.transport("yolo").request_bytes(
                    "request/vision/frame", {"result_id": result.result_id}, timeout=0.5,
                )
                info, arrays, age = unpack_frame(packet)
                if (info.observation_id != result.observation_id
                        or info.stream_id != result.source_stream_id
                        or info.source != result.source
                        or info.captured_at_ns != result.captured_at_ns
                        or (info.width, info.height) != (result.width, result.height)
                        or set(arrays) != {"rgb"}):
                    raise ValueError("vision_frame_mismatch")
                if time.monotonic() - began + age >= YOLO_MAX_AGE:
                    raise AgentError("vision_result_expired")
                detected_frame = cv2.cvtColor(arrays["rgb"], cv2.COLOR_RGB2BGR)
                cached = result
                view["received"] = began - age  # 采集起年龄加完整往返时间，不将下载当作新观测。
            view.update(result=cached, frame=detected_frame, reason="")
        except (AgentError, TimeoutError):
            view.update(result=None, frame=None, reason="YOLO unavailable / restart 02_start_yolo")
        except ValueError:
            view.update(result=None, frame=None, reason="Invalid YOLO result or frame")
        await asyncio.sleep(0.2)


def draw_yolo(frame, result, reason, observation_id, detected_frame=None):
    """左侧实时 RGB，右侧推理时的原图；框和类别始终画在检测对应帧上。"""
    height, width = frame.shape[:2]
    panel_width = max(width, result.width if result else 0, 500)
    image_height = max(480, height, result.height if result else 0)
    canvas = np.zeros((image_height + 60, width + panel_width, 3), dtype=np.uint8)
    canvas[:height, :width] = frame
    lines = ["YOLO", reason]
    if result is not None:
        if detected_frame is None or detected_frame.shape != (result.height, result.width, 3):
            result, reason = None, "Waiting for matching YOLO frame"
            lines = ["YOLO", reason]
    if result is not None:
        canvas[:result.height, width:width + result.width] = detected_frame
        lines = [f"YOLO: {len(result.items)} object(s) | {result.scope}",
                 f"Source: {result.source}"]
        for item in result.items:
            x1, y1, x2, y2 = map(int, item.box)
            x1, x2 = min(x1, result.width - 1), min(x2, result.width - 1)
            y1, y2 = min(y1, result.height - 1), min(y2, result.height - 1)
            cv2.rectangle(canvas, (width + x1, y1), (width + x2, y2), (0, 255, 255), 2)
            label = f"{item.label[:40]}  {item.score:.0%}"
            text_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0][0]
            text_x = width + max(0, min(x1, panel_width - text_width - 4))
            cv2.putText(canvas, label, (text_x, max(16, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 255, 255), 1, cv2.LINE_AA)
        if not result.items:
            lines.append("No objects detected")
    for index, line in enumerate(lines):
        cv2.putText(canvas, line, (width + 12, 25 * (index + 1)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (255, 255, 255), 1, cv2.LINE_AA)
    for x, text in ((12, f"Live RGB: {observation_id or 'waiting'}"),
                    (width + 12, f"Observation: {result.observation_id if result else 'waiting'}")):
        cv2.putText(canvas, text, (x, image_height + 25), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, "YOLO inference frame (may lag live RGB)", (width + 12, image_height + 48),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


async def main():
    use_local_token(SESSION)
    async with System.connect(ENDPOINT, env_id=ENV_ID) as system:
        await system.registry.wait_for("camera")
        transport = system.registry.transport("camera")
        camera = CameraClient(transport)
        observations = transport.subscribe("stream/camera/observation")  # 只保留最新事件。
        view = {"result": None, "frame": None, "received": 0.0,
                "reason": "Waiting for 02_start_yolo"}
        yolo = asyncio.create_task(read_yolo(system, view))
        frame, previous, received = None, None, 0.0
        stream = None
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
                            stream = info.stream_id
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
                    result, reason = None, "Waiting for fresh camera frames"
                else:
                    canvas = frame.copy()
                    label = f"FPS: {fps:.1f} | {source}"  # 不同观测的接收帧率，不是窗口刷新率。
                    result, reason = view["result"], view["reason"]
                    if result is not None and now - view["received"] >= YOLO_MAX_AGE:
                        result, reason = None, "YOLO stale / waiting for new result"
                    elif result is not None and result.source_stream_id != stream:
                        result, reason = None, "YOLO belongs to another camera stream"
                canvas = draw_yolo(canvas, result, reason, previous, view["frame"])
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
            yolo.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await yolo
            cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
