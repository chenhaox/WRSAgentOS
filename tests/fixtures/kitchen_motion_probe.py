"""Mesh-kitchen skills in an isolated WRS process; no hardware.

Food leaves the second bowl into the skimmer without the bowl touching it,
the skimmer sits in the pot without touching the pot, then after that seat
the skimmer is lifted out and poured into the empty bowl. The jaw starts
open and closes onto the handle, from above for a bowl and from the side
for the skimmer. Each pour aims one mouth at the
other, and the food rests on the inner floor. A board over the skimmer
rejects the pour.
"""

import json

import numpy as np

from wrs_agent.env.kitchen_motion import GRASPS, KitchenMotion, add_box
from wrs_agent.env.wrs import ROOT, VirtualModel
from wrs_agent.executor import SkillFailure
from wrs_agent.scene import load_scene

SCENE = ROOT / "wrs_agent/assets/kitchen/scene.toml"


def grasp_gap(kitchen):
    name = kitchen.held
    if name not in GRASPS:
        return None
    pos, rot = kitchen._pose(name)
    contact = pos + rot @ np.asarray(GRASPS[name][0], dtype=float)
    return float(np.linalg.norm(kitchen._tcp() - contact))


def jaw_limits(kitchen):
    jaws = [float(frame.get("jaw", 0.0)) for frame in kitchen.path]
    return min(jaws), max(jaws)


def mouth_alignment(kitchen, source, target):
    """Largest alignment of the source opening with the target mouth."""
    source_cavity = kitchen.collision.parts(source)[1]
    target_cavity = kitchen.collision.parts(target)[1]
    best = -1.0
    for frame in kitchen.path:
        source_pos, source_rot = frame["poses"].get(source, kitchen._pose(source))
        target_pos, target_rot = frame["poses"].get(target, kitchen._pose(target))
        opening = source_rot @ np.array([0.0, 0.0, 1.0])
        mouth = source_pos + source_rot @ source_cavity[0]
        other = target_pos + target_rot @ target_cavity[0]
        aim = other - mouth
        span = float(np.linalg.norm(aim))
        if span < 1e-6:
            continue
        best = max(best, float(np.dot(opening, aim / span)))
    return best


def assert_on_floor(kitchen, food, vessel):
    pos = kitchen._pose(food)[0]
    floor = kitchen.collision.floor_z(vessel, pos[:2])
    half = float(np.asarray(kitchen.model.objects[food][0].geometry.xyz_lengths, dtype=float)[2]) / 2
    assert floor is not None, (food, pos.tolist())
    assert abs(float(pos[2]) - (floor + half)) < 0.015, (food, float(pos[2]), floor, half)


def play(kitchen, name, arguments, gaps, clear=(), track=()):
    count = kitchen.plan_skill(name, arguments)
    jumps = []
    previous = {}
    span = {food: [] for food in track}
    for index in range(count):
        kitchen.step(index)
        gap = grasp_gap(kitchen)
        if gap is not None:
            gaps.append(gap)
        for left, right in clear:
            if kitchen._overlaps(left, right, {}):
                raise SkillFailure(f"{left}_hits_{right}")
        for food in ("bowl_food_1", "bowl_food_2", "bowl_food_3", "bowl_food_4"):
            pos = kitchen._pose(food)[0]
            if food in previous:
                jumps.append(float(np.linalg.norm(pos - previous[food])))
            previous[food] = pos
            if food in span:
                span[food].append(float(pos[2]))
    if not kitchen.verify():
        raise SkillFailure(f"{name}_not_verified")
    return count, max(jumps, default=0.0), span


