"""Recognized objects in the world frame. Positions come from depth; sizes from the class.

The detector names a class and a pixel box; the camera points inside the box, moved
through the hand-eye matrix, give where it is. Height comes from the highest visible
points, so a bowl on a rack ends up on the rack rather than on the table.
"""

from dataclasses import dataclass

import numpy as np

from wrs_agent.nodes.fafu.handeye import transform
from wrs_agent.schemas import ObjectData

# Points this close above the table plane belong to the table, not the object.
TABLE_BAND = 0.008
# A body whose lowest point is this close to the table is set down on it.
TABLE_SNAP = 0.02
MIN_POINTS = 30
TOP_BAND = 0.015


@dataclass
class Seen:
    key: str
    pos: np.ndarray
    rotmat: np.ndarray
    score: float
    center_x: float  # Image x of the box center; locate_object's leftmost/rightmost order.
    name: str = ""


def table_height(points_world, default, *, band=0.10):
    """The densest height near ``default``; the table is the largest flat area in view."""
    z = points_world[:, 2]
    z = z[np.abs(z - default) <= band]
    if len(z) < 500:
        return float(default)
    hist, edges = np.histogram(z, bins=80)
    peak = int(np.argmax(hist))
    near = z[(z >= edges[peak]) & (z <= edges[peak + 1])]
    return float(np.median(near))


def _yaw(points_xy, size):
    """Turn an elongated class to the principal axis of what was seen from above."""
    if max(size[0], size[1]) < 1.3 * min(size[0], size[1]) or len(points_xy) < MIN_POINTS:
        return np.eye(3)
    centered = points_xy - points_xy.mean(axis=0)
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    major = vh[0]
    long_axis = 0 if size[0] >= size[1] else 1
    angle = float(np.arctan2(major[1], major[0])) - (0.0 if long_axis == 0 else np.pi / 2)
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def locate(detections, points_cam, world_from_cam, catalog, *, table_z, min_score, shrink):
    """One Seen per detection with enough depth. ``points_cam`` is the HxWx3 camera cloud."""
    height, width = points_cam.shape[:2]
    found = []
    for item in detections:
        entry = catalog.by_prompt(item.label)
        if entry is None or item.score < min_score:
            continue
        x1, y1, x2, y2 = item.box
        dx, dy = (x2 - x1) * shrink, (y2 - y1) * shrink
        u1, u2 = int(max(0, x1 + dx)), int(min(width, x2 - dx))
        v1, v2 = int(max(0, y1 + dy)), int(min(height, y2 - dy))
        if u2 <= u1 or v2 <= v1:
            continue
        cloud = points_cam[v1:v2, u1:u2].reshape(-1, 3)
        cloud = cloud[np.isfinite(cloud).all(axis=1) & (cloud[:, 2] > 0)]
        if len(cloud) < MIN_POINTS:
            continue
        world = transform(world_from_cam, cloud)
        world = world[world[:, 2] > table_z + TABLE_BAND]
        if len(world) < MIN_POINTS:
            continue
        size = entry.size
        top = float(np.percentile(world[:, 2], 95))
        bottom = top - size[2]
        if bottom < table_z + TABLE_SNAP:
            bottom = table_z
        # A tilted camera sees the near side too; the rim or top face is seen all round,
        # so its extent, from the whole box, centers the object.
        full = points_cam[int(max(0, y1)):int(min(height, y2)) + 1,
                          int(max(0, x1)):int(min(width, x2)) + 1].reshape(-1, 3)
        full = transform(world_from_cam, full[np.isfinite(full).all(axis=1) & (full[:, 2] > 0)])
        band = full[np.abs(full[:, 2] - (top - TOP_BAND / 2)) <= TOP_BAND / 2 + 0.005, :2]
        if len(band) < MIN_POINTS:
            band = world[:, :2]
        xy = (np.percentile(band, 2, axis=0) + np.percentile(band, 98, axis=0)) / 2
        pos = np.array([xy[0], xy[1], bottom + size[2] / 2])
        found.append(Seen(key=entry.key, pos=pos, rotmat=_yaw(world[:, :2], size),
                          score=float(item.score), center_x=(x1 + x2) / 2))
    return found


def merge(seen, distance):
    """Two boxes of one class on the same spot are one object; keep the confident one."""
    kept = []
    for item in sorted(seen, key=lambda s: -s.score):
        if all(other.key != item.key or np.linalg.norm(other.pos[:2] - item.pos[:2]) >= distance
               for other in kept):
            kept.append(item)
    return kept


def name(seen, previous, distance):
    """Keep an id across observations when the same class is still about where it was.

    ``previous`` maps name -> (class key, position). New objects take the lowest free
    number of their class, so ids stay short: bowl_1, bowl_2.
    """
    taken = set()
    pairs = sorted(
        ((float(np.linalg.norm(item.pos[:2] - pos[:2])), index, old)
         for index, item in enumerate(seen)
         for old, (key, pos) in previous.items() if key == item.key),
    )
    for gap, index, old in pairs:
        if gap < distance and not seen[index].name and old not in taken:
            seen[index].name = old
            taken.add(old)
    for item in sorted(seen, key=lambda s: (s.key, s.pos[1], s.pos[0])):
        if item.name:
            continue
        number = 1
        while f"{item.key}_{number}" in taken:
            number += 1
        item.name = f"{item.key}_{number}"
        taken.add(item.name)
    return seen


def selection_hints(seen):
    """locate_object only tells two of a kind apart by image order: leftmost/rightmost."""
    hints = {}
    by_class = {}
    for item in seen:
        by_class.setdefault(item.key, []).append(item)
    for items in by_class.values():
        if len(items) == 1:
            hints[items[0].name] = "only"
        elif len(items) == 2:
            left, right = sorted(items, key=lambda s: s.center_x)
            hints[left.name], hints[right.name] = "leftmost", "rightmost"
        else:
            for item in items:
                hints[item.name] = None
    return hints


def object_data(item, entry, hint, observed_at_ns):
    if hint is None:
        locate = "同类超过两个，无法单独定位"
    else:
        locate = f"locate_object target={entry.prompt} selection={hint}"
    return ObjectData(
        source="perception", label=f"{entry.name}（{locate}）"[:80],
        pos=[float(v) for v in item.pos], rotmat=item.rotmat.tolist(),
        geometry={"kind": "box", "xyz_lengths": list(entry.size)},
        rgb=list(entry.rgb), alpha=0.6, observed_at_ns=observed_at_ns,
    )


def table_data(catalog, table_z, center_xy):
    size = catalog.table_size
    return ObjectData(
        source="perception", label="桌面",
        pos=[float(center_xy[0]), float(center_xy[1]), table_z - size[2] / 2],
        rotmat=np.eye(3).tolist(), geometry={"kind": "box", "xyz_lengths": list(size)},
        rgb=list(catalog.table_rgb), alpha=0.25,
    )
