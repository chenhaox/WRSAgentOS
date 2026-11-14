"""Computed pour/shake_off/stir/scoop in an isolated WRS process; no hardware."""

import json
import time

from wrs_agent.env.wrs import ROOT, VirtualModel
from wrs_agent.env.wrs_motion import VirtualKitchen
from wrs_agent.executor import SkillFailure
from wrs_agent.scene import load_scene
from wrs_agent.schemas import ObjectData

model = VirtualModel()
model.load_scene(load_scene(ROOT / "tests/fixtures/kitchen_scene.toml"))
kitchen = VirtualKitchen(model)
np = model.wrs.np
report = {}


def run(name, plan, *args):
    """Play the plan and return the most-tilted uprightness and lowest bottom of the object."""
    started = time.monotonic()
    count = plan(*args)
    upright, bottom = 1., float("inf")
    for index in range(count):
        _, data, _ = kitchen.step(index)
        upright = min(upright, data.rotmat[2][2])
        bottom = min(bottom, data.pos[2] - data.geometry.xyz_lengths[2] / 2)
    assert kitchen.verify(), name
    report[name] = {"steps": count, "planning_s": round(time.monotonic() - started, 2)}
    return upright, bottom


def top(name):
    data = model.object_data(name)
    return data.pos[2] + data.geometry.xyz_lengths[2] / 2


def rejects(reason, plan, *args):
    before = model.robot.qs.copy()
    try:
        plan(*args)
    except SkillFailure as exc:
        assert str(exc) == reason, (reason, str(exc))
        np.testing.assert_allclose(model.robot.qs, before)
        return
    raise AssertionError(f"expected {reason}")


rejects("source_not_held", kitchen.plan_pour, "cup", "bowl")
run("pick_cup", kitchen.plan_pick, "cup")
held_at = model.object_data("cup").pos
upright, bottom = run("pour", kitchen.plan_pour, "cup", "bowl")
# Turned past horizontal (opening facing down) while staying above the bowl's rim.
assert upright < 0 and bottom > top("bowl"), (upright, bottom)
cup = model.object_data("cup")
# Back upright above the bowl, still held; the bowl itself never moves.
assert kitchen.held == "cup" and np.asarray(cup.rotmat)[2, 2] > .999
assert np.allclose(cup.pos[:2], model.object_data("bowl").pos[:2], atol=2e-3)
assert cup.pos[2] > held_at[2] - .1
run("shake_off", kitchen.plan_shake_off, "cup")
rejects("tool_not_held", kitchen.plan_stir, "spoon", "pot")
run("place_cup", kitchen.plan_place, "table")

run("pick_spoon", kitchen.plan_pick, "spoon")
rejects("vessel_too_small_for_tool", kitchen.plan_stir, "spoon", "cup")
upright, bottom = run("stir", kitchen.plan_stir, "spoon", "pot")
# The spoon's tip went below the rim but stayed above the pot's floor.
pot = model.object_data("pot")
assert upright > .999 and pot.pos[2] - pot.geometry.xyz_lengths[2] / 2 < bottom < top("pot")
upright, bottom = run("scoop", kitchen.plan_scoop, "spoon", "pot", "bowl")
assert upright < .01 and bottom < top("pot"), (upright, bottom)
assert kitchen.held == "spoon"

blocked = VirtualModel()
scene = load_scene(ROOT / "tests/fixtures/kitchen_scene.toml")
bowl = scene["bowl"]
scene["shelf"] = ObjectData(source="configuration", pos=[bowl.pos[0], bowl.pos[1], .09],
                            rotmat=bowl.rotmat,
                            geometry={"kind": "box", "xyz_lengths": [.3, .3, .01]})
blocked.load_scene(scene)
shelved = VirtualKitchen(blocked)
count = shelved.plan_pick("cup")
for index in range(count):
    shelved.step(index)
before = blocked.robot.qs.copy()
try:
    shelved.plan_pour("cup", "bowl")
    raise AssertionError("shelf must block the pour")
except SkillFailure:
    np.testing.assert_allclose(blocked.robot.qs, before)
    assert shelved.held == "cup"
report["collision_rejection"] = "PASS"
print(json.dumps({"wrs_computed_motion": "PASS", "hardware": False, **report}))
