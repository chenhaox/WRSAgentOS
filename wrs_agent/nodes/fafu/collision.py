"""Planner obstacles for recognized objects: class sizes from classes.toml, poses from vision.

Not joint limits and not a viewer. Each body uses the same layout as the mesh kitchen,
so an open vessel is a floor and four walls with an empty cavity above the floor.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from wrs_agent.env.kitchen_collision import _shape

CLASSES = Path(__file__).resolve().parents[2] / "assets" / "fafu" / "classes.toml"


@dataclass(frozen=True)
class ObjectClass:
    key: str
    prompt: str
    name: str
    kind: str
    size: tuple
    wall: float
    floor: float
    graspable: bool
    rgb: tuple

    @property
    def vessel(self):
        return self.kind == "vessel"

    def body(self):
        """(local parts, cavity or None, liquid) with the origin at the box center."""
        spec = {"kind": self.kind, "center": [0.0, 0.0, 0.0], "size": list(self.size)}
        if self.vessel:
            spec.update(wall=self.wall, floor=self.floor)
        parts, cavity = _shape(spec)
        return parts, cavity, False


@dataclass(frozen=True)
class Catalog:
    classes: dict
    table_size: tuple
    table_rgb: tuple

    @property
    def vocabulary(self):
        return [entry.prompt for entry in self.classes.values()]

    def by_prompt(self, prompt):
        for entry in self.classes.values():
            if entry.prompt.casefold() == str(prompt).casefold():
                return entry
        return None

    def table_body(self):
        return ((np.zeros(3), np.asarray(self.table_size, dtype=float)),), None, False


def load_catalog(path=CLASSES):
    with Path(path).open("rb") as source:
        config = tomllib.load(source)
    classes = {}
    for key, spec in config["classes"].items():
        size = tuple(float(v) for v in spec["size"])
        kind = spec["kind"]
        if kind not in {"vessel", "solid"} or len(size) != 3 or min(size) <= 0:
            raise ValueError(f"classes.toml 的 {key} 需要 kind 为 vessel/solid、size 为三个正数")
        entry = ObjectClass(
            key=key, prompt=spec["prompt"], name=spec.get("name", key), kind=kind, size=size,
            wall=float(spec.get("wall", 0.005)), floor=float(spec.get("floor", 0.005)),
            graspable=bool(spec.get("graspable", True)),
            rgb=tuple(float(v) for v in spec.get("rgb", (0.6, 0.6, 0.6))),
        )
        if entry.vessel and (2 * entry.wall >= min(size[:2]) or entry.floor >= size[2]):
            raise ValueError(f"classes.toml 的 {key} 壁厚或底厚超过了外形")
        classes[key] = entry
    prompts = [entry.prompt.casefold() for entry in classes.values()]
    if len(set(prompts)) != len(prompts):
        raise ValueError("classes.toml 里有重复的 prompt")
    table = config["table"]
    return Catalog(classes=classes, table_size=tuple(float(v) for v in table["size"]),
                   table_rgb=tuple(float(v) for v in table.get("rgb", (0.75, 0.72, 0.65))))


def bodies(catalog, objects):
    """Collision bodies by object name for the planner. ``objects`` maps name -> class key."""
    found = {}
    for name, key in objects.items():
        found[name] = catalog.table_body() if key == "table" else catalog.classes[key].body()
    return found
