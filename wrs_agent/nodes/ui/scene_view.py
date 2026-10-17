"""Optional WRS 3D page drawn from the robot node's snapshots; it never sends motion."""

import asyncio
import os
import socket
import threading

from wrs_agent.env.wrs import load_wrs, serve_viewer_hub, sync_scene_objects


def _port_in_use(port):
    with socket.socket() as probe:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _bypass_proxy_for_loopback():
    # websockets honours HTTP(S)_PROXY; a system proxy would swallow ws://127.0.0.1/publish.
    for name in ("NO_PROXY", "no_proxy"):
        hosts = [item for item in os.environ.get(name, "").split(",") if item]
        os.environ[name] = ",".join(dict.fromkeys([*hosts, "127.0.0.1", "localhost"]))


class SceneView:
    def __init__(self, dashboard, port):
        self.dashboard, self.port = dashboard, port
        self.world = None
        self._thread = None

    def _status(self, state, detail):
        self.dashboard.viewer = {"state": state, "detail": detail}

    async def run(self):
        url = f"http://127.0.0.1:{self.port}/"
        if _port_in_use(self.port):
            self._status("external", f"{self.port} 端口已有画面服务，直接嵌入它。")
            return
        self._status("starting", "正在加载 WRS 模型…")
        _bypass_proxy_for_loopback()
        try:
            wrs = await asyncio.to_thread(load_wrs)
            await asyncio.to_thread(self._build, wrs)
        except Exception as exc:
            self._status("failed", f"WRS 画面不可用：{type(exc).__name__}: {exc}")
            return
        hub = asyncio.create_task(serve_viewer_hub(self.port))
        try:
            for _ in range(50):
                if hub.done() or _port_in_use(self.port):
                    break
                await asyncio.sleep(0.1)
            if hub.done():
                await hub
                raise RuntimeError("viewer_hub_stopped")
            self._thread = threading.Thread(
                target=self.world.run, name="wrs-ui-viewer", daemon=True,
            )
            self._thread.start()
            self._status("ready", url)
            await hub
            self._status("failed", "WRS 画面服务已停止。")
        except Exception as exc:
            self._status("failed", f"WRS 画面服务失败：{type(exc).__name__}: {exc}")
        finally:
            self.world.close()
            hub.cancel()
            await asyncio.gather(hub, return_exceptions=True)

    def _build(self, wrs):
        world = wrs.wvw.World(
            cam_pos=(1.8, 1.4, 1.2), cam_lookat_pos=(0.3, 0, 0.35),
            port=self.port, hz=10, auto_start_hub=False,
        )
        world.set_caption("WRS-Agent · UI 节点")
        robot, gripper = wrs.ur_ur7e.ur7e_with_gripper()
        robot.add_to_scene(world.scene)
        wrs.wssop.frame().add_to_scene(world.scene)
        displayed = {}
        board = self.dashboard

        def update(dt):
            # Display only: the latest snapshot comes from the dashboard's own polling.
            snapshot = board.robot
            if snapshot is None or snapshot.data.kind != "scene":
                return
            try:
                state = snapshot.data.robot.kinematics
                if state is not None and state.valid:
                    robot.fk(state.qs)
                    if state.gripper_width is not None:
                        gripper.set_opening(state.gripper_width)
                sync_scene_objects(wrs, world.scene, snapshot.data.objects, displayed)
            except Exception as exc:
                self._status("failed", f"画面刷新失败：{type(exc).__name__}: {exc}")

        world.schedule_interval(update, 0.1)
        self.world = world

    async def close(self):
        if self.world is not None:
            self.world.close()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 2)
