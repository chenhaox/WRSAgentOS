"""Read-only browser preview. Network and encoding work never run in the WRS scene loop."""

import asyncio
import base64
import contextlib
import io
import json
import os
import time
from importlib.resources import files

import numpy as np
from PIL import Image

from wrs_agent.errors import AgentError
from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.transport import Transport


def _image(array, format):
    image = Image.fromarray(array)
    image.thumbnail((400, 300))
    buffer = io.BytesIO()
    image.save(buffer, format=format)
    mime = "jpeg" if format == "JPEG" else "png"
    return f"data:image/{mime};base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def make_preview(info, arrays):
    depth = arrays["depth"].astype(np.float32) * info.calibration.depth_scale_m
    shade = (np.clip(depth / 0.6, 0, 1) * 255).astype(np.uint8)
    # Fixed 0–0.6 m scale: colors do not change just because another object enters the image.
    colored = np.stack((255 - shade, shade, np.full_like(shade, 160)), axis=-1)
    colored[depth <= 0] = 0
    xyz = arrays["points"].reshape(-1, 3)
    rgb = arrays["rgb"].reshape(-1, 3)
    stride = max(1, (len(xyz) + 2047) // 2048)
    xyz, rgb = xyz[::stride], rgb[::stride]
    valid = np.isfinite(xyz).all(axis=1) & (xyz[:, 2] > 0)
    return {
        "state": "ready", "reason": "", "observation_id": info.observation_id,
        "source": info.source, "frame_id": info.frame_id,
        "width": info.width, "height": info.height,
        "rgb": _image(arrays["rgb"], "JPEG"), "depth": _image(colored, "PNG"),
        "points": np.round(xyz[valid], 5).tolist(), "colors": rgb[valid].tolist(),
    }


class CameraPreview:
    def __init__(self, *, endpoint, env_id, site="local", node_id="camera"):
        self.connection = endpoint, site, env_id + "-" + node_id
        self.target = {"endpoint": endpoint, "site": site, "env_id": env_id, "node_id": node_id}
        self.latest = {"state": "connecting", "reason": ""}
        self.updated_at = time.monotonic()
        self.script = files(__package__).joinpath("panel.js").read_bytes()

    async def start(self):
        self.bus = Transport(*self.connection, os.environ.get("WRS_AGENT_TOKEN", ""), "camera-ui")
        self.client = CameraClient(self.bus)
        self.worker = asyncio.create_task(self._run())

    async def close(self):
        self.worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.worker
        await self.bus.close()

    async def _run(self):
        while True:
            try:
                status = await self.client.status()
                if status["state"] != "ready":
                    self.latest = {"state": status["state"], "reason": status["reason"]}
                else:
                    info, arrays, age = await self.client.read(max_age_s=1.0)
                    if age > 1.5:
                        raise AgentError("camera_stale")
                    self.latest = await asyncio.to_thread(make_preview, info, arrays)
                self.updated_at = time.monotonic()
            except TimeoutError:
                self.latest = {"state": "offline", "reason": "camera_node_unavailable"}
            except AgentError as exc:
                self.latest = {"state": "unavailable", "reason": exc.code}
            except (ValueError, OSError):
                self.latest = {"state": "unavailable", "reason": "invalid_camera_frame"}
            await asyncio.sleep(0.25)

    def http_response(self, path, original):
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        def response(body, content_type):
            return Response(200, "OK", Headers({
                "Content-Type": content_type, "Content-Length": str(len(body)),
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            }), body)

        path = path.split("?", 1)[0]
        if path == "/camera/panel.js":
            return response(self.script, "text/javascript; charset=utf-8")
        if path == "/camera/preview":
            value = self.latest
            if value["state"] == "ready" and time.monotonic() - self.updated_at > 1.5:
                value = {"state": "stale", "reason": "camera_preview_stale"}
            value = {**value, "connection": self.target}  # Public routing data, never the token.
            return response(json.dumps(value, allow_nan=False).encode(), "application/json")
        if path in {"/", "/index.html"} and original.status_code == 200:
            body = original.body.replace(
                b"</body>", b'<script type="module" src="/camera/panel.js"></script></body>',
            )
            return response(body, "text/html; charset=utf-8")
        return original
