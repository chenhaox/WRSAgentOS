"""实时查看 GraspNet：左边实时 RGB，右边最近一次推理的原图、分割、检测框和抓取候选。

只读，不提交任务；候选来自 06、fafu 实机的 pick 流程或任何 infer_grasps。Q/Esc 退出。
候选投影：绿色最优，黄色其次；横线是两指张开宽度，竖线沿接近方向到指尖。
"""

import asyncio
import contextlib
import time

import cv2
import numpy as np

from examples._session import use_local_token
from wrs_agent import System
from wrs_agent.errors import AgentError
from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.nodes.grasp.data import LatestGrasps, read_grasps

ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
GRASP_NODE = "graspnet"
SHOWN = 8
WINDOW = "Live RGB + GraspNet - Q / Esc to exit"


def project(points, calibration):
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    z = np.maximum(points[:, 2], 1e-6)
    u = calibration.fx * points[:, 0] / z + calibration.cx
    v = calibration.fy * points[:, 1] / z + calibration.cy
    return np.stack((u, v), axis=1).round().astype(int)


def draw_grasps(data):
    """Overlay on the retained inference frame, never on a newer one."""
    segment = data.segment
    observation = segment.info.observation
    frame = cv2.cvtColor(segment.arrays["rgb"], cv2.COLOR_RGB2BGR)
    mask = segment.mask
    frame[mask] = (frame[mask] * .55 + np.array([100, 220, 40]) * .45).astype(np.uint8)
    if segment.info.location is not None:
        x1, y1, x2, y2 = map(int, segment.info.location.object.box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (30, 170, 255), 2)
    ranked = sorted(data.info.candidates, key=lambda c: -c.score)[:SHOWN]
    for rank, candidate in reversed(list(enumerate(ranked))):
        rotation = np.asarray(candidate.rotation).reshape(3, 3)
        center = np.asarray(candidate.translation)
        approach, closing = rotation[:, 0], rotation[:, 1]
        half = closing * candidate.width_m / 2
        tip = approach * candidate.depth_m
        a, b = center - half, center + half
        tail = center - approach * 0.04
        pa, pb, ta, tb, pc, pt = project([a, b, a + tip, b + tip, center, tail],
                                         observation.calibration)
        color = (0, 255, 0) if rank == 0 else (0, 220, 255) if rank < 3 else (160, 160, 160)
        thick = 3 if rank == 0 else 1
        cv2.line(frame, tuple(pa), tuple(pb), color, thick, cv2.LINE_AA)
        cv2.line(frame, tuple(pa), tuple(ta), color, thick, cv2.LINE_AA)
        cv2.line(frame, tuple(pb), tuple(tb), color, thick, cv2.LINE_AA)
        cv2.line(frame, tuple(pc), tuple(pt), color, thick, cv2.LINE_AA)
        cv2.putText(frame, str(rank + 1), tuple(pt), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1,
                    cv2.LINE_AA)
    best = ranked[0]
    lines = [f"target {segment.info.target}  candidates {len(data.info.candidates)}",
             f"best score {best.score:.3f}  width {best.width_m * 1000:.0f} mm  "
             f"z {best.translation[2]:.3f} m",
             f"task {data.info.owner.task_id[:12]}  obs {observation.observation_id[-10:]}"]
    return frame, lines, observation.stream_id


async def read_latest(system, view):
    cached = None
    while True:
        try:
            transport = system.registry.transport(GRASP_NODE)
            latest = LatestGrasps.model_validate(
                await transport.request("request/grasp/latest", {}, timeout=0.5))
            if cached != latest.candidate_set_id:
                data, _ = await read_grasps(transport, latest.candidate_set_id, latest.task_id)
                frame, lines, stream = draw_grasps(data)
                cached = latest.candidate_set_id
                view.update(frame=frame, lines=lines, stream=stream)
            view.update(age=latest.age_s, reason="")
        except AgentError as exc:
            view.update(reason="no grasps yet" if exc.code == "grasp_not_available" else exc.code)
        except TimeoutError:
            view.update(reason="graspnet unavailable / start 04_start_graspnet")
        except ValueError:
            view.update(reason="invalid grasp packet")
        await asyncio.sleep(0.3)


def compose(live, view):
    height, width = live.shape[:2]
    right = view["frame"]
    panel = (np.zeros((height, width, 3), np.uint8) if right is None
             else cv2.resize(right, (width, height)))
    canvas = np.zeros((height + 80, width * 2, 3), np.uint8)
    canvas[:height, :width], canvas[:height, width:] = live, panel
    lines = list(view["lines"]) if right is not None else []
    if right is not None:
        lines.append(f"age {view['age']:.1f} s")
    if view["reason"]:
        lines.append(view["reason"])
    for index, line in enumerate(lines[:4]):
        cv2.putText(canvas, line, (width + 10, height + 18 + 18 * index), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, "live RGB", (10, height + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


async def main():
    use_local_token(SESSION)
    async with System.connect(ENDPOINT, env_id=ENV_ID) as system:
        await system.registry.wait_for("camera")
        camera = CameraClient(system.registry.transport("camera"))
        with contextlib.suppress(TimeoutError, AgentError):
            await system.registry.wait_for(GRASP_NODE, timeout=2.0)
        view = {"frame": None, "lines": [], "stream": None, "age": 0.0,
                "reason": "waiting for graspnet"}
        reader = asyncio.create_task(read_latest(system, view))
        live, received = None, 0.0
        try:
            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            while True:
                try:
                    _, arrays, _ = await camera.read(components=("rgb",), max_age_s=1.0)
                    live = cv2.cvtColor(arrays["rgb"], cv2.COLOR_RGB2BGR)
                    received = time.monotonic()
                except (AgentError, TimeoutError):
                    pass
                if live is None or time.monotonic() - received > 1.0:
                    frame = np.zeros((480, 848, 3), np.uint8)
                    cv2.putText(frame, "waiting for camera", (20, 40), cv2.FONT_HERSHEY_SIMPLEX,
                                0.7, (0, 0, 255), 2, cv2.LINE_AA)
                else:
                    frame = live
                cv2.imshow(WINDOW, compose(frame, view))
                if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
                    break
                try:
                    if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        break
                except cv2.error:
                    break
                await asyncio.sleep(0.03)
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
            cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
