"""Explicit skill contracts and functions; no bus, model SDK or device imports."""

from dataclasses import dataclass

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

    def snapshot(self):
        return {"objects": self.objects.copy(), "held_object": self.held, "pose": self.pose}


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
SPECS = {
    name: SkillSpec(
        name=name,
        node="tts" if name == "speak" else "wrs",
        description=f"Virtual {name}",
        parameters=args.model_json_schema(),
        required_capabilities=[name],
        resources=["speaker"] if name == "speak" else ["arm"],
        preconditions={
            "pick": ["empty_gripper", "object_visible"],
            "place": ["object_held", "known_target"],
            "move_named_pose": ["known_pose"],
        }.get(name, []),
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
