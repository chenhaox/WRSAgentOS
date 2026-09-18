"""Conservative in-memory templates. Never store execution IDs or authority."""

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256

from wrs_agent.schemas import Plan
from wrs_agent.skills import SKILLS

TRANSFER = ("observe", "pick", "place", "verify")
NAME = r"[A-Za-z][A-Za-z0-9_-]{0,39}"


@dataclass(frozen=True)
class Intent:
    object: str
    target: str

    @property
    def signature(self):
        return f"transfer:{self.object}:{self.target}"


def parse_intent(goal):
    patterns = [
        rf"把\s*({NAME})\s*放到\s*({NAME})(?:\s*并验证)?[。]?",
        rf"put\s+({NAME})\s+in\s+({NAME})(?:\s+and verify)?[.]?",
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern, goal.strip())
        if match and match[2] in {"B", "C"}:
            return Intent(match[1], match[2])
    return None


def applicability(intent, worlds, capabilities, bindings):
    node_names = {bindings.get(name) for name in TRANSFER}
    if len(node_names) != 1:
        return None, "binding_changed"
    node_name = next(iter(node_names))
    world, cap = worlds.get(node_name), capabilities.get(node_name)
    if world is None or cap is None or not set(TRANSFER).issubset(cap.skills):
        return None, "capability_missing"
    if world.admission != "OPEN" or not world.stop_confirmed or world.active_action:
        return None, "state_not_ready"
    if world.data.held_object is not None:
        return None, "gripper_not_empty"
    if intent.object not in world.data.objects or world.data.objects[intent.object] == "gripper":
        return None, "object_not_observed"
    calibration = world.data.facts.get("calibration")
    if not isinstance(calibration, str) or not calibration:
        return None, "calibration_missing"
    specs = {name: SKILLS[name].spec.model_dump() for name in TRANSFER}
    return {
        "location": world.data.objects[intent.object],
        "calibration": calibration,
        "skills": sha256(json.dumps(specs, sort_keys=True).encode()).hexdigest(),
        "node": node_name,
        "capability": cap.model_dump(),
    }, ""


def parameterize(plan, intent):
    if tuple(step.skill for step in plan.steps) != TRANSFER:
        return None
    expected = [
        {},
        {"object": intent.object},
        {"object": intent.object, "target": intent.target},
        {"object": intent.object, "target": intent.target},
    ]
    for index, step in enumerate(plan.steps):
        dependencies = [] if index == 0 else [plan.steps[index - 1].step_id]
        if (
            step.args != expected[index]
            or step.depends_on != dependencies
            or step.version != SKILLS[step.skill].spec.version
            or step.category != "interactive"
        ):
            return None
    template = plan.model_dump()
    for step in template["steps"]:
        if "object" in step["args"]:
            step["args"]["object"] = "$object"
        if "target" in step["args"]:
            step["args"]["target"] = "$target"
    return template


@dataclass
class CacheEntry:
    template: dict
    conditions: dict
    schema_version: int = 1


class PlanCache:
    def __init__(self, capacity=64):
        if not 1 <= capacity <= 256:
            raise ValueError("invalid_cache_capacity")
        self.capacity = capacity
        self.entries = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.last_hit = False
        self.reject_reason = ""
        self.shadow_candidate = None
        self.failures = OrderedDict()

    def lookup(self, goal, worlds, capabilities, bindings):
        self.last_hit = False
        self.shadow_candidate = None
        intent = parse_intent(goal)
        plan, reason = None, "unparsed_intent"
        if intent:
            self.shadow_candidate = next(
                (key for key in self.entries if key.startswith(f"transfer:{intent.object}:")), None
            )
            conditions, reason = applicability(intent, worlds, capabilities, bindings)
            entry = self.entries.get(intent.signature)
            if not reason:
                if entry is None:
                    reason = "no_entry"
                elif entry.schema_version != 1 or entry.conditions != conditions:
                    reason = "applicability_changed"
                else:
                    data = json.loads(json.dumps(entry.template))
                    for step in data["steps"]:
                        step["args"] = {
                            key: intent.object
                            if value == "$object"
                            else intent.target
                            if value == "$target"
                            else value
                            for key, value in step["args"].items()
                        }
                    plan = Plan.model_validate(data)
                    self.entries.move_to_end(intent.signature)
        self.reject_reason = reason
        if plan is None:
            self.misses += 1
        else:
            self.last_hit = True
            self.hits += 1
        return plan

    def remember(self, goal, plan, worlds, capabilities, bindings):
        """Call only after the task's postconditions were verified by execution."""
        intent = parse_intent(goal)
        if intent is None:
            return False
        conditions, reason = applicability(intent, worlds, capabilities, bindings)
        template = parameterize(plan, intent)
        if reason or template is None:
            return False
        self.entries[intent.signature] = CacheEntry(template, conditions)
        self.entries.move_to_end(intent.signature)
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
        return True

    def invalidate(self, goal, reason):
        intent = parse_intent(goal)
        if intent:
            self.entries.pop(intent.signature, None)
            self.failures[intent.signature] = reason
            while len(self.failures) > self.capacity:
                self.failures.popitem(last=False)
