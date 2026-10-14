"""Offline detector fixture only; production YoloNode always loads local YOLO weights."""

import asyncio
import sys
import time

from wrs_agent.nodes.serve import serve_node
from wrs_agent.nodes.vision import YoloNode
from wrs_agent.nodes.vision.results import Object2D


class FixtureDetector:
    name = "offline-fixture"
    sha256 = "0" * 64
    text_sha256 = None
    labels = ["cup"]

    def detect(self, rgb):
        width, height = map(int, sys.argv[3:5]) if len(sys.argv) >= 5 else (320, 240)
        assert rgb.shape == (height, width, 3) and rgb.dtype.name == "uint8"
        return [Object2D(instance_id="0", label="cup", score=0.9, box=[20., 20., 80., 80.])]

    def locate(self, rgb, target):
        time.sleep(.3)  # Ensure the camera's two-frame cache advances while locating.
        return self.detect(rgb)


class FixtureVision(YoloNode):
    def _detector(self):
        return FixtureDetector()


if __name__ == "__main__":
    asyncio.run(serve_node(
        FixtureVision, node_id="yolo", endpoint=sys.argv[1], env_id=sys.argv[2],
        options={"weights": "unused-fixture.pt", "weights_sha256": "0" * 64},
    ))
