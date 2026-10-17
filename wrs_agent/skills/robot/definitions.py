"""Robot action parameters and contracts; implementations belong to the chosen environment."""

from importlib.resources import files
from typing import Literal

from pydantic import Field, model_validator

from wrs_agent.schemas import Boundary, Empty, Name
from wrs_agent.skills.contracts import Skill


class MoveArgs(Boundary):
    pose: Literal["home", "B", "C"]


class RelativeMoveArgs(Boundary):
    dx: float = Field(default=0.0, ge=-0.1, le=0.1, description="World X displacement in meters")
    dy: float = Field(default=0.0, ge=-0.1, le=0.1, description="World Y displacement in meters")
    dz: float = Field(default=0.0, ge=-0.1, le=0.1, description="World Z displacement in meters")

    @model_validator(mode="after")
    def bounded_displacement(self):
        distance_squared = self.dx**2 + self.dy**2 + self.dz**2
        if not 0 < distance_squared <= 0.1**2 + 1e-12:
            raise ValueError("displacement_must_be_nonzero_and_at_most_10cm")
        return self


class GripperArgs(Boundary):
    command: Literal["open", "close"]


class PickArgs(Boundary):
    object: Name


class PlaceArgs(PickArgs):
    target: Literal["B", "C"]


def _distinct(*values):
    if len(set(values)) != len(values):
        raise ValueError("objects_must_be_distinct")


class ReorientArgs(PickArgs):
    orientation: Literal["upright", "upside_down"]


class PlaceOnArgs(PickArgs):
    surface: Name

    @model_validator(mode="after")
    def distinct_objects(self):
        _distinct(self.object, self.surface)
        return self


class SeatInArgs(PickArgs):
    container: Name

    @model_validator(mode="after")
    def distinct_objects(self):
        _distinct(self.object, self.container)
        return self


class ControlArgs(Boundary):
    appliance: Name
    state: Literal["on", "off"]


class PourArgs(Boundary):
    source: Name
    target: Name

    @model_validator(mode="after")
    def distinct_objects(self):
        _distinct(self.source, self.target)
        return self


class StirArgs(Boundary):
    tool: Name
    vessel: Name

    @model_validator(mode="after")
    def distinct_objects(self):
        _distinct(self.tool, self.vessel)
        return self


class ScoopArgs(Boundary):
    tool: Name
    source: Name
    target: Name

    @model_validator(mode="after")
    def distinct_objects(self):
        _distinct(self.tool, self.source, self.target)
        return self


_GUIDE = files(__package__).joinpath("SKILL.md").read_text(encoding="utf-8")


def _skill(
    name,
    arguments,
    description,
    *,
    preconditions=(),
    recovery=(),
    verification="virtual_world_postcondition",
    timeout=10.0,
):
    return Skill(
        name=name,
        description=description,
        instructions=_GUIDE,
        resources=("arm",),
        preconditions=preconditions,
        recovery=recovery,
        verification=verification,
        timeout=timeout,
        arguments=arguments,
    )


SKILLS = {
    entry.name: entry
    for entry in (
        _skill(
            "observe",
            Empty,
            "Refresh robot and environment state only; no camera detection and no text output",
        ),
        _skill(
            "move_named_pose",
            MoveArgs,
            "Move to a validated named joint pose",
            preconditions=("known_pose",),
        ),
        _skill(
            "move_relative",
            RelativeMoveArgs,
            "Offset the flange in world XYZ meters at most 10 cm: up/down is ±Z, "
            "left/right is ±Y, forward/back is ±X; preserve endpoint orientation, "
            "joint-interpolated path without collision checking",
            preconditions=("reachable_target",),
            verification="wrs_fk",
        ),
        _skill(
            "set_gripper",
            GripperArgs,
            "Fully open or fully close the parallel gripper jaw; the arm does not move. "
            "Closing is jaw motion only: it never confirms contact or holding an object",
            verification="wrs_fk",
        ),
        _skill(
            "pick",
            PickArgs,
            "Acquire an observed object and verify holding",
            preconditions=("empty_gripper", "object_visible"),
            recovery=("observe_once",),
        ),
        _skill(
            "place",
            PlaceArgs,
            "Place the held object at a known target",
            preconditions=("object_held", "known_target"),
        ),
        _skill(
            "verify",
            PlaceArgs,
            "Confirm object placement and empty gripper",
        ),
        _skill(
            "reorient",
            ReorientArgs,
            "Turn the held object until its opening faces up (upright) or down; succeeds "
            "at once if it already has that orientation. The object stays in the gripper",
            preconditions=("object_held", "regrasp_clearance"),
            timeout=20.0,
        ),
        _skill(
            "place_on",
            PlaceOnArgs,
            "Set the held object down resting on an observed surface (table, plate, "
            "cooktop) and release it; for the inside of a container use seat_in",
            preconditions=("object_held", "surface_visible"),
        ),
        _skill(
            "seat_in",
            SeatInArgs,
            "Lower the held object through an observed container's opening and release it "
            "inside, not on the rim: strainer into pot, noodles into strainer, lid onto pot",
            preconditions=("object_held", "container_visible", "container_upright"),
        ),
        _skill(
            "set_control",
            ControlArgs,
            "Switch an observed appliance such as a cooktop on or off with its control; "
            "succeeds only when the appliance reports the requested state",
            preconditions=("appliance_visible", "empty_gripper"),
        ),
        _skill(
            "pour",
            PourArgs,
            "Carry the held source above the observed target and tilt its opening past "
            "horizontal to empty it, then return it upright and still held",
            preconditions=("source_held", "target_visible"),
            timeout=30.0,
        ),
        _skill(
            "stir",
            StirArgs,
            "Lower the held tool through the observed vessel's opening and circle inside "
            "it, then lift it out; the tool stays in the gripper",
            preconditions=("tool_held", "vessel_visible"),
            timeout=60.0,
        ),
        _skill(
            "scoop",
            ScoopArgs,
            "Dip the held tool into the source, sweep and lift one portion, then tilt it "
            "over the target to release it; the tool stays in the gripper",
            preconditions=("tool_held", "source_visible", "target_visible"),
            timeout=30.0,
        ),
        _skill(
            "shake_off",
            PickArgs,
            "Lift the held strainer and shake it side to side to drain it; it stays held",
            preconditions=("object_held",),
            timeout=20.0,
        ),
    )
}
