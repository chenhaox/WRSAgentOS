"""Read-only browser preview. Network and encoding work never run in the WRS scene loop."""

import asyncio
import base64
import contextlib
import io
import json
import os
import time
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

import numpy as np
from PIL import Image

from wrs_agent.errors import AgentError
from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.nodes.grasp.data import LatestGrasps, read_grasps
from wrs_agent.transport import Transport

COMPONENTS = {"rgb": ("rgb",), "depth": ("depth",), "points": ("rgb", "points")}
MAX_PREVIEW_POINTS = 8192


def _image(array, format):
    image = Image.fromarray(array)
    image.thumbnail((400, 300))
    buffer = io.BytesIO()
    image.save(buffer, format=format)
    mime = "jpeg" if format == "JPEG" else "png"
    return f"data:image/{mime};base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def make_preview(info, arrays, view):
    result = {
        "state": "ready", "reason": "", "observation_id": info.observation_id,
        "source": info.source, "frame_id": info.frame_id,
        "width": info.width, "height": info.height, "view": view,
    }
    if view == "rgb":
        result["rgb"] = _image(arrays["rgb"], "JPEG")
    elif view == "depth":
        depth = arrays["depth"].astype(np.float32) * info.calibration.depth_scale_m
        shade = (np.clip(depth / 0.6, 0, 1) * 255).astype(np.uint8)
        # Fixed 0–0.6 m scale: colors do not change as objects enter the image.
        colored = np.stack((255 - shade, shade, np.full_like(shade, 160)), axis=-1)
        colored[depth <= 0] = 0
        result["depth"] = _image(colored, "PNG")
    elif view == "points":
        stride = max(1, int(np.ceil(np.sqrt(info.width * info.height / MAX_PREVIEW_POINTS))))
        # Subsample both axes; a flattened stride aliases into stripes on regular RGB-D grids.
        xyz = arrays["points"][::stride, ::stride].reshape(-1, 3)[:MAX_PREVIEW_POINTS]
        rgb = arrays["rgb"][::stride, ::stride].reshape(-1, 3)[:MAX_PREVIEW_POINTS]
        valid = np.isfinite(xyz).all(axis=1) & (xyz[:, 2] > 0)
        result.update(points=np.round(xyz[valid], 5).tolist(), colors=rgb[valid].tolist())
    else:
        raise ValueError("invalid_preview_view")
    return result


