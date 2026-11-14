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
            snapshot = await self._first_snapshot()
            wrs = await asyncio.to_thread(load_wrs)
            await asyncio.to_thread(self._build, wrs, snapshot)
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

    async def _first_snapshot(self):
        for _ in range(600):
            snapshot = self.dashboard.robot
            if snapshot is not None and snapshot.data.kind == "scene":
                return snapshot
            await asyncio.sleep(0.1)
        return None

    def _build(self, wrs, snapshot):
        facts = {} if snapshot is None else dict(snapshot.data.facts)
        objects = {} if snapshot is None else snapshot.data.objects
        fafu = facts.get("robot_model") == "fafu_baseV1"
        kitchen = "skimmer" in objects and "pot" in objects
        if fafu and kitchen:
            camera = ((1.65, -1.85, 1.55), (0.3, 0.0, 0.23))
        else:
            camera = ((1.8, 1.4, 1.2), (0.3, 0.0, 0.35))
        world = wrs.wvw.World(
            cam_pos=camera[0], cam_lookat_pos=camera[1],
            port=self.port, hz=10, auto_start_hub=False,
        )
        world.set_caption("WRS-Agent · 厨具技能" if kitchen else "WRS-Agent · UI 节点")
        jaw = None
        gripper = None
        if fafu:
            from wrs_agent.env.fafu import load_model
            from wrs_agent.env.kitchen_motion import _prepare_jaws, jaw_mesh

            loaded = load_model()
            robot = loaded[1]
            import tomllib
            from pathlib import Path

            import numpy as np

            profile = tomllib.loads(
                (Path(__file__).parents[2] / "assets/fafu/profile.toml").read_text(encoding="utf-8")
            )
            robot.set_pos_rotmat(pos=profile["base_pos"])
            joint_index = {joint.name: index for index, joint in enumerate(robot.structure.jnts)}
            joint_ids = [joint_index[f"joint{number}"] for number in range(1, 7)]

            def apply(q):
                # Display the simulated joints even when they sit outside the URDF limits.
                full = np.zeros_like(robot.qs)
                full[joint_ids] = q
                robot.fk(full)

            class _Arm:
                pass

            holder = _Arm()
            holder.robot = robot
            visual, open_geom, mid, closed = _prepare_jaws(holder)
            jaw = (visual, open_geom, mid, closed)
        else:
            robot, gripper = wrs.ur_ur7e.ur7e_with_gripper()
        robot.add_to_scene(world.scene)
        wrs.wssop.frame().add_to_scene(world.scene)
        displayed = {}
        board = self.dashboard

        def update(_dt):
            # Display only: the latest snapshot comes from the dashboard's own polling.
            current = board.robot
            if current is None or current.data.kind != "scene":
                return
            try:
                state = current.data.robot.kinematics
                if state is not None and state.valid:
                    if fafu:
                        apply(list(state.qs))
                        if jaw is not None:
                            visual.geom = jaw_mesh(
                                *jaw[1:],
                                float(current.data.facts.get("jaw") or 0.0),
                                float(current.data.facts.get("jaw_gap") or 0.016),
                            )
                    else:
                        robot.fk(state.qs)
                        if gripper is not None and state.gripper_width is not None:
                            gripper.set_opening(state.gripper_width)
                sync_scene_objects(wrs, world.scene, current.data.objects, displayed)
            except Exception as exc:
                self._status("failed", f"画面刷新失败：{type(exc).__name__}: {exc}")

        world.schedule_interval(update, 0.1)
        self.world = world

    async def close(self):
        if self.world is not None:
            self.world.close()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 2)
