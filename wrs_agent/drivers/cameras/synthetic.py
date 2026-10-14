"""Explicit synthetic input for examples/tests; never presented as a real camera."""

import time

import numpy as np

from wrs_agent.drivers.cameras.frame import CameraFrame


class SyntheticCamera:
    def __init__(self, *, width=320, height=240, fps=10):
        self.width, self.height, self.fps = width, height, fps
        self.sequence = 0

    def open(self):
        self.deadline = time.monotonic()

    def read(self):
        time.sleep(max(0, self.deadline - time.monotonic()))
        self.deadline = time.monotonic() + 1 / self.fps
        self.sequence += 1
        h, w = self.height, self.width
        v, u = np.mgrid[:h, :w]
        rgb = np.empty((h, w, 3), dtype=np.uint8)
        rgb[:, :, 0] = u * 255 // w
        rgb[:, :, 1] = v * 255 // h
        rgb[:, :, 2] = self.sequence % 256
        depth = np.full((h, w), 350, dtype="<u2")
        depth[h // 3:2 * h // 3, w // 3:2 * w // 3] = 250
        z = depth.astype("<f4") * 0.001
        points = np.stack(((u - w / 2) * z / w, (v - h / 2) * z / w, z), axis=-1)
        return CameraFrame(
            rgb, depth, points.astype("<f4"), time.time_ns(),
            {"fx": float(w), "fy": float(w), "cx": w / 2, "cy": h / 2,
             "distortion": "none", "coefficients": [0.0] * 5, "depth_scale_m": 0.001},
            "synthetic-depth-optical", "synthetic",
        )

    def close(self):
        """No device is acquired by the synthetic source."""
