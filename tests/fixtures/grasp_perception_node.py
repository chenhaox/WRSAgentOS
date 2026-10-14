"""Offline model fixtures for the production SAM and GraspNet node lifecycles."""

import asyncio
import sys
import time

import numpy as np

from wrs_agent.nodes.grasp import GraspNetNode
from wrs_agent.nodes.grasp.graspnet import candidates_from_rows, sample_target
from wrs_agent.nodes.segmentation import Sam3Node
from wrs_agent.nodes.serve import serve_node


class FixtureSam:
    name, sha256 = "offline-sam-fixture", "0" * 64
    score_kind = "fixture"

    def segment(self, rgb, target, selection):
        time.sleep(.3)  # The camera's two-frame cache will have advanced during inference.
        if target == "ambiguous":
            raise ValueError("target_ambiguous")
        height, width = rgb.shape[:2]
        mask = np.zeros((height, width), dtype=bool)
        mask[height // 3:2 * height // 3, width // 3:2 * width // 3] = True
        return mask, .9

    def segment_box(self, rgb, box):
        time.sleep(.3)
        mask = np.zeros(rgb.shape[:2], dtype=bool)
        x1, y1, x2, y2 = map(int, box)
        mask[y1:y2, x1:x2] = True
        return mask, .9


class FixtureGrasp:
    name, sha256, revision = "offline-grasp-fixture", "0" * 64, "0" * 40

    def infer(self, points, mask):
        center = sample_target(points, mask).mean(axis=0)
        row = np.array([.9, .04, .02, .03, *np.eye(3).reshape(-1), *center, -1.])
        return candidates_from_rows(row[None], max_width_m=.05)


class FixtureSamNode(Sam3Node):
    def _segmenter(self):
        model = FixtureSam()
        if self.options.backend == "fastsam":
            model.name, model.score_kind = "offline-clip-fixture", "clip_cosine"
            model.text_sha256 = "1" * 64
        if self.options.backend == "fastsam-box":
            model.name, model.score_kind = "offline-box-fixture", "box_iou"
        return model


class FixtureGraspNode(GraspNetNode):
    def _predictor(self):
        return FixtureGrasp()


if __name__ == "__main__":
    kind, endpoint, env_id = sys.argv[1:4]
    options = {"weights": "unused-fixture.pt", "weights_sha256": "0" * 64}
    if kind == "graspnet":
        options.update(source="unused-fixture", source_revision="0" * 40,
                       segmentation=sys.argv[4] if len(sys.argv) > 4 else "sam3")
    if kind == "segmentation":
        options.update(backend="fastsam", clip_weights="unused-clip-fixture.pt")
    if kind == "segmentation-box":
        options.update(backend="fastsam-box")
    asyncio.run(serve_node(FixtureGraspNode if kind == "graspnet" else FixtureSamNode,
                           node_id=kind, endpoint=endpoint, env_id=env_id, options=options))
