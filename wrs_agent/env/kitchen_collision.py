"""Collision boxes for the mesh kitchen and the FAFU links.

Open vessels are a floor plus four walls. Their interior is empty, so a tool or
something poured out can enter. Boxes were measured from the STL bounds.
"""

import tomllib
from pathlib import Path

import numpy as np

ASSETS = Path(__file__).resolve().parents[1] / "assets"
SHRINK = 0.004


def _rotation_boxes(center, size, wall, floor):
    """Local solid parts of an open-top box: floor and four walls."""
    center = np.asarray(center, dtype=float)
    size = np.asarray(size, dtype=float)
    sx, sy, sz = size
    cx, cy, cz = center
    parts = [(
        np.array([cx, cy, cz - sz / 2 + floor / 2]),
        np.array([sx, sy, floor]),
    )]
    cavity_h = sz - floor
    wall_z = cz - sz / 2 + floor + cavity_h / 2
    parts.append((np.array([cx, cy + sy / 2 - wall / 2, wall_z]), np.array([sx, wall, cavity_h])))
    parts.append((np.array([cx, cy - sy / 2 + wall / 2, wall_z]), np.array([sx, wall, cavity_h])))
    inner_y = sy - 2 * wall
    parts.append((
        np.array([cx + sx / 2 - wall / 2, cy, wall_z]),
        np.array([wall, inner_y, cavity_h]),
    ))
    parts.append((
        np.array([cx - sx / 2 + wall / 2, cy, wall_z]),
        np.array([wall, inner_y, cavity_h]),
    ))
    cavity_size = np.array([sx - 2 * wall, sy - 2 * wall, cavity_h])
    cavity_center = np.array([cx, cy, cz - sz / 2 + floor + cavity_h / 2])
    return parts, cavity_center, cavity_size


def _shape(spec):
    center = np.asarray(spec["center"], dtype=float)
    size = np.asarray(spec["size"], dtype=float)
    extras = [
        (np.asarray(item["center"], dtype=float), np.asarray(item["size"], dtype=float))
        for item in spec.get("extras", [])
    ]
    if spec["kind"] == "solid":
        return ((center, size), *extras), None
    parts, cavity_center, cavity_size = _rotation_boxes(
        center, size, float(spec["wall"]), float(spec["floor"])
    )
    return (*parts, *extras), (cavity_center, cavity_size)


def load_bodies():
    """Object name -> (local parts, cavity or None, liquid)."""
    with (ASSETS / "kitchen" / "collision.toml").open("rb") as source:
        config = tomllib.load(source)
    shapes = {}
    for name, spec in config["shapes"].items():
        parts, cavity = _shape(spec)
        shapes[name] = (parts, cavity, bool(spec.get("liquid", False)))
    return {obj: shapes[shape] for obj, shape in config["assign"].items()}


def load_arm():
    """Link name -> (local center, half extents), slightly inset."""
    with (ASSETS / "fafu" / "collision.toml").open("rb") as source:
        config = tomllib.load(source)
    boxes = {}
    for name, spec in config["links"].items():
        size = np.maximum(np.asarray(spec["size"], dtype=float) - 2 * SHRINK, 0.01)
        boxes[name] = (np.asarray(spec["center"], dtype=float), size / 2)
    return boxes


def boxes_overlap(center_a, rot_a, half_a, center_b, rot_b, half_b, pad=0.001):
    """Separating-axis test for two oriented boxes. Half extents are full size / 2."""
    half_a = np.asarray(half_a, dtype=float) + pad
    half_b = np.asarray(half_b, dtype=float) + pad
    translation = rot_a.T @ (center_b - center_a)
    # Radii use |R|; projected distances need the signed R, or the test depends
    # on which box is passed first and can miss or invent a contact.
    signed = rot_a.T @ rot_b
    rotation = np.abs(signed) + 1e-8
    for axis in range(3):
        if abs(translation[axis]) > half_a[axis] + half_b @ rotation[axis]:
            return False
    for axis in range(3):
        if abs(translation @ signed[:, axis]) > half_a @ rotation[:, axis] + half_b[axis]:
            return False
    for i in range(3):
        i1, i2 = (i + 1) % 3, (i + 2) % 3
        for j in range(3):
            j1, j2 = (j + 1) % 3, (j + 2) % 3
            radius = (
                half_a[i1] * rotation[i2, j] + half_a[i2] * rotation[i1, j]
                + half_b[j1] * rotation[i, j2] + half_b[j2] * rotation[i, j1]
            )
            distance = abs(translation[i1] * signed[i2, j] - translation[i2] * signed[i1, j])
            if distance > radius:
                return False
    return True


def point_in_cavity(point, origin, rotmat, cavity):
    center, size = cavity
    local = rotmat.T @ (np.asarray(point, dtype=float) - origin)
    return np.all(np.abs(local - center) <= size / 2 + 1e-4)
