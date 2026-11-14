"""Resolve editable metre/degree placements into the existing world-frame protocol."""

import math
import tomllib
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

from pydantic import Field

from wrs_agent.schemas import MAX_BYTES, Boundary, Name, ObjectData, Vector3


class Placement(Boundary):
    pos: Vector3
    rpy_deg: Vector3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    parent: Name | None = None


class LayoutConfig(Boundary):
    objects: dict[Name, Placement] = Field(max_length=32)


def rotation_from_degrees(rpy):
    """Right-handed fixed-axis roll/pitch/yaw: Rz(yaw) @ Ry(pitch) @ Rx(roll)."""
    roll, pitch, yaw = (math.radians(v) for v in rpy)
    sr, sp, sy = math.sin(roll), math.sin(pitch), math.sin(yaw)
    cr, cp, cy = math.cos(roll), math.cos(pitch), math.cos(yaw)
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]


def apply_layout(path, objects):
    with Path(path).open("rb") as source:
        contents = source.read(MAX_BYTES + 1)
    if len(contents) > MAX_BYTES:
        raise ValueError("layout_config_too_large")
    placements = LayoutConfig.model_validate(
        tomllib.loads(contents.decode("utf-8"))
    ).objects
    if set(placements) != set(objects):
        missing = sorted(set(objects) - set(placements))
        unknown = sorted(set(placements) - set(objects))
        raise ValueError(f"layout_objects_mismatch: missing={missing}, unknown={unknown}")
    for name, placement in placements.items():
        if placement.parent is not None and placement.parent not in placements:
            raise ValueError(f"layout_unknown_parent: {name} -> {placement.parent}")
    try:
        order = list(TopologicalSorter({
            name: () if p.parent is None else (p.parent,)
            for name, p in placements.items()
        }).static_order())
    except CycleError as error:
        raise ValueError("layout_parent_cycle") from error

    result = {}
    for name in order:
        placement = placements[name]
        pos, rotation = placement.pos, rotation_from_degrees(placement.rpy_deg)
        if placement.parent is not None:
            parent = result[placement.parent]
            pos = [
                parent.pos[i] + sum(parent.rotmat[i][j] * pos[j] for j in range(3))
                for i in range(3)
            ]
            rotation = [
                [sum(parent.rotmat[i][k] * rotation[k][j] for k in range(3))
                 for j in range(3)]
                for i in range(3)
            ]
        # Revalidate instead of using model_copy(update=...), which skips validation.
        result[name] = ObjectData.model_validate({
            **objects[name].model_dump(), "pos": pos, "rotmat": rotation,
        })
    return {name: result[name] for name in objects}