class CameraPreview:
    def __init__(self, *, endpoint, env_id, site="local", node_id="camera", fps=30, point_fps=5,
                 grasp_node_id="graspnet"):
        if not 1 <= fps <= 30 or not 1 <= point_fps <= 10:
            raise ValueError("invalid_preview_fps")
        self.connection = endpoint, site, env_id + "-" + node_id
        self.target = {"endpoint": endpoint, "site": site, "env_id": env_id, "node_id": node_id}
        self.grasp_connection = endpoint, site, env_id + "-" + grasp_node_id
        self.grasp_node_id = grasp_node_id
        self.rates = {"rgb": fps, "depth": fps, "points": point_fps, "grasps": 2}
        self.latest, self.observed_at, self.requested_at = {}, {}, {}
        self.checked_at = {}
        self.next_read = dict.fromkeys(self.rates, 0.0)
        self.changed = asyncio.Event()
        self.script = files(__package__).joinpath("panel.js").read_bytes()
        self.scene_script = files(__package__).joinpath("scene.js").read_bytes()

    async def start(self):
        self.bus = Transport(*self.connection, os.environ.get("WRS_AGENT_TOKEN", ""), "camera-ui")
        try:
            self.grasp_bus = Transport(*self.grasp_connection,
                                       os.environ.get("WRS_AGENT_TOKEN", ""), "grasp-ui")
        except BaseException:
            await self.bus.close()
            raise
        self.client = CameraClient(self.bus)
        self.worker = asyncio.create_task(self._run())

    async def close(self):
        self.worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.worker
        await self.bus.close()
        await self.grasp_bus.close()

    async def _update_grasps(self):
        began = time.monotonic()
        ref = LatestGrasps.model_validate(await self.grasp_bus.request(
            "request/grasp/latest", {}, timeout=.5,
        ))
        previous = self.latest.get("grasps", {})
        observed_at = began - ref.age_s  # Conservatively include the latest query's RTT.
        if previous.get("candidate_set_id") != ref.candidate_set_id:
            data, age = await read_grasps(self.grasp_bus, ref.candidate_set_id, ref.task_id)
            info = data.info.segment.observation
            if info.observation_id != ref.observation_id:
                raise ValueError("grasp_observation_mismatch")
            observed_at = time.monotonic() - age
            value = await asyncio.to_thread(make_preview, info, data.segment.arrays, "points")
            value.update(view="grasps", candidate_set_id=ref.candidate_set_id,
                         task_id=ref.task_id, target=data.info.segment.target,
                         grasp_count=len(data.info.candidates),
                         grasps=[c.model_dump() for c in
                                 sorted(data.info.candidates, key=lambda c: -c.score)[:8]])
            self.latest["grasps"] = value
        self.observed_at["grasps"] = observed_at

    async def _update(self, view):
        began = time.monotonic()
        try:
            if view == "grasps":
                await self._update_grasps()
            else:
                info, arrays, age = await self.client.read(
                    components=COMPONENTS[view], max_age_s=1.0,
                )
                observed_at = time.monotonic() - age
                if age > 1.5:
                    raise AgentError("camera_stale")
                previous = self.latest.get(view, {})
                if previous.get("observation_id") != info.observation_id:
                    self.latest[view] = await asyncio.to_thread(make_preview, info, arrays, view)
                self.observed_at[view] = observed_at
        except TimeoutError:
            reason = "grasp_node_unavailable" if view == "grasps" else "camera_node_unavailable"
            self.latest[view] = {"state": "offline", "reason": reason}
        except AgentError as exc:
            self.latest[view] = {"state": "unavailable", "reason": exc.code}
            # Only ask for health when there is no usable frame, not on every successful read.
            if view != "grasps":
                with contextlib.suppress(AgentError, TimeoutError):
                    status = await self.client.status()
                    if status["state"] != "ready":
                        self.latest[view] = {"state": status["state"], "reason": status["reason"]}
        except (ValueError, OSError):
            self.latest[view] = {"state": "unavailable", "reason": "invalid_camera_frame"}
        interval = 1 / self.rates[view] if self.latest[view]["state"] == "ready" else .5
        self.checked_at[view] = time.monotonic()
        self.next_read[view] = max(began + interval, time.monotonic() + .001)

    async def _run(self):
        while True:
            self.changed.clear()
            now = time.monotonic()
            # Four bounded views, at most one request/encoding job in flight.
            active = [view for view, requested in self.requested_at.items()
                      if now - requested < 1.5]
            for view in active:
                if time.monotonic() >= self.next_read[view]:
                    await self._update(view)
            for view in self.rates.keys() - set(active):
                self.latest.pop(view, None)
                self.observed_at.pop(view, None)
                self.checked_at.pop(view, None)
            delay = max(.001, min((self.next_read[v] - time.monotonic() for v in active),
                                 default=.5))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), timeout=delay)

    def http_response(self, path, original):
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        def response(body, content_type):
            return Response(200, "OK", Headers({
                "Content-Type": content_type, "Content-Length": str(len(body)),
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            }), body)

        url = urlsplit(path)
        path = url.path
        if path == "/camera/panel.js":
            return response(self.script, "text/javascript; charset=utf-8")
        if path == "/camera/scene.js":
            return response(self.scene_script, "text/javascript; charset=utf-8")
        if path == "/camera/preview":
            view = parse_qs(url.query).get("view", ["rgb"])[0]
            if view not in self.rates:
                return response(b'{"state":"unavailable","reason":"invalid_preview_view"}',
                                "application/json")
            self.requested_at[view] = time.monotonic()
            self.changed.set()
            value = self.latest.get(view, {"state": "connecting", "reason": ""})
            if value["state"] == "ready":
                age = time.monotonic() - self.observed_at[view]
                limit = 30 if view == "grasps" else 1.5
                if age > limit or time.monotonic() - self.checked_at[view] > 1.5:
                    value = {"state": "stale", "reason": "camera_preview_stale"}
                else:
                    value = {**value, "age_s": age}
            value = {**value, "view": view, "preview_fps": self.rates[view],
                     "grasp_node_id": self.grasp_node_id,
                     "connection": self.target}  # Public routing data, never the token.
            return response(json.dumps(value, allow_nan=False).encode(), "application/json")
        if path in {"/", "/index.html"} and original.status_code == 200:
            body = original.body.replace(
                b"</body>", b'<script type="module" src="/camera/panel.js"></script></body>',
            )
            return response(body, "text/html; charset=utf-8")
        return original
