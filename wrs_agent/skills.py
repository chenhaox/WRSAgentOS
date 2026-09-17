"""Explicit skill contracts and functions; no bus, model SDK or device imports."""

import json
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256

from pydantic import Field

from wrs_agent.schemas import Boundary, Name


class NoArgs(Boundary):
    pass


class MoveArgs(Boundary):
    pose: Name


class PickArgs(Boundary):
    object: Name


class PlaceArgs(PickArgs):
    target: Name


class SpeakArgs(Boundary):
    text: str = Field(min_length=1, max_length=512)


class SkillSpec(Boundary):
    name: Name
    node: Name
    version: int = 1
    description: str
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    parameters: dict
    required_capabilities: list[str]
    resources: list[str]
    preconditions: list[str]
    verification: str
    interrupt_mode: str = "controlled_stop"
    timeout: float = Field(default=10.0, gt=0)
    recovery: list[str] = Field(default_factory=list)


@dataclass
class VirtualWorld:
    objects: dict[str, str]
    held: str | None = None
    pose: str = "home"
    version: int = 0
    calibration: str = "mock-v1"

    def snapshot(self):
        return {
            "objects": self.objects.copy(),
            "held_object": self.held,
            "pose": self.pose,
            "facts": {"calibration": self.calibration},
        }


@dataclass
class SpeechState:
    version: int = 0
    completed: int = 0
    last_text: str = ""

    def snapshot(self):
        return {"facts": {"completed": self.completed, "last_text": self.last_text}}


def observe(world, args):
    return True


def move_named_pose(world, args):
    if args.pose not in {"home", "B", "C"}:
        raise ValueError("unknown_pose")
    world.pose = args.pose
    return world.pose == args.pose


def pick(world, args):
    if world.held is not None or args.object not in world.objects:
        raise ValueError("pick_precondition")
    world.held = args.object
    world.objects[args.object] = "gripper"
    return world.held == args.object


def place(world, args):
    if world.held != args.object or args.target not in {"B", "C"}:
        raise ValueError("place_precondition")
    world.objects[args.object] = args.target
    world.held = None
    return world.objects[args.object] == args.target and world.held is None


def verify(world, args):
    return world.objects.get(args.object) == args.target and world.held is None


def speak(state, args):
    # Virtual completion of the full utterance, never a claim about audible output.
    state.last_text = args.text
    state.completed += 1
    return state.last_text == args.text


ROBOT_SKILLS = {"observe", "move_named_pose", "pick", "place", "verify"}
FUNCTIONS = {
    "observe": (NoArgs, observe),
    "move_named_pose": (MoveArgs, move_named_pose),
    "pick": (PickArgs, pick),
    "place": (PlaceArgs, place),
    "verify": (PlaceArgs, verify),
    "speak": (SpeakArgs, speak),
}
METADATA = {
    "observe": (
        "Read current object and robot evidence",
        ["观察", "检测", "observe"],
        ["perception"],
    ),
    "move_named_pose": (
        "Move to a validated named joint pose",
        ["移动", "回家", "move", "home"],
        ["motion"],
    ),
    "pick": (
        "Acquire an observed object and verify holding",
        ["抓取", "拿起", "pick"],
        ["manipulation"],
    ),
    "place": (
        "Place the held object at a known target",
        ["放", "放置", "put", "place"],
        ["manipulation"],
    ),
    "verify": (
        "Confirm object placement and empty gripper",
        ["检查", "验证", "verify"],
        ["verification"],
    ),
    "speak": (
        "Speak an utterance on the independent TTS node",
        ["说", "播报", "speak", "say"],
        ["audio"],
    ),
}
SPECS = {
    name: SkillSpec(
        name=name,
        node="tts" if name == "speak" else "wrs",
        description=METADATA[name][0],
        aliases=METADATA[name][1],
        tags=METADATA[name][2],
        parameters=args.model_json_schema(),
        required_capabilities=[name],
        resources=["speaker"] if name == "speak" else ["arm"],
        preconditions={
            "pick": ["empty_gripper", "object_visible"],
            "place": ["object_held", "known_target"],
            "move_named_pose": ["known_pose"],
        }.get(name, []),
        recovery=["observe_once"] if name == "pick" else [],
        verification="virtual_utterance_complete"
        if name == "speak"
        else "virtual_world_postcondition",
    )
    for name, (args, _) in FUNCTIONS.items()
}


def validate_skill(name, version, args):
    if name not in FUNCTIONS or version != SPECS[name].version:
        raise ValueError("unknown_skill_or_version")
    parsed = FUNCTIONS[name][0].model_validate(args)
    if name == "move_named_pose" and parsed.pose not in {"home", "B", "C"}:
        raise ValueError("unknown_pose")
    if name in {"place", "verify"} and parsed.target not in {"B", "C"}:
        raise ValueError("unknown_target")
    return parsed


def registry_signature():
    payload = {name: spec.model_dump() for name, spec in sorted(SPECS.items())}
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=128)
def _rank_candidates(query, signature, available):
    # Cache names only. Permission and current capabilities are checked by every caller.
    scores = {}
    transfer = any(word in query for word in ("放", "put ", "place "))
    for name in available:
        spec = SPECS[name]
        score = sum(word.casefold() in query for word in [name, *spec.aliases, *spec.tags])
        if transfer and name in {"observe", "pick", "place", "verify"}:
            score += 1
        scores[name] = score
    ranked = sorted(available, key=lambda name: (-scores[name], name))
    return tuple(name for name in ranked if scores[name]) or tuple(ranked)


def lookup_skills(query, capabilities, bindings=None, *, limit=8):
    if not 1 <= limit <= 16:
        raise ValueError("invalid_skill_limit")
    bindings = bindings or {name: spec.node for name, spec in SPECS.items()}
    available = tuple(
        name
        for name, spec in sorted(SPECS.items())
        if (cap := capabilities.get(bindings.get(name))) is not None
        and set(spec.required_capabilities).issubset(cap.skills)
    )
    names = _rank_candidates(query.casefold().strip(), registry_signature(), available)
    # Return copies: callers cannot mutate the live registry through metadata lists.
    return [SPECS[name].model_copy(deep=True) for name in names[:limit]]