def main():
    model = VirtualModel()
    model.load_scene(load_scene(SCENE))
    kitchen = KitchenMotion(model)
    foods = ["bowl_food_1", "bowl_food_2", "bowl_food_3", "bowl_food_4"]
    assert set(foods) <= set(kitchen.contents_of("bowl_support_2"))
    gaps = []

    rack_before = kitchen._pose("bowl_rack_2")[0].copy()
    play(kitchen, "pick", {"object": "bowl_support_2"}, gaps)
    opened, closed = jaw_limits(kitchen)
    assert opened < 0.2 and closed > 0.8, (opened, closed)
    assert gaps and max(gaps) < 0.02, gaps
    assert np.allclose(kitchen._pose("bowl_rack_2")[0], rack_before)
    bowl_bottom = float(kitchen._pose("bowl_support_2")[0][2] + kitchen._lowest_local("bowl_support_2"))
    _, rack_top = kitchen.collision.z_bounds("bowl_rack_2")
    assert bowl_bottom > rack_top + 0.02, (bowl_bottom, rack_top)
    _, jump, span = play(
        kitchen, "pour", {"source": "bowl_support_2", "target": "skimmer"}, gaps,
        clear=(("bowl_support_2", "skimmer"),), track=tuple(foods),
    )
    assert jump < 0.12, jump
    assert max(span["bowl_food_1"]) - min(span["bowl_food_1"]) > 0.08, span["bowl_food_1"]
    assert mouth_alignment(kitchen, "bowl_support_2", "skimmer") >= 0.55
    landed = kitchen.contents_of("skimmer")
    assert set(foods) <= set(landed), landed
    for food in foods:
        assert kitchen.collision.contains("skimmer", kitchen._pose(food)[0]), kitchen._pose(food)[0]
        assert not kitchen.collision.contains("bowl_support_2", kitchen._pose(food)[0])
        assert_on_floor(kitchen, food, "skimmer")
    spread = [kitchen._pose(food)[0][:2] for food in foods]
    for index, left in enumerate(spread):
        for right in spread[index + 1:]:
            assert float(np.linalg.norm(left - right)) > 0.02, (left, right)
    assert kitchen.held == "bowl_support_2"
    play(kitchen, "place_on", {"object": "bowl_support_2", "surface": "bowl_rack_2"}, gaps)
    assert kitchen.held is None

    play(kitchen, "pick", {"object": "skimmer"}, gaps)
    assert max(gaps) < 0.02, max(gaps)
    play(
        kitchen, "seat_in", {"object": "skimmer", "container": "pot"}, gaps,
        clear=(("skimmer", "pot"),),
    )
    assert kitchen.held is None
    origin, rot = kitchen._pose("skimmer")
    center, size = kitchen.collision.parts("skimmer")[1]
    basket = origin + rot @ np.array([center[0], center[1], center[2] - size[2] / 2 + 0.01])
    assert kitchen.collision.contains("pot", basket), basket
    assert not kitchen._overlaps("skimmer", "pot", {})
    for food in foods:
        assert kitchen.collision.contains("skimmer", kitchen._pose(food)[0])
        assert kitchen.collision.contains("pot", kitchen._pose(food)[0])

    play(kitchen, "pick", {"object": "skimmer"}, gaps, clear=(("skimmer", "pot"),))
    play(kitchen, "shake_off", {"object": "skimmer"}, gaps, clear=(("skimmer", "pot"),))
    assert kitchen.held == "skimmer"
    assert set(foods) <= set(kitchen.contents_of("skimmer"))
    _, jump, span = play(
        kitchen, "pour", {"source": "skimmer", "target": "bowl_support"}, gaps,
        clear=(("skimmer", "bowl_support"),), track=("bowl_food_1",),
    )
    assert jump < 0.12, jump
    assert max(span["bowl_food_1"]) - min(span["bowl_food_1"]) > 0.05
    assert mouth_alignment(kitchen, "skimmer", "bowl_support") >= 0.55
    assert set(foods) <= set(kitchen.contents_of("bowl_support"))
    for food in foods:
        assert_on_floor(kitchen, food, "bowl_support")
    play(kitchen, "place_on", {"object": "skimmer", "surface": "skimmer_holder"}, gaps)
    assert kitchen.held is None

    blocked = VirtualModel()
    blocked.load_scene(load_scene(SCENE))
    shelved = KitchenMotion(blocked)
    play(shelved, "pick", {"object": "bowl_support_2"}, [])
    # The board is added after the grasp, across the mouth of the skimmer.
    add_box(blocked, "shelf", [0.04, 0.30, 0.26], [0.20, 0.18, 0.04])
    before = blocked.robot.qs.copy()
    try:
        shelved.plan_skill("pour", {"source": "bowl_support_2", "target": "skimmer"})
        raise AssertionError("shelf must block the pour")
    except SkillFailure:
        np.testing.assert_allclose(blocked.robot.qs, before)
        assert shelved.held == "bowl_support_2"
        assert set(foods) <= set(shelved.contents_of("bowl_support_2"))
    link = next(item for item in model.robot.runtime_lnks if item.name == "link6")
    vs = link.visuals[0].geom.vs
    tip = vs[vs[:, 0] > 0.12]
    open_gap = float(tip[tip[:, 1] > 0, 1].min() - tip[tip[:, 1] < 0, 1].max())
    assert open_gap > 0.05, open_gap
    closed_vs = kitchen._jaw_closed[0.013].vs
    closed_tip = closed_vs[closed_vs[:, 0] > 0.12]
    jaw_gap = float(closed_tip[closed_tip[:, 1] > 0, 1].min() - closed_tip[closed_tip[:, 1] < 0, 1].max())
    # Skimmer rod is 23 mm across. The pads have to meet it, not pass through it.
    assert 0.022 < jaw_gap < 0.032, jaw_gap
    print(json.dumps({
        "kitchen_mesh_motion": "PASS",
        "hardware": False,
        "poured_food": foods,
        "grasp_gap": max(gaps),
        "jaw_gap": jaw_gap,
        "jaw_open_gap": open_gap,
        "skimmer_in_pot": True,
        "served_in_bowl": True,
        "bowl_left_rack": True,
        "collision_rejection": "PASS",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
