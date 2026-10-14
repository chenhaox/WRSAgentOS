"""Small camera consumer; only selected components travel over Zenoh."""

import time

from wrs_agent.nodes.camera.frames import FrameRequest, unpack_frame


class CameraClient:
    def __init__(self, transport):
        self.transport = transport

    async def status(self):
        return await self.transport.request("request/camera/status", {}, timeout=0.5)

    async def read(self, *, observation_id=None, components=("rgb", "depth", "points"),
                   max_age_s=2.0):
        request = FrameRequest(observation_id=observation_id, components=list(components),
                               max_age_s=max_age_s)
        began = time.monotonic()
        payload = await self.transport.request_bytes(
            "request/camera/frame", request.model_dump(), timeout=1.0,
        )
        info, arrays, age = unpack_frame(payload)
        # Conservatively include the whole round trip; clocks need not agree across hosts.
        return info, arrays, age + time.monotonic() - began
