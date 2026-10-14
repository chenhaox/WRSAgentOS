"""Own a camera worker, retain recent complete frames, expose read-only Zenoh services."""

import asyncio
import threading
import time
from typing import Literal

from pydantic import Field, model_validator

from wrs_agent.errors import AgentError
from wrs_agent.instance_lock import InstanceLock
from wrs_agent.nodes.camera.frames import FrameCache, FrameRequest, Observation, pack_frame
from wrs_agent.nodes.node import Node
from wrs_agent.schemas import Boundary, Empty, new_id


class CameraOptions(Boundary):
    backend: Literal["synthetic", "realsense"] = "synthetic"
    serial: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9-]*$")
    width: int = Field(default=848, ge=16, le=1280)
    height: int = Field(default=480, ge=16, le=720)
    fps: Literal[5, 10, 15, 30] = 15
    allow_hardware: bool = False

    @model_validator(mode="after")
    def explicit_hardware(self):
        if self.backend == "realsense" and not self.allow_hardware:
            raise ValueError("realsense_requires_hardware_opt_in")
        return self


class CameraNode(Node):
    node_type = "camera"
    features = ("camera_rgb_depth_points_v1",)
    options_type = CameraOptions

    async def setup(self):
        self.cache = FrameCache()
        self.health = "connecting"
        self.reason = ""
        self.sequence = 0
        self.stream_id = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._changed = asyncio.Event()
        self._pending = None
        self._notified = False
        self._loop = asyncio.get_running_loop()
        self.transport.register_handler("request/camera/status", self.status)
        self.transport.register_handler("request/camera/frame", self.read_frame, binary=True)
        self._worker = threading.Thread(target=self._capture, name="camera-capture", daemon=True)
        self._worker.start()
        self.on_close(self._close_capture)
        self.spawn(self._pump())

    def _driver(self):
        options = self.options
        if options.backend == "realsense":
            from wrs_agent.drivers.cameras.realsense import RealSenseCamera

            return RealSenseCamera(options.serial, width=options.width,
                                   height=options.height, fps=options.fps)
        from wrs_agent.drivers.cameras.synthetic import SyntheticCamera

        return SyntheticCamera(width=options.width, height=options.height, fps=options.fps)

    def _offer(self, value):
        with self._lock:
            self._pending = value  # Only one pending frame, even if the loop is busy.
            if not self._notified:
                self._notified = True
                self._loop.call_soon_threadsafe(self._changed.set)

    def _capture(self):
        driver, device_lock = None, None
        try:
            while not self._stop.is_set():
                try:
                    if driver is None:
                        driver = self._driver()
                    if self.options.backend == "realsense" and device_lock is None:
                        # Resolve in this worker, then lock the actual device before opening it.
                        device_lock = InstanceLock("realsense-" + driver.select_serial())
                    driver.open()
                    stream_id = new_id()  # Reconnection invalidates the old observation identity.
                    while not self._stop.is_set():
                        frame = driver.read()
                        self._offer((stream_id, frame, time.monotonic(), ""))
                except ImportError:
                    self._offer((None, None, 0, "camera_dependency_missing"))
                    return
                except Exception:
                    self._offer((None, None, 0, "camera_disconnected"))
                finally:
                    if driver is not None:
                        try:
                            driver.close()
                        except Exception:
                            self._offer((None, None, 0, "camera_close_failed"))
                self._stop.wait(2.0)
        finally:
            if device_lock is not None:
                device_lock.close()

    async def _close_capture(self):
        self._stop.set()
        await asyncio.to_thread(self._worker.join, 3.0)
        self.cache.clear()
        if self._worker.is_alive():
            raise RuntimeError("camera_worker_did_not_stop")

    async def _pump(self):
        while True:
            await self._changed.wait()
            with self._lock:
                item, self._pending = self._pending, None
                self._changed.clear()
                self._notified = False
            stream_id, frame, received, reason = item
            if frame is None:
                self.cache.clear()
                self.health, self.reason = "disconnected", reason
                continue
            if stream_id != self.stream_id:
                self.cache.clear()
                self.stream_id, self.sequence = stream_id, 0
            self.sequence += 1
            info = Observation(
                observation_id=f"{stream_id}.{self.sequence}", stream_id=stream_id,
                sequence=self.sequence, captured_at_ns=frame.captured_at_ns,
                source=frame.source, frame_id=frame.frame_id,
                width=frame.depth.shape[1], height=frame.depth.shape[0],
                calibration=frame.calibration, alignment=frame.alignment,
            )
            self.cache.put(info, frame, received)
            self.health, self.reason = "ready", ""
            self.transport.publish("stream/camera/observation", info.model_dump())

    async def status(self, payload):
        Empty.model_validate(payload)
        info, age = None, None
        state, reason = self.health, self.reason
        if state == "ready":
            try:
                info, _, age = self.cache.get(FrameRequest())
            except AgentError as exc:
                state, reason = "stale", exc.code
        return {"state": state, "reason": reason, "age_s": age,
                "observation": info.model_dump() if info else None,
                "cache_bytes": self.cache.nbytes}

    async def read_frame(self, payload):
        request = FrameRequest.model_validate(payload)
        info, frame, age = self.cache.get(request)
        # Keep this chosen frame alive for this bounded query; serialize outside the control loop.
        return await asyncio.to_thread(pack_frame, info, frame, request.components, age)
