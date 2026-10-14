"""Finite WRS IK/collision/attachment probe in an isolated process; no hardware."""

import json

from tests.fixtures.grasping.virtual_data import data
from wrs_agent.env.fixed_camera import load_calibration
from wrs_agent.env.wrs import ROOT, VirtualModel
from wrs_agent.env.wrs_grasp import VirtualGrasp, match_scene
from wrs_agent.executor import SkillFailure
from wrs_agent.scene import load_scene
from wrs_agent.schemas import ObjectData

model = VirtualModel()
objects = model.load_scene(load_scene(ROOT / "tests/fixtures/grasping/virtual_scene.toml"))
calibration = load_calibration(ROOT / "tests/fixtures/grasping/virtual_camera.json")
sample = data()
target = match_scene(sample.segment, calibration, objects)
grasp = VirtualGrasp(model)
before = model.robot.qs.copy()
count = grasp.plan(sample, calibration, target)
model.wrs.np.testing.assert_allclose(model.robot.qs, before)
for index in range(count):
    grasp.step(index)
assert grasp.verify()

blocked = VirtualModel()
scene = load_scene(ROOT / "tests/fixtures/grasping/virtual_scene.toml")
scene["wall"] = ObjectData(source="configuration", pos=[.4, .1, .105],
                           rotmat=[[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]],
                           geometry={"kind": "box", "xyz_lengths": [.15, .15, .012]})
blocked.load_scene(scene)
failed = VirtualGrasp(blocked)
before = blocked.robot.qs.copy()
try:
    failed.plan(sample, calibration, "box")
    raise AssertionError("wall must reject the grasp")
except SkillFailure:
    assert failed.held is None
    blocked.wrs.np.testing.assert_allclose(blocked.robot.qs, before)
print(json.dumps({"wrs_virtual_grasp": "PASS", "collision_rejection": "PASS",
                  "waypoints": count, "hardware": False}))
