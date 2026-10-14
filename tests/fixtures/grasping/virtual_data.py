"""虚拟方块/桌面数据；不代表模型推理或物理测量。"""

import time

import numpy as np

from wrs_agent.drivers.cameras.frame import CameraFrame
from wrs_agent.nodes.camera.frames import Observation
from wrs_agent.nodes.grasp.data import GraspData, GraspSet
from wrs_agent.nodes.grasp.graspnet import candidates_from_rows
from wrs_agent.nodes.segmentation.data import SegmentData, SegmentInfo
from wrs_agent.nodes.vision.artifacts import ArtifactOwner


def frame():
    height, width = 24, 32
    depth = np.full((height, width), 400, dtype="<u2")
    depth[8:16, 12:20] = 330
    v, u = np.mgrid[:height, :width]
    z = depth.astype("<f4") * .001
    points = np.stack(((u - 16) * z / 320, (v - 12) * z / 320, z), axis=-1).astype("<f4")
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[8:16, 12:20] = [255, 0, 0]
    return CameraFrame(rgb, depth, points, time.time_ns(),
                       dict(fx=320., fy=320., cx=16., cy=12., distortion="none",
                            coefficients=[0.] * 5, depth_scale_m=.001),
                       "virtual-depth-optical", "synthetic")


def candidates():
    rotation = np.array([[0., 1., 0.], [0., 0., 1.], [1., 0., 0.]])
    row = np.array([.9, .012, .02, .03, *rotation.reshape(-1), 0., 0., .35, -1.])
    return candidates_from_rows(row[None], max_width_m=.05)


def data(task_id="task"):
    sample = frame()
    observation = Observation(observation_id="fixture.1", stream_id="fixture", sequence=1,
                              source=sample.source, frame_id=sample.frame_id, width=32, height=24,
                              captured_at_ns=sample.captured_at_ns, calibration=sample.calibration)
    owner = ArtifactOwner(node_id="fixture", task_id=task_id, action_id="action",
                          boot_id="boot", control_epoch=0)
    mask = sample.depth < 400
    segment = SegmentData(SegmentInfo(segment_id="segment", owner=owner, observation=observation,
                                      target="box", model="virtual-fixture",
                                      weights_sha256="0" * 64, score=.9, pixel_count=64),
                          {name: getattr(sample, name) for name in ("rgb", "depth", "points")},
                          mask)
    return GraspData(GraspSet(candidate_set_id="candidate", owner=owner, segment=segment.info,
                              model="virtual-fixture", weights_sha256="0" * 64,
                              source_revision="0" * 40, candidates=candidates()), segment)
