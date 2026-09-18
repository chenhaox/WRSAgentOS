from copy import deepcopy
from dataclasses import replace

import pytest

from wrs_agent.cache import PlanCache, parse_intent
from wrs_agent.schemas import ActionContext, CapabilitySnapshot, NodeSnapshot, Plan, RobotData, Step
from wrs_agent.skills import SKILLS

BINDINGS = {name: "wrs" for name in ("observe", "pick", "place", "verify")}


def context():
    worlds = {
        "wrs": NodeSnapshot(
            node_id="wrs",
            captured_at_ns=1,
            boot_id="boot",
            control_epoch=0,
            world_version=0,
            admission="OPEN",
            active_action=None,
            stop_confirmed=True,
            data=RobotData(objects={"A": "table", "D": "table"}, facts={"calibration": "v1"}),
        )
    }
    caps = {"wrs": CapabilitySnapshot(skills=list(BINDINGS))}
    return worlds, caps


def plan():
    return Plan(
        steps=[
            Step(step_id="o", skill="observe"),
            Step(step_id="p", skill="pick", args={"object": "A"}, depends_on=["o"]),
            Step(step_id="l", skill="place", args={"object": "A", "target": "B"}, depends_on=["p"]),
            Step(
                step_id="v", skill="verify", args={"object": "A", "target": "B"}, depends_on=["l"]
            ),
        ]
    )


@pytest.mark.parametrize(
    "goal",
    [
        "不要把 A 放到 B",
        "把两个 A 放到 B",
        "把 A 放到 B 然后去 C",
        "把 A 放到 B，完成后再拿 D",
        "put A in B tomorrow",
        "put A in C and then B",
    ],
)
def test_negation_quantity_and_timing_not_cacheable(goal):
    assert parse_intent(goal) is None


def test_strict_hit_unrelated_change_new_authority_and_no_cached_grants():
    cache = PlanCache()
    worlds, caps = context()
    assert cache.remember("把 A 放到 B", plan(), worlds, caps, BINDINGS)
    changed = ActionContext(**worlds["wrs"].model_dump(), lease_id="new-lease").model_copy(
        update={
            "boot_id": "new-boot",
            "control_epoch": 8,
            "world_version": 10,
            "data": worlds["wrs"].data.model_copy(update={"objects": {"A": "table", "D": "C"}}),
        }
    )
    hit = cache.lookup("put A in B", {"wrs": changed}, caps, BINDINGS)
    assert hit == plan() and cache.hits == 1
    stored = repr(cache.entries)
    assert all(key not in stored for key in ("action_id", "control_epoch", "boot_id", "lease_id"))
    assert cache.lookup("put A in C", worlds, caps, BINDINGS) is None
    assert cache.shadow_candidate == "transfer:A:B"


@pytest.mark.parametrize(
    "change",
    ["location", "calibration", "capabilities", "held", "unknown", "schema", "skill", "binding"],
)
def test_applicability_change_rejects(change, monkeypatch):
    cache = PlanCache()
    worlds, caps = context()
    assert cache.remember("put A in B", plan(), worlds, caps, BINDINGS)
    bindings = dict(BINDINGS)
    world = worlds["wrs"]
    if change == "location":
        worlds["wrs"] = world.model_copy(
            update={"data": world.data.model_copy(update={"objects": {"A": "C"}})}
        )
    elif change == "calibration":
        worlds["wrs"] = world.model_copy(
            update={"data": world.data.model_copy(update={"facts": {"calibration": "v2"}})}
        )
    elif change == "capabilities":
        caps["wrs"] = CapabilitySnapshot(skills=["observe"])
    elif change == "held":
        worlds["wrs"] = world.model_copy(
            update={"data": world.data.model_copy(update={"held_object": "A"})}
        )
    elif change == "unknown":
        worlds["wrs"] = world.model_copy(update={"admission": "UNKNOWN"})
    elif change == "schema":
        cache.entries["transfer:A:B"].schema_version = 2
    elif change == "binding":
        bindings["pick"] = "different-node"
    else:
        monkeypatch.setitem(
            SKILLS,
            "pick",
            replace(SKILLS["pick"], spec=SKILLS["pick"].spec.model_copy(update={"version": 2})),
        )
    assert cache.lookup("put A in B", worlds, caps, bindings) is None
    assert cache.reject_reason


def test_only_exact_verified_plan_structure_and_bounded_failure_records():
    worlds, caps = context()
    cache = PlanCache(capacity=1)
    wrong = deepcopy(plan().model_dump())
    wrong["steps"][2]["args"]["target"] = "C"
    assert not cache.remember("put A in B", Plan.model_validate(wrong), worlds, caps, BINDINGS)
    cache.remember("put A in B", plan(), worlds, caps, BINDINGS)
    cache.invalidate("put A in B", "postcondition_failed")
    assert cache.lookup("put A in B", worlds, caps, BINDINGS) is None
    assert cache.failures["transfer:A:B"] == "postcondition_failed"


def test_changed_skill_guidance_invalidates_cached_plan(monkeypatch):
    cache = PlanCache()
    worlds, caps = context()
    assert cache.remember("put A in B", plan(), worlds, caps, BINDINGS)
    monkeypatch.setitem(
        SKILLS,
        "pick",
        replace(
            SKILLS["pick"],
            spec=SKILLS["pick"].spec.model_copy(update={"instructions": "changed guidance"}),
        ),
    )
    assert cache.lookup("put A in B", worlds, caps, BINDINGS) is None
    assert cache.reject_reason
