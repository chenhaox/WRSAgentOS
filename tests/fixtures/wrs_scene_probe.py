"""Actual WRS scene construction and independent viewer synchronization, without a GPU window."""

import json

from wrs_agent.env.wrs import ROOT, VirtualModel, sync_scene_objects
from wrs_agent.scene import load_scene
from wrs_agent.schemas import ObjectData

model = VirtualModel()
wrs, np = model.wrs, model.wrs.np
configured = load_scene(ROOT / "examples/wrs/scene.toml")
configured["A"] = configured["A"].model_copy(
    update={"rotmat": [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]})
configured["unknown"] = ObjectData(source="configuration", pos=[0.1, 0.2, 0.3])
snapshot = model.load_scene(configured)
assert model.robot in model.scene.mecbas and len(model.scene.sobjs) == 3
for name in ("table", "A", "plate"):
    obj = model.objects[name][1]
    np.testing.assert_allclose(obj.pos, configured[name].pos, atol=1e-6)
    np.testing.assert_allclose(obj.rotmat, configured[name].rotmat, atol=1e-6)
    np.testing.assert_allclose(snapshot[name].pos, obj.pos, atol=1e-6)
assert snapshot["unknown"].rotmat is None and "unknown" not in model.objects

viewer_scene, displayed = wrs.wss.Scene(), {}
sync_scene_objects(wrs, viewer_scene, snapshot, displayed)
assert len(viewer_scene.sobjs) == 3
original = displayed["A"][1]
sync_scene_objects(wrs, viewer_scene, snapshot, displayed)
assert displayed["A"][1] is original  # Repeated query must not rebuild geometry.
changed = {"A": snapshot["A"].model_copy(update={"pos": [0.4, 0.0, 0.03]})}
sync_scene_objects(wrs, viewer_scene, changed, displayed)
assert set(displayed) == {"A"} and len(viewer_scene.sobjs) == 1
np.testing.assert_allclose(displayed["A"][1].pos, [0.4, 0.0, 0.03], atol=1e-6)
np.testing.assert_allclose(model.objects["A"][1].pos, configured["A"].pos, atol=1e-6)
changed["A"] = changed["A"].model_copy(update={"valid": False})
sync_scene_objects(wrs, viewer_scene, changed, displayed)
assert not displayed and not viewer_scene.sobjs
print(json.dumps({"wrs_scene": "PASS", "viewer_sync": "PASS", "hardware": False}))
