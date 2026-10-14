"""Virtual camera/models and the real WRS grasp backend, with ordinary node protocols."""

import asyncio
import sys
import time

import numpy as np

from tests.fixtures.grasping.virtual_data import candidates, frame
from wrs_agent.nodes.camera import CameraNode
from wrs_agent.nodes.grasp import GraspNetNode
from wrs_agent.nodes.segmentation import Sam3Node
from wrs_agent.nodes.serve import serve_node
from wrs_agent.nodes.vision import YoloNode
from wrs_agent.nodes.vision.results import Object2D
from wrs_agent.nodes.wrs import WrsNode
from wrs_agent.processes import ROOT


class VirtualCamera:
    def open(self):
        self.next_frame = time.monotonic()

    def read(self):
        time.sleep(max(0, self.next_frame - time.monotonic()))
        self.next_frame = time.monotonic() + 1 / 15
        return frame()

    def close(self):
        pass


class Camera(CameraNode):
    def _driver(self):
        return VirtualCamera()


class Sam:
    name, sha256 = "virtual-sam-fixture", "0" * 64
    score_kind = "fixture"

    def segment(self, rgb, target, selection):
        assert target == "box"
        return rgb[:, :, 0] == 255, .9

    def segment_box(self, rgb, box):
        assert box == [12., 8., 20., 16.]
        return rgb[:, :, 0] == 255, .9


class SamNode(Sam3Node):
    def _segmenter(self):
        return Sam()


class Detector:
    name, sha256, text_sha256 = "virtual-detector-fixture", "0" * 64, None
    labels = ["box"]

    def detect(self, rgb):
        assert int(np.count_nonzero(rgb[:, :, 0])) == 64
        return [Object2D(instance_id="0", label="box", score=.9, box=[12., 8., 20., 16.])]

    def locate(self, rgb, target):
        assert target == "box"
        return self.detect(rgb)


class DetectorNode(YoloNode):
    def _detector(self):
        return Detector()


class Grasp:
    name, sha256, revision = "virtual-grasp-fixture", "0" * 64, "0" * 40

    def infer(self, points, mask):
        assert int(mask.sum()) == 64
        return candidates()


class GraspNode(GraspNetNode):
    def _predictor(self):
        return Grasp()


if __name__ == "__main__":
    kind, endpoint, env_id = sys.argv[1:4]
    options = {"weights": "unused.pt", "weights_sha256": "0" * 64}
    if kind == "camera":
        options = {"width": 32, "height": 24, "fps": 15}
    if kind == "graspnet":
        options.update(source="unused", source_revision="0" * 40,
                       segmentation=sys.argv[4] if len(sys.argv) > 4 else "sam3")
    if kind == "segmentation":
        options.update(backend="fastsam-box")
    if kind == "wrs":
        options = {
            "backend": "wrs-grasp", "scene": ROOT / "tests/fixtures/grasping/virtual_scene.toml",
            "grasp_calibration": ROOT / "tests/fixtures/grasping/virtual_camera.json",
        }
    asyncio.run(serve_node({"camera": Camera, "sam3": SamNode, "graspnet": GraspNode,
                            "yolo": DetectorNode, "segmentation": SamNode,
                            "wrs": WrsNode}[kind], node_id=kind, endpoint=endpoint, env_id=env_id,
                           suffix="" if kind == "wrs" else "-" + kind, options=options))
