"""Skill motions for the mesh kitchen, played the same way as VirtualKitchen.

Planning reads the current poses, restores the arm, and leaves a path that
``step`` plays. A grasp locks the object to the fingertips, so later motion
keeps the jaw on the same point. Pour, scoop and shake move what is inside:
pieces slide to the lip, leave the opening, and fall into the target.
"""

import asyncio
import time
from copy import deepcopy
from itertools import islice

import numpy as np

from wrs_agent.env.kitchen_collision import boxes_overlap, load_arm, load_bodies, point_in_cavity
from wrs_agent.executor import SkillFailure
from wrs_agent.schemas import ObjectData
from wrs_agent.skills.robot import SKILLS

SKILLS_HANDLED = (
    "observe", "move_named_pose", "move_relative", "set_gripper",
    "pick", "place_on", "seat_in", "pour", "stir", "scoop", "shake_off",
    "reorient",
)
# Offered on the kitchen page. stir, scoop and reorient stay in the motion
# code until they succeed the same way as the food sequence.
SKILLS_READY = (
    "observe", "move_named_pose", "move_relative", "set_gripper",
    "pick", "place_on", "seat_in", "pour", "shake_off",
)

LIFT = 0.18
CLEAR = 0.12
# Fingertips sit on the object. tool +X points out through the fingers and is
# joint 6's axis (URDF joint6 xyz="1 0 0" on link6). tool +Y is the jaw opening.
# Bowls are yawed 180°, handle toward the arm. The hand comes straight down
# onto that handle and the jaws close vertically, so joint 6 stays horizontal
# and pouring is a roll of joint 6. Fingers pointed straight down would put
# joint 6 on the vertical axis, and rolling it would only spin the bowl.
# The skimmer handle is the rod on local -X, grasped from the side.
GRASPS = {
    # Bowl handle mesh spans local z 0.035..0.065, so the jaw centers on 0.050.
    # The old 0.042 sat on the lower face and drove that finger through the bar.
    "bowl_support": ((0.120, 0.0, 0.050), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "bowl_support_2": ((0.120, 0.0, 0.050), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "skimmer": ((-0.064, 0.0, -0.015), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}
# Inner-face |y| when shut. Just outside the handle, so the pads touch it
# instead of closing to a fixed 16 mm gap that cuts into the thicker bar.
GRASP_GAP = {
    "bowl_support": 0.016,
    "bowl_support_2": 0.016,
    "skimmer": 0.013,
}
STEP = 0.04
JOINT_STEP = 0.15
# A longer trail restarts mid-carry, so it no longer leads back and is not retraced.
TRAIL_LIMIT = 4000
# Travel between skills: joint spacing of checked samples, tree step, search time
# per goal, and how far a held vessel may lean on the way.
TRANSIT_CHECK = 0.04
TRANSIT_EXTEND = 0.3
TRANSIT_BUDGET = 1.5
# When a fixed pattern already works, the search only looks for something shorter.
TRANSIT_QUICK = 0.8
TRANSIT_TILT = np.radians(20.0)
# Shoulder joints swing the most mass, so a nearer goal is judged mostly on them.
JOINT_WEIGHT = np.array([1.6, 1.5, 1.2, 0.8, 0.6, 0.4])
DT = 0.02
GRAVITY = 9.81
TILT = 115.0
STEP_ANGLE = 5.0


def _rotation(axis, degrees):
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    x, y, z = axis
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]], dtype=float)
    angle = np.radians(degrees)
    return np.eye(3) + np.sin(angle) * skew + (1 - np.cos(angle)) * (skew @ skew)


def _blend_yaw(start, stop, t):
    """Interpolate the upright yaw from ``start`` to ``stop``."""
    relative = np.asarray(start, dtype=float).T @ np.asarray(stop, dtype=float)
    angle = float(np.degrees(np.arctan2(relative[1, 0], relative[0, 0])))
    return _rotation([0.0, 0.0, 1.0], angle * t) @ np.asarray(start, dtype=float)


def _line(start, stop, step=STEP):
    delta = np.asarray(stop, dtype=float) - np.asarray(start, dtype=float)
    count = max(1, int(np.ceil(np.linalg.norm(delta) / step)))
    return [np.asarray(start, dtype=float) + delta * index / count for index in range(1, count + 1)]


def _pinched(vertices, amount, gap):
    """Move the finger vertices inward. ``amount`` 0 is open, 1 stops at ``gap``.

    ``gap`` is the inner-face distance from the jaw center. It has to stay
    outside the handle or the bar passes through the finger mesh.
    """
    vs = np.array(vertices, dtype=np.float32, copy=True)
    if amount <= 0:
        return vs
    finger = (np.abs(vs[:, 1]) > 0.025) & (vs[:, 0] > 0.06)
    moved = vs[finger, 1] - np.sign(vs[finger, 1]) * np.float32(0.037 * amount)
    positive = moved > 0
    limit = np.float32(gap + (0.036 - gap) * (1.0 - amount))
    moved[positive] = np.maximum(moved[positive], limit)
    moved[~positive] = np.minimum(moved[~positive], -limit)
    vs[finger, 1] = moved
    return vs


def _prepare_jaws(model):
    """The gripper mesh has no joint, so open and closed copies are swapped in.

    Every copy is its own geometry, including the open one. The viewer uploads
    a mesh when the geometry object changes, and the loader mesh is shared by
    every arm, so the open fingers have to be a separate object too.
    """
    from wrs.geom.geometry import _Geom

    link = next(item for item in model.robot.runtime_lnks if item.name == "link6")
    visual = link.visuals[0]
    geom = visual.geom
    kit = getattr(geom, "_jaw_kit", None)
    if kit is None:
        base = np.array(geom.vs, dtype=np.float32, copy=True)
        faces = np.array(geom.fs, copy=True)
        open_geom = _Geom(vs=base.copy(), fs=faces.copy())
        mid, closed = {}, {}
        for gap in sorted(set(GRASP_GAP.values())):
            mid[gap] = _Geom(vs=_pinched(base, 0.5, gap), fs=faces.copy())
            closed[gap] = _Geom(vs=_pinched(base, 1.0, gap), fs=faces.copy())
        kit = (open_geom, mid, closed)
        geom._jaw_kit = kit
    open_geom, mid, closed = kit
    visual.geom = open_geom
    return visual, open_geom, mid, closed


def add_box(model, name, pos, size, rgb=(0.42, 0.44, 0.48)):
    """Add a solid box to a loaded scene. Used by the collision probe."""
    rotmat = np.eye(3)
    obj = model.wrs.wssop.box(
        pos=pos, rotmat=rotmat, xyz_lengths=size, rgb=rgb, alpha=1.0, name=name,
    )
    obj.add_to_scene(model.scene)
    data = ObjectData(
        source="configuration", label=name, pos=list(pos), rotmat=rotmat.tolist(),
        geometry={"kind": "box", "xyz_lengths": list(size)}, rgb=list(rgb),
    )
    model.objects[name] = (data, obj)
    return obj


class _Collision:
    def __init__(self, model):
        self.model = model
        self.model_held = None
        # A model built from recognition brings its own bodies; the mesh kitchen reads the file.
        bodies = getattr(model, "bodies", None)
        self.bodies = load_bodies() if bodies is None else bodies
        self.arm = load_arm()
        self.links = {link.name: link for link in model.robot.runtime_lnks}
        missing = [name for name in self.arm if name not in self.links]
        if missing:
            raise SkillFailure(f"arm_link_missing:{','.join(missing)}")
        self.arm_names = set(self.arm)
        self.rest_pairs = self._pairs({})

    def parts(self, name):
        if name in self.bodies:
            return self.bodies[name]
        data = self.model.objects[name][0]
        geometry = data.geometry
        if geometry is not None and geometry.kind == "box":
            size = np.asarray(geometry.xyz_lengths, dtype=float)
            return ((np.zeros(3), size),), None, False
        return (), None, False

    def pose(self, name, overrides):
        if name in overrides:
            pos, rot = overrides[name]
            return np.asarray(pos, dtype=float), np.asarray(rot, dtype=float)
        obj = self.model.objects[name][1]
        return np.asarray(obj.pos, dtype=float).copy(), np.asarray(obj.rotmat, dtype=float).copy()

    def _boxes(self, name, overrides):
        pos, rot = self.pose(name, overrides)
        for center, size in self.parts(name)[0]:
            yield pos + rot @ center, rot, np.asarray(size, dtype=float) / 2

    def _pairs(self, overrides):
        arm_boxes = []
        for name, (center, half) in self.arm.items():
            tf = np.asarray(self.links[name].tf, dtype=float)
            rot = tf[:3, :3]
            arm_boxes.append((name, tf[:3, 3] + rot @ center, rot, half))
        object_boxes = []
        for name in self.model.objects:
            for box in self._boxes(name, overrides):
                object_boxes.append((name, *box))
        found = set()
        for link, center, rot, half in arm_boxes:
            for name, other_center, other_rot, other_half in object_boxes:
                if boxes_overlap(center, rot, half, other_center, other_rot, other_half):
                    found.add(frozenset((link, name)))
        for index, (name, center, rot, half) in enumerate(object_boxes):
            for other_name, other_center, other_rot, other_half in object_boxes[index + 1:]:
                if name == other_name:
                    continue
                if boxes_overlap(center, rot, half, other_center, other_rot, other_half):
                    found.add(frozenset((name, other_name)))
        return found

    def hits(self, qs, overrides, moving, sink, family, enter=(), depart=()):
        self.model.apply(qs)
        enter = set(enter)
        depart = set(depart)
        bad = []
        for pair in self._pairs(overrides):
            if pair in self.rest_pairs or pair in family:
                continue
            names = set(pair)
            # The wrist is allowed to meet the object it is holding.
            if names & self.arm_names and names & moving:
                continue
            if names & moving and names & sink:
                continue
            # Pieces may fall into a vessel. The held body and the arm may not.
            if names & enter and not (names & self.arm_names) and self.model_held not in names:
                continue
            # Leaving the stand the object was sitting on. The arm still may not pass through it.
            if names & depart and names & moving and not (names & self.arm_names):
                continue
            if names & moving or (names & self.arm_names and not names <= self.arm_names):
                bad.append(pair)
        return bad

    def z_bounds(self, name, overrides=None):
        pos, rot = self.pose(name, overrides or {})
        lows, highs = [], []
        for center, size in self.parts(name)[0]:
            world = pos + rot @ center
            half_z = abs(rot[2]) @ (np.asarray(size, dtype=float) / 2)
            lows.append(world[2] - half_z)
            highs.append(world[2] + half_z)
        if not lows:
            pos, _ = self.pose(name, overrides or {})
            return float(pos[2]), float(pos[2])
        return float(min(lows)), float(max(highs))

    def contains(self, name, point, overrides=None):
        cavity = self.parts(name)[1]
        if cavity is None:
            return False
        pos, rot = self.pose(name, overrides or {})
        return point_in_cavity(point, pos, rot, cavity)

    def floor_z(self, name, xy, overrides=None):
        """World height of the inner floor when ``xy`` is over the opening."""
        cavity = self.parts(name)[1]
        if cavity is None:
            return None
        pos, rot = self.pose(name, overrides or {})
        center, size = cavity
        local = rot.T @ (np.array([xy[0], xy[1], pos[2]], dtype=float) - pos)
        if abs(local[0] - center[0]) > size[0] / 2 or abs(local[1] - center[1]) > size[1] / 2:
            return None
        floor = pos + rot @ np.array([center[0], center[1], center[2] - size[2] / 2])
        return float(floor[2])


def jaw_mesh(open_geom, mid, closed, amount, gap):
    """Pick the gripper mesh for a jaw amount. 0 is open, 1 meets the handle."""
    amount = float(np.clip(amount, 0.0, 1.0))
    chosen = min(closed, key=lambda item: abs(item - float(gap)))
    if amount < 0.25:
        return open_geom
    if amount < 0.75:
        return mid[chosen]
    return closed[chosen]


class KitchenMotion:
    """Owned by the caller thread. Each plan restores the arm it searched with."""

    def __init__(self, model):
        self.model = model
        self.rng = np.random.default_rng(1)
        # Path search draws from its own stream, so the fixed patterns do not shift it.
        self.search_rng = np.random.default_rng(2)
        # Per instance, so a recognized scene can put a measured grasp on any object.
        self.grasps = dict(GRASPS)
        self.grasp_gaps = dict(GRASP_GAP)
        self._jaw_visual, self._jaw_open, self._jaw_mid, self._jaw_closed = _prepare_jaws(model)
        self.jaw = 0.0
        self.jaw_gap = 0.016
        self._relax_limits()
        robot = model.robot
        chain, _, base_tf, loc = robot._resolve_ik_target("main", model.tcp)
        self._solver = robot.get_solver(chain)
        self._base_tf = np.asarray(base_tf, dtype=float)
        self._loc = np.asarray(loc, dtype=float)
        self.collision = _Collision(model)
        self.carriers = self._find_carriers()
        self.family = {frozenset((child, parent)) for child, parent in self.carriers.items()}
        self.locals = {
            child: self._relative(parent, child) for child, parent in self.carriers.items()
        }
        self.rest = {name: self._pose(name) for name in model.objects}
        self.support = self._find_supports()
        self.home = {name: self.support.get(name) or self._stand_under(name) for name in model.objects}
        # Frames stepped since the held object was picked, so a place can retrace them.
        self.trail = []
        self._relation_key = None
        self._relations = {}
        self.held = None
        self.offset = None
        self.held_rot = None
        self.grasp = None
        self.path = []
        self.kind = None
        self.focus = None
        self.goal_orientation = None

    def _relax_limits(self):
        """Let this kitchen sim use a wider joint range than the hardware URDF.

        The numerical solver reads the chain limits live, and ``model.apply``
        otherwise rejects anything outside the URDF. Both have to move together.
        """
        margin = np.array([0.55, 0.45, 0.55, 0.85, 0.65, 0.85], dtype=np.float32)
        chain = self.model.chain
        chain.lmt_lo[:] = np.asarray(chain.lmt_lo, dtype=np.float32) - margin
        chain.lmt_up[:] = np.asarray(chain.lmt_up, dtype=np.float32) + margin
        self.limits = np.column_stack((
            np.asarray(chain.lmt_lo, dtype=float),
            np.asarray(chain.lmt_up, dtype=float),
        ))
        self.model.limits = self.limits
        limits = self.limits
        robot = self.model.robot
        indices = self.model.indices

        def apply(q):
            q = np.asarray(q, dtype=float)
            if q.shape != (6,) or not np.isfinite(q).all():
                raise ValueError("关节角必须是 6 个有限数值。")
            if np.any(q < limits[:, 0] - 1e-5) or np.any(q > limits[:, 1] + 1e-5):
                raise ValueError("示例关节角超出了放宽后的限位。")
            full = np.zeros_like(robot.qs)
            full[indices] = q
            robot.fk(full)

        self.model.apply = apply

    def _snapshot(self):
        return (
            self._qs(),
            {name: self._pose(name) for name in self.model.objects},
            self.held,
            None if self.offset is None else np.asarray(self.offset, dtype=float).copy(),
            None if self.held_rot is None else np.asarray(self.held_rot, dtype=float).copy(),
            None if self.grasp is None else np.asarray(self.grasp, dtype=float).copy(),
            dict(self.carriers),
            deepcopy(self.locals),
        )

    def _rollback(self, saved):
        qs, poses, held, offset, rot, grasp, carriers, locals_ = saved
        self.held, self.offset, self.held_rot, self.grasp = held, offset, rot, grasp
        self.carriers, self.locals = carriers, locals_
        self.model.apply(qs)
        for name, (pos, rotmat) in poses.items():
            if name in self.model.objects:
                self._set(name, pos, rotmat)

    def plan_skill(self, name, arguments):
        """Validate ``arguments`` with the registered skill, then plan it."""
        if name not in SKILLS:
            raise SkillFailure("unknown_skill")
        if name not in SKILLS_HANDLED:
            raise SkillFailure("skill_not_simulated")
        args = SKILLS[name].arguments.model_validate(arguments)
        return getattr(self, f"plan_{name}")(args)

    def _spawn_liquid(self):
        """Water cubes for the measuring cup. Not used by the food sequence."""
        bodies = load_bodies()
        wrs = self.model.wrs
        for name, (parts, cavity, liquid) in bodies.items():
            if not liquid or cavity is None or name not in self.model.objects:
                continue
            pos, rot = self._pose(name)
            center, size = cavity
            index = 0
            for x in (-0.25, 0.25):
                for y in (-0.25, 0.25):
                    for z in (0.25, 0.55):
                        local = center + np.array([x, y, z]) * size * np.array([0.45, 0.45, 0.35])
                        world = pos + rot @ local
                        index += 1
                        drop = f"liquid_{name}_{index}"
                        length = [0.012, 0.012, 0.012]
                        obj = wrs.wssop.box(
                            pos=world, rotmat=np.eye(3), xyz_lengths=length,
                            rgb=[0.45, 0.74, 0.95], alpha=0.5, name=drop,
                        )
                        obj.add_to_scene(self.model.scene)
                        data = ObjectData(
                            source="configuration", label="液体", pos=world.tolist(),
                            rotmat=np.eye(3).tolist(),
                            geometry={"kind": "box", "xyz_lengths": length},
                            rgb=[0.45, 0.74, 0.95], alpha=0.5,
                        )
                        self.model.objects[drop] = (data, obj)

    def _pose(self, name):
        obj = self.model.objects[name][1]
        return np.asarray(obj.pos, dtype=float).copy(), np.asarray(obj.rotmat, dtype=float).copy()

    def _set(self, name, pos, rot):
        data, obj = self.model.objects[name]
        pos = np.asarray(pos, dtype=float)
        rot = np.asarray(rot, dtype=float)
        obj.set_pos_rotmat(pos, rot)
        self.model.objects[name] = (data.model_copy(update={
            "pos": [float(value) for value in pos],
            "rotmat": [[float(value) for value in row] for row in rot],
        }), obj)

    def _qs(self):
        return np.asarray(self.model.robot.qs[self.model.indices], dtype=float).copy()

    def _tcp(self):
        return np.asarray(self.model.tcp.tf[:3, 3], dtype=float).copy()

    def _relative(self, parent, child):
        parent_pos, parent_rot = self._pose(parent)
        child_pos, child_rot = self._pose(child)
        return parent_rot.T @ (child_pos - parent_pos), parent_rot.T @ child_rot

    def _find_carriers(self):
        carriers = {}
        for name in self.model.objects:
            point, _ = self._pose(name)
            for vessel in self.model.objects:
                if vessel != name and self.collision.contains(vessel, point):
                    carriers[name] = vessel
                    break
        return carriers

    def _find_supports(self):
        supports = {}
        for name in self.model.objects:
            if name in self.carriers:
                supports[name] = self.carriers[name]
                continue
            bottom, _ = self.collision.z_bounds(name)
            pos, _ = self._pose(name)
            best, gap = None, 0.03
            for other in self.model.objects:
                if other == name:
                    continue
                _, top = self.collision.z_bounds(other)
                other_pos, _ = self._pose(other)
                if np.linalg.norm(other_pos[:2] - pos[:2]) > 0.2:
                    continue
                clearance = bottom - top
                if 0 <= clearance < gap:
                    best, gap = other, clearance
            supports[name] = best
        return supports

    def _children(self, name):
        return [child for child, parent in self.carriers.items() if parent == name]

    def _family(self):
        return {frozenset((child, parent)) for child, parent in self.carriers.items()}

    def _hits(self, *args, **kwargs):
        self.collision.model_held = self.held
        return self.collision.hits(*args, **kwargs)

    def _grasp_gap(self, name):
        return self.grasp_gaps.get(name, 0.016)

    def _set_jaw(self, amount, gap=None):
        """0 is open, 1 is closed on the handle. The arm box narrows with it."""
        self.jaw = float(np.clip(amount, 0.0, 1.0))
        if gap is not None:
            self.jaw_gap = float(gap)
        self._jaw_visual.geom = jaw_mesh(
            self._jaw_open, self._jaw_mid, self._jaw_closed, self.jaw, self.jaw_gap,
        )
        _center, half = self.collision.arm["link6"]
        half = np.array(half, dtype=float, copy=True)
        half[1] = 0.075 * (1.0 - self.jaw) + 0.041 * self.jaw
        self.collision.arm["link6"] = (_center, half)

    def _overlaps(self, left, right, poses, pad=0.006):
        """True when the solid boxes of two objects occupy the same space."""
        for box_a in self.collision._boxes(left, poses):
            for box_b in self.collision._boxes(right, poses):
                if boxes_overlap(*box_a, *box_b, pad=pad):
                    return True
        return False

    def _pour_axis(self, name):
        """Horizontal local axis the contents leave along.

        A bowl's handle is on local +X, so the food goes out over the opposite rim.
        """
        if name.startswith("bowl_"):
            return np.array([-1.0, 0.0, 0.0])
        return np.array([1.0, 0.0, 0.0])

    def _seeds(self, seed):
        yield np.asarray(seed, dtype=float)
        for delta in (0.35, -0.35):
            nudged = np.array(seed, dtype=float, copy=True)
            nudged[1] = np.clip(nudged[1] + delta, self.limits[1, 0], self.limits[1, 1])
            nudged[2] = np.clip(nudged[2] - delta, self.limits[2, 0], self.limits[2, 1])
            yield nudged
        for _ in range(2):
            yield self.rng.uniform(self.limits[:, 0], self.limits[:, 1])

    def _ik(self, point, seed):
        found = self._solver.ik_partial(
            root_rotmat=self._base_tf[:3, :3],
            root_pos=self._base_tf[:3, 3],
            tgt_pos=np.asarray(point, dtype=np.float32),
            loc_tf=self._loc,
            qs_active_init=np.asarray(seed, dtype=np.float32),
            max_iter=50,
        )
        if not found:
            return None
        q = np.clip(np.asarray(found[0], dtype=float), self.limits[:, 0], self.limits[:, 1])
        return q if np.isfinite(q).all() else None

    def _solve(self, point, seed):
        best = None
        for guess in self._seeds(seed):
            q = self._ik(point, guess)
            if q is None:
                continue
            jump = float(np.max(np.abs(q - seed)))
            if jump <= 0.8:
                return q
            if best is None or jump < best[0]:
                best = (jump, q)
        return None if best is None else best[1]

    def _ik_pose(self, pos, rot, seed):
        solutions = self.model.robot.ik(
            np.asarray(pos, dtype=np.float32),
            np.asarray(rot, dtype=np.float32),
            tcp=self.model.tcp,
            qs_active_init=np.asarray(seed, dtype=np.float32),
            max_iter=70,
            max_solutions=1,
        )
        if not solutions:
            return None
        for item in solutions:
            full = np.asarray(item, dtype=float)
            active = full[self.model.indices] if full.shape[0] != 6 else full
            if not np.isfinite(active).all():
                continue
            return np.clip(active, self.limits[:, 0], self.limits[:, 1])
        return None

    def _solve_pose(self, pos, rot, seed, *, allow_roll=False, wide=False, aim_tol=0.25):
        """Full-pose IK. The fingertip has to land on ``pos``, not merely nearby."""
        pos = np.asarray(pos, dtype=float)
        base = np.asarray(rot, dtype=float)
        rots = [base]
        if allow_roll:
            for degrees in (25, -25, 50, -50, 80, -80, 120, -120, 180):
                rots.append(_rotation(base[:, 0], degrees) @ base)
        guesses = [np.asarray(seed, dtype=float)]
        if wide:
            guesses.extend(self._seeds(seed))
            for _ in range(8):
                guesses.append(self.rng.uniform(self.limits[:, 0], self.limits[:, 1]))
        best = None
        for candidate in rots:
            for guess in guesses:
                q = self._ik_pose(pos, candidate, guess)
                if q is None:
                    continue
                self.model.apply(q)
                reached = self._tcp()
                miss = float(np.linalg.norm(reached - pos))
                aimed = float(np.linalg.norm(np.asarray(self.model.tcp.tf[:3, 0], dtype=float) - candidate[:, 0]))
                if miss <= 0.012 and aimed <= aim_tol:
                    jump = float(np.max(np.abs(q - seed)))
                    if jump <= 1.6 or wide:
                        return q
                if best is None or miss < best[0]:
                    best = (miss, aimed, q)
            if not allow_roll:
                break
        if best is not None and best[0] <= 0.012 and best[1] <= aim_tol:
            return best[2]
        return None

    def _tool_rot(self, tool_x, tool_y):
        x = np.asarray(tool_x, dtype=float)
        x = x / np.linalg.norm(x)
        y = np.asarray(tool_y, dtype=float)
        y = y - x * float(np.dot(x, y))
        y = y / np.linalg.norm(y)
        return np.column_stack((x, y, np.cross(x, y)))

    def _contact(self, name, pos, rot):
        """Fingertip position and tool rotation that put the jaw on ``name``."""
        spec = self.grasps.get(name)
        if spec is None:
            _, top = self.collision.z_bounds(name)
            point = np.array([pos[0], pos[1], top + 0.004])
            return point, self._tool_rot([0.0, 0.0, -1.0], [0.0, 1.0, 0.0])
        local, axis_x, axis_y = spec
        point = np.asarray(pos, dtype=float) + rot @ np.asarray(local, dtype=float)
        return point, self._tool_rot(rot @ np.asarray(axis_x, dtype=float), rot @ np.asarray(axis_y, dtype=float))

    def _lock_grasp(self, obj_pos, obj_rot):
        tcp = np.eye(4)
        tcp[:3, :3] = np.asarray(self.model.tcp.tf[:3, :3], dtype=float)
        tcp[:3, 3] = self._tcp()
        obj = np.eye(4)
        obj[:3, :3] = np.asarray(obj_rot, dtype=float)
        obj[:3, 3] = np.asarray(obj_pos, dtype=float)
        self.grasp = np.linalg.inv(tcp) @ obj

    def _tcp_from_object(self, pos, rot):
        obj = np.eye(4)
        obj[:3, :3] = np.asarray(rot, dtype=float)
        obj[:3, 3] = np.asarray(pos, dtype=float)
        tcp = obj @ np.linalg.inv(self.grasp)
        return tcp[:3, 3], tcp[:3, :3]

    def _move_aimed(self, points, rot, reason, touch, close_at=None, *, lined=False):
        """Move onto ``touch``. The jaw stays open until it is close, then shuts.

        Only the object itself may meet the fingers. The stand under it stays solid.
        ``lined`` starts from the current tool pose and only follows ``points``.
        """
        seed = self._qs()
        frames = []
        moving = {touch, *self._children(touch)}
        depart = set()
        support = self.support.get(touch)
        if support is not None:
            depart.add(support)
        # The cradle under the skimmer overlaps the basket. Contents that fell
        # in later may sit in that same volume. The arm still may not.
        for pair in self.collision.rest_pairs:
            if touch in pair:
                depart.update(pair - {touch})
        family = set(self._family())
        children = [child for child in moving if child != touch]
        for index, left in enumerate(children):
            for right in children[index + 1:]:
                family.add(frozenset((left, right)))
        first = np.asarray(points[0], dtype=float)

        def consider(sample):
            if close_at is None:
                return
            self.model.apply(sample)
            dist = float(np.linalg.norm(self._tcp() - close_at))
            self._set_jaw(
                0.0 if dist > 0.06 else min(1.0, (0.06 - dist) / 0.05),
                self._grasp_gap(touch),
            )

        down = float(np.asarray(rot, dtype=float)[2, 0]) < -0.9
        pinch_up = abs(float(np.asarray(rot, dtype=float)[2, 1])) > 0.7
        tol = 0.08 if down else (0.15 if pinch_up else 0.25)
        if lined:
            # Already above the handle in the grasp orientation: descend along the line.
            self.model.apply(seed)
            rot = np.asarray(self.model.tcp.tf[:3, :3], dtype=float).copy()
            cursor = self._tcp().copy()
            remaining = list(points)
            pinch_up = down = False
        else:
            q = self._solve_pose(first, rot, seed, allow_roll=False, wide=True, aim_tol=tol)
            if q is None and not down and not pinch_up:
                q = self._solve_pose(first, rot, seed, allow_roll=True, wide=True)
            if q is None:
                raise SkillFailure(reason)
            self.model.apply(q)
            rot = np.asarray(self.model.tcp.tf[:3, :3], dtype=float).copy()
            self.model.apply(seed)
        if pinch_up or down:
            count = max(1, int(np.ceil(np.max(np.abs(q - seed)) / JOINT_STEP)))
            for index in range(1, count + 1):
                sample = seed + (q - seed) * index / count
                consider(sample)
                if self._hits(sample, {}, moving, set(), family, depart=depart):
                    raise SkillFailure(reason)
                frames.append(self._frame(sample, {}, None))
            seed = q
            cursor = first
            remaining = points[1:]
        elif not lined:
            # Follow the waypoints in short steps. One joint leap from the
            # bowl swings the open gripper through the skimmer.
            cursor = self._tcp().copy()
            remaining = points
        for point in remaining:
            for target in _line(cursor, point):
                q = self._solve_pose(target, rot, seed)
                if q is None:
                    q = self._solve_pose(target, rot, seed, wide=True)
                if q is None:
                    raise SkillFailure(reason)
                count = max(1, int(np.ceil(np.max(np.abs(q - seed)) / JOINT_STEP)))
                for index in range(1, count + 1):
                    sample = seed + (q - seed) * index / count
                    consider(sample)
                    if self._hits(sample, {}, moving, set(), family, depart=depart):
                        raise SkillFailure(reason)
                    frames.append(self._frame(sample, {}, None))
                seed = q
            cursor = np.asarray(point, dtype=float)
        return frames

    def _transit_check(self, touch=(), sink=(), enter=(), depart=(), apart=(), upright=None):
        """Fast clear test for travel, with the pair rules of ``_hits``.

        Only the arm links and the held object with its contents move. Pairs of
        resting objects cannot change on the way, so they are not tested.
        ``touch`` may meet the arm (the handle about to be grasped). ``upright``
        bounds how far the held object's up axis may turn from where it started.
        """
        held = self.held
        carried = set() if held is None else {held, *self._children(held)}
        locals_ = {} if held is None else {
            child: self.locals[child] for child in self._children(held) if child in self.locals
        }
        allowed = set(touch) | carried
        family = set(self._family())
        rest_pairs = self.collision.rest_pairs
        sink, enter, depart, apart = set(sink), set(enter), set(depart), set(apart)
        arm_names = self.collision.arm_names
        static = []
        for name in self.model.objects:
            if name in carried:
                continue
            for center, rot, half in self.collision._boxes(name, {}):
                static.append((name, center, rot, half, float(np.linalg.norm(half))))

        def ignored(mover, other):
            pair = frozenset((mover, other))
            if pair in rest_pairs or pair in family:
                return True
            if mover in arm_names:
                return other in allowed
            return other in sink or other in depart or (other in enter and mover != held)

        targets = {
            mover: [box for box in static if not ignored(mover, box[0])]
            for mover in (*self.collision.arm, *carried)
        }
        keep_apart = [box for box in static if box[0] in apart]
        start_up = None
        if held is not None and upright is not None and self.held_rot is not None:
            start_up = np.asarray(self.held_rot, dtype=float)[:, 2].copy()
        min_up = float(np.cos(upright)) if upright is not None else -1.0

        def free(q):
            if held is None:
                self.model.apply(q)
                poses = {}
            else:
                poses = self._attached(q, held, self.offset, self.held_rot, locals_)
            movers = []
            for name, (center, half) in self.collision.arm.items():
                tf = np.asarray(self.collision.links[name].tf, dtype=float)
                rot = tf[:3, :3]
                movers.append((name, tf[:3, 3] + rot @ center, rot, np.asarray(half, dtype=float)))
            for name in carried:
                for box in self.collision._boxes(name, poses):
                    movers.append((name, *box))
            for name, center, rot, half in movers:
                # Bounding spheres first; the margin covers the box test's pad on both sides.
                reach = float(np.linalg.norm(half)) + 0.004
                for _other, other_center, other_rot, other_half, other_reach in targets[name]:
                    if float(np.linalg.norm(center - other_center)) > reach + other_reach:
                        continue
                    if boxes_overlap(center, rot, half, other_center, other_rot, other_half):
                        return False
            if held is not None:
                if keep_apart:
                    for center, rot, half in self.collision._boxes(held, poses):
                        for _other, other_center, other_rot, other_half, _ in keep_apart:
                            if boxes_overlap(center, rot, half, other_center, other_rot, other_half, pad=0.006):
                                return False
                if start_up is not None:
                    if float(poses[held][1][:, 2] @ start_up) < min_up:
                        return False
            return True

        return free

    def _clear_edge(self, start, goal, free):
        start = np.asarray(start, dtype=float)
        goal = np.asarray(goal, dtype=float)
        count = max(1, int(np.ceil(float(np.max(np.abs(goal - start))) / TRANSIT_CHECK)))
        return all(free(start + (goal - start) * index / count) for index in range(1, count + 1))

    def _goal_configs(self, pos, rot, seed, free, aim_tol=0.25, limit=6):
        """Clear IK solutions for one tool pose, the one nearest ``seed`` first."""
        pos = np.asarray(pos, dtype=float)
        rot = np.asarray(rot, dtype=float)
        seed = np.asarray(seed, dtype=float)
        # Many spread-out guesses, so the nearest arm posture is found every time
        # instead of whichever one a few random tries happen to land on.
        guesses = list(islice(self._seeds(seed), 3))
        guesses += [self.search_rng.uniform(self.limits[:, 0], self.limits[:, 1]) for _ in range(24)]
        found = []
        for guess in guesses:
            q = self._ik_pose(pos, rot, guess)
            if q is None:
                continue
            self.model.apply(q)
            if float(np.linalg.norm(self._tcp() - pos)) > 0.012:
                continue
            if float(np.linalg.norm(np.asarray(self.model.tcp.tf[:3, 0], dtype=float) - rot[:, 0])) > aim_tol:
                continue
            if any(float(np.max(np.abs(q - other))) < 0.05 for other in found):
                continue
            if free(q):
                found.append(q)
        self.model.apply(seed)
        found.sort(key=lambda q: float(np.sum(np.abs(q - seed) * JOINT_WEIGHT)))
        return found[:limit]

    def _transit(self, goal, free, budget=TRANSIT_BUDGET):
        """Joints from here to ``goal`` that ``free`` accepts, start excluded, or None.

        A straight blend when it is clear. Otherwise RRT-Connect, then every
        corner a straight blend can skip is cut, so no detour is left over.
        """
        start = self._qs()
        goal = np.asarray(goal, dtype=float)
        try:
            if self._clear_edge(start, goal, free):
                path = [start, goal]
            else:
                path = self._connect(start, goal, free, budget)
                if path is None:
                    return None
                path = self._shortcut(path, free)
        finally:
            self.model.apply(start)
        qs = []
        for left, right in zip(path, path[1:]):
            count = max(1, int(np.ceil(float(np.max(np.abs(right - left))) / JOINT_STEP)))
            qs.extend(left + (right - left) * index / count for index in range(1, count + 1))
        return qs

    def _connect(self, start, goal, free, budget):
        lo, hi = self.limits[:, 0], self.limits[:, 1]
        trees = [([start], [-1]), ([goal], [-1])]
        deadline = time.perf_counter() + budget

        def extend(tree, target):
            nodes, parents = tree
            near = int(np.argmin([float(np.linalg.norm(node - target)) for node in nodes]))
            delta = target - nodes[near]
            span = float(np.linalg.norm(delta))
            if span < 1e-6:
                return near, True
            reached = span <= TRANSIT_EXTEND
            new = target if reached else nodes[near] + delta * (TRANSIT_EXTEND / span)
            if not self._clear_edge(nodes[near], new, free):
                return None, False
            nodes.append(np.asarray(new, dtype=float))
            parents.append(near)
            return len(nodes) - 1, reached

        def branch(tree, index):
            nodes, parents = tree
            out = []
            while index >= 0:
                out.append(nodes[index])
                index = parents[index]
            return out

        grow = 0
        while time.perf_counter() < deadline:
            tree, other = trees[grow], trees[1 - grow]
            added, _ = extend(tree, self.search_rng.uniform(lo, hi))
            if added is not None:
                target = tree[0][added]
                while True:
                    joined, reached = extend(other, target)
                    if joined is None:
                        break
                    if reached:
                        here = branch(tree, added)[::-1]
                        there = branch(other, joined)
                        path = here + there[1:]
                        return path if grow == 0 else path[::-1]
            grow = 1 - grow
        return None

    def _shortcut(self, path, free):
        path = [np.asarray(node, dtype=float) for node in path]
        smoothed = [path[0]]
        index = 0
        while index < len(path) - 1:
            for far in range(len(path) - 1, index, -1):
                if far == index + 1 or self._clear_edge(path[index], path[far], free):
                    smoothed.append(path[far])
                    index = far
                    break
        path = smoothed
        for _ in range(40):
            if len(path) < 3:
                break
            # Cut across a corner: points part way along two different legs.
            first = int(self.search_rng.integers(0, len(path) - 1))
            second = int(self.search_rng.integers(0, len(path) - 1))
            if abs(first - second) < 1:
                continue
            first, second = sorted((first, second))
            t, u = float(self.search_rng.uniform()), float(self.search_rng.uniform())
            a = path[first] + (path[first + 1] - path[first]) * t
            b = path[second] + (path[second + 1] - path[second]) * u
            if self._clear_edge(a, b, free):
                path = path[:first + 1] + [a, b] + path[second + 1:]
        return path

    def _travel_cost(self, start, frames):
        """Weighted joint distance the frames sweep, starting from ``start``."""
        cost = 0.0
        previous = np.asarray(start, dtype=float)
        for frame in frames:
            q = np.asarray(frame["qs"], dtype=float)
            cost += float(np.sum(np.abs(q - previous) * JOINT_WEIGHT))
            previous = q
        return cost

    def _travel_frames(self, qs):
        """Playback frames for a transit. A held object rides on the locked grasp."""
        held = self.held
        locals_ = {} if held is None else {
            child: self.locals[child] for child in self._children(held) if child in self.locals
        }
        frames = []
        for q in qs:
            poses = {}
            if held is not None:
                poses = self._attached(q, held, self.offset, self.held_rot, locals_)
                self.held_rot = poses[held][1]
            frames.append(self._frame(q, poses, held))
        return frames

    def _approach(self, pre, contact, rot, reason, touch, quick=False):
        """Travel to above the handle along a clear joint path, then descend onto it.

        ``quick`` only looks for a shorter way than an approach that already works.
        """
        seed = self._qs()
        free = self._transit_check(touch={touch, *self._children(touch)})
        down = float(np.asarray(rot, dtype=float)[2, 0]) < -0.9
        pinch_up = abs(float(np.asarray(rot, dtype=float)[2, 1])) > 0.7
        tol = 0.08 if down else (0.15 if pinch_up else 0.25)
        for goal in self._goal_configs(pre, rot, seed, free, aim_tol=tol, limit=4):
            self.model.apply(seed)
            self._set_jaw(0.0, self._grasp_gap(touch))
            qs = self._transit(goal, free, budget=TRANSIT_QUICK if quick else TRANSIT_BUDGET)
            if qs is None:
                continue
            frames = self._travel_frames(qs)
            self.model.apply(goal)
            try:
                frames += self._move_aimed([contact], rot, reason, touch, close_at=contact, lined=True)
            except SkillFailure:
                continue
            return frames
        self.model.apply(seed)
        return None

    def _carry_object(self, waypoints, sink, reason, start=None, apart=(), enter=(), depart=()):
        """Move the held object. The jaw stays on the point it closed on.

        ``apart`` names objects the held mesh must not intersect, even when
        those objects are in ``sink`` so the arm and the falling contents may
        pass near them.
        """
        seed = self._qs()
        if start is None:
            cursor_pos, cursor_rot = self._pose(self.held)
        else:
            cursor_pos, cursor_rot = start
        frames = []
        moving = {self.held, *self._children(self.held)}
        family = set(self._family())
        children = [child for child in moving if child != self.held]
        for index, left in enumerate(children):
            for right in children[index + 1:]:
                family.add(frozenset((left, right)))
        locals_ = {
            child: self.locals[child]
            for child in self._children(self.held) if child in self.locals
        }
        for pos, rot in waypoints:
            pos = np.asarray(pos, dtype=float)
            rot = np.asarray(rot, dtype=float)
            origin = np.asarray(cursor_pos, dtype=float)
            span = float(np.linalg.norm(pos - origin))
            for sample_pos in _line(cursor_pos, pos):
                travelled = float(np.linalg.norm(sample_pos - origin))
                sample_rot = _blend_yaw(cursor_rot, rot, 1.0 if span < 1e-6 else travelled / span)
                tcp_pos, tcp_rot = self._tcp_from_object(sample_pos, sample_rot)
                q = self._solve_pose(tcp_pos, tcp_rot, seed)
                if q is None:
                    q = self._solve_pose(tcp_pos, tcp_rot, seed, wide=True)
                if q is None:
                    raise SkillFailure(reason)
                count = max(1, int(np.ceil(np.max(np.abs(q - seed)) / JOINT_STEP)))
                for index in range(1, count + 1):
                    sample = seed + (q - seed) * index / count
                    poses = self._attached(sample, self.held, self.offset, rot, locals_)
                    if self._hits(sample, poses, moving, sink, family, enter=enter, depart=depart):
                        raise SkillFailure(reason)
                    if any(self._overlaps(self.held, other, poses) for other in apart):
                        raise SkillFailure(reason)
                    self.held_rot = poses[self.held][1]
                    frames.append(self._frame(sample, poses, self.held))
                seed = q
            cursor_pos, cursor_rot = frames[-1]["poses"][self.held]
        return frames

    def _joint_carry(self, goal, rot, sink, reason, apart=(), enter=(), depart=()):
        """Move the held object to one pose along the joints when a straight path is blocked."""
        seed = self._qs()
        tcp_pos, tcp_rot = self._tcp_from_object(goal, rot)
        q = self._solve_pose(tcp_pos, tcp_rot, seed, wide=True)
        if q is None:
            raise SkillFailure(reason)
        moving = {self.held, *self._children(self.held)}
        family = set(self._family())
        children = [child for child in moving if child != self.held]
        for index, left in enumerate(children):
            for right in children[index + 1:]:
                family.add(frozenset((left, right)))
        locals_ = {
            child: self.locals[child]
            for child in children if child in self.locals
        }
        count = max(1, int(np.ceil(np.max(np.abs(q - seed)) / JOINT_STEP)))
        frames = []
        for index in range(1, count + 1):
            sample = seed + (q - seed) * index / count
            poses = self._attached(sample, self.held, self.offset, rot, locals_)
            if self._hits(sample, poses, moving, sink, family, enter=enter, depart=depart):
                raise SkillFailure(reason)
            if any(self._overlaps(self.held, other, poses) for other in apart):
                raise SkillFailure(reason)
            self.held_rot = poses[self.held][1]
            frames.append(self._frame(sample, poses, self.held))
        return frames

    def _attached(self, q, held, offset, rot, locals_):
        self.model.apply(q)
        if self.grasp is not None:
            obj = np.asarray(self.model.tcp.tf, dtype=float) @ self.grasp
            pos = obj[:3, 3]
            rot = obj[:3, :3]
        else:
            pos = self._tcp() + offset
        poses = {held: (pos.copy(), np.asarray(rot, dtype=float).copy())}
        for child, (local_pos, local_rot) in locals_.items():
            if self.carriers.get(child) == held:
                poses[child] = (pos + rot @ local_pos, rot @ local_rot)
        return poses

    def _arm(self, points, *, attached, sink, reason, enter=(), depart=(), strict=False):
        seed = self._qs()
        cursor = self._tcp()
        frames = []
        moving = set()
        locals_ = {}
        hold_rot = None
        if attached:
            moving.add(self.held)
            moving.update(self._children(self.held))
            locals_ = {
                child: self.locals[child]
                for child in self._children(self.held) if child in self.locals
            }
            if self.grasp is not None:
                self.model.apply(seed)
                hold_rot = np.asarray(self.model.tcp.tf[:3, :3], dtype=float).copy()
        family = set(self._family())
        if attached:
            children = [child for child in moving if child != self.held]
            for index, left in enumerate(children):
                for right in children[index + 1:]:
                    family.add(frozenset((left, right)))
        for point in points:
            for target in _line(cursor, point):
                if hold_rot is not None:
                    q = self._solve_pose(target, hold_rot, seed)
                    if q is None:
                        q = self._solve_pose(target, hold_rot, seed, wide=True)
                    if q is None and not strict:
                        q = self._solve_pose(target, hold_rot, seed, allow_roll=True, wide=True)
                else:
                    q = self._solve(target, seed)
                if q is None:
                    raise SkillFailure(reason)
                count = max(1, int(np.ceil(np.max(np.abs(q - seed)) / JOINT_STEP)))
                for index in range(1, count + 1):
                    sample = seed + (q - seed) * index / count
                    poses = (
                        self._attached(sample, self.held, self.offset, self.held_rot, locals_)
                        if attached else {}
                    )
                    if self._hits(sample, poses, moving, sink, family, enter=enter, depart=depart):
                        raise SkillFailure(reason)
                    frames.append(self._frame(sample, poses, self.held if attached else None))
                seed = q
            cursor = np.asarray(point, dtype=float)
        return frames

    def _frame(self, qs, poses, held):
        return {
            "qs": np.asarray(qs, dtype=float).copy(),
            "poses": {
                name: (np.asarray(pos, dtype=float).copy(), np.asarray(rot, dtype=float).copy())
                for name, (pos, rot) in poses.items()
            },
            "held": held,
            "offset": None if self.offset is None else np.asarray(self.offset, dtype=float).copy(),
            "held_rot": None if self.held_rot is None else np.asarray(self.held_rot, dtype=float).copy(),
            "grasp": None if self.grasp is None else np.asarray(self.grasp, dtype=float).copy(),
            "jaw": float(self.jaw),
            "jaw_gap": float(self.jaw_gap),
            "carriers": dict(self.carriers),
            "locals": deepcopy(self.locals),
        }

    def _commit(self, frames, kind, focus):
        start = self._qs()
        try:
            if not frames:
                raise SkillFailure("empty_motion")
        finally:
            self.model.apply(start)
        self.path, self.kind, self.focus = frames, kind, focus
        return len(frames)

    def _require(self, name):
        if name not in self.model.objects:
            raise SkillFailure(f"{name}_not_in_scene")

    def _above(self, name):
        _, top = self.collision.z_bounds(name)
        pos, _ = self._pose(name)
        return np.array([pos[0], pos[1], max(top + CLEAR, 0.36)])

    def plan_pick(self, args):
        name = args.object
        if self.held is not None:
            raise SkillFailure("gripper_already_holding")
        self._require(name)
        if name == "table":
            raise SkillFailure("object_not_graspable")
        # Food cubes ride inside a vessel and have no handle. A skimmer or bowl
        # keeps its grasp even when it is seated in another container.
        if name in self.carriers and name not in self.grasps:
            raise SkillFailure("contents_move_with_container")
        start_carriers = dict(self.carriers)
        try:
            self._set_jaw(0.0)
            obj_pos, obj_rot = self._pose(name)
            contact, tool_rot = self._contact(name, obj_pos, obj_rot)
            tool_x = tool_rot[:, 0]
            # Jaws close straight down onto a bowl handle. A skimmer is a side grasp.
            pinch_up = abs(float(tool_rot[2, 1])) > 0.7
            vertical = float(tool_x[2]) < -0.7
            if pinch_up or vertical:
                # Straight down onto the handle. The jaw closes over the last centimetres.
                pre = contact + np.array([0.0, 0.0, 0.08 if pinch_up else 0.06])
                points = [pre, contact]
            else:
                pre = contact + np.array([0.0, 0.0, 0.10])
                # Side grasp: straight up, across above the handle, then down.
                here = self._tcp()
                high = max(float(here[2]), float(contact[2])) + 0.24
                # Up first, then out in front of the net, then across and down.
                # A single joint move from the bowl cuts through the skimmer.
                front = max(float(here[0]), float(contact[0])) + 0.14
                points = [
                    np.array([here[0], here[1], high]),
                    np.array([front, here[1], high]),
                    np.array([front, contact[1], high]),
                    np.array([contact[0], contact[1], high]),
                    contact,
                ]
            # From wherever the last skill stopped, straight to above the handle when
            # that is clear, around what is in the way when not. The fixed patterns
            # stay as the fallback.
            seed = self._qs()
            try:
                fixed = self._move_aimed(
                    points, tool_rot, "pick_unreachable_or_colliding", name,
                    close_at=contact,
                )
            except SkillFailure:
                fixed = None
            self.model.apply(seed)
            self._set_jaw(0.0)
            searched = self._approach(
                pre, contact, tool_rot, "pick_unreachable_or_colliding", name, quick=fixed is not None,
            )
            if fixed is None and searched is None:
                raise SkillFailure("pick_unreachable_or_colliding")
            options = [frames for frames in (searched, fixed) if frames]
            frames = min(options, key=lambda frames: self._travel_cost(seed, frames))
            self._set_jaw(1.0)
            self.model.apply(frames[-1]["qs"])
            if float(np.linalg.norm(self._tcp() - contact)) > 0.015:
                raise SkillFailure("pick_unreachable_or_colliding")
            self._lock_grasp(obj_pos, obj_rot)
            self.offset = obj_pos - self._tcp()
            self.held_rot = np.asarray(obj_rot, dtype=float).copy()
            self.held = name
            parent = self.carriers.pop(name, None)
            if parent is not None:
                self.locals.pop(name, None)
            # The object may still be nested in the stand it started on. The arm may not.
            depart = {item for item in (parent, self.support.get(name)) if item}
            for pair in self.collision.rest_pairs:
                if name in pair:
                    depart.update(pair - {name})
            # Keep the jaw axis fixed on the way up. A vertical close can
            # still climb; a fingers-down grip cannot.
            rises = (0.14, 0.10, 0.06) if pinch_up or vertical else (LIFT,)
            lifted_ok = False
            grasp_q = frames[-1]["qs"]
            for rise in rises:
                self.model.apply(grasp_q)
                lift = self._tcp() + np.array([0.0, 0.0, rise])
                try:
                    frames += self._arm(
                        [lift], attached=True, sink=set(), reason="pick_unreachable_or_colliding",
                        depart=depart, strict=pinch_up or vertical,
                    )
                    lifted_ok = True
                    break
                except SkillFailure:
                    self.model.apply(grasp_q)
            if not lifted_ok:
                raise SkillFailure("pick_unreachable_or_colliding")
            self.held = None
            self.carriers = start_carriers
        finally:
            self.offset, self.held_rot, self.grasp = None, None, None
            self.held = None
            self.carriers = start_carriers
        lifted = [frame for frame in frames if frame["held"] == name]
        approach = [frame for frame in frames if frame["held"] != name]
        if not lifted:
            raise SkillFailure("pick_unreachable_or_colliding")
        for frame in lifted:
            frame["held"] = name
        self.held = None
        return self._commit(approach + lifted, "pick", name)

    def plan_place_on(self, args):
        return self._put(args.object, args.surface, "place_on", inside=False)

    def plan_seat_in(self, args):
        # A stand or rack has no opening. Seating something on it can only mean
        # setting it down there, which is what place_on does.
        if args.container in self.model.objects and self.collision.parts(args.container)[1] is None:
            return self._put(args.object, args.container, "place_on", inside=False)
        return self._put(args.object, args.container, "seat_in", inside=True)

    def _put(self, name, surface, kind, *, inside):
        if self.held != name:
            raise SkillFailure("object_not_held")
        self._require(surface)
        if inside and self.collision.parts(surface)[1] is None:
            raise SkillFailure("not_a_container")
        rest = self._rest_on(name, surface, inside)
        obj_pos, rot = self._pose(name)
        if not inside:
            rot = np.asarray(self.rest[name][1], dtype=float)
        # Lift on Z, move across at that height, then lower. A diagonal
        # carry is the extra swing that clips the pot on the way in.
        yaws = (0.0,) if not inside else (0.0, 90.0, -90.0, 180.0)
        sink = set()
        apart = (surface,) if inside else ()
        depart = () if inside else (surface,)
        reason = f"{kind}_unreachable_or_colliding"
        start_q = self._qs()
        start_rot = None if self.held_rot is None else np.asarray(self.held_rot).copy()
        last = None
        slide = None
        for yaw in yaws:
            self.model.apply(start_q)
            self.held_rot = start_rot
            aimed = _rotation([0.0, 0.0, 1.0], yaw) @ np.asarray(rot, dtype=float)
            try:
                slide = self._lower(
                    name, surface, rest, obj_pos, aimed, sink, apart, depart, reason, inside,
                )
                break
            except SkillFailure as exc:
                last = exc
        # A searched joint path to above the rest. When the slide also works, the
        # shorter of the two is kept, so a detour out front is not played for nothing.
        self.model.apply(start_q)
        self.held_rot = start_rot
        searched = self._place_transit(
            name, surface, rest, rot, yaws, apart, depart, reason, inside, quick=slide is not None,
        )
        options = [frames for frames in (slide, searched) if frames]
        if options:
            frames = min(options, key=lambda frames: self._travel_cost(start_q, frames))
            return self._commit(frames + self._let_go(frames, name, surface, rest, inside), kind, name)
        # The carry that brought it here is a known way back.
        self.model.apply(start_q)
        self.held_rot = start_rot
        frames = self._retrace(name, surface, rest)
        if frames:
            return self._commit(frames + self._let_go(frames, name, surface, rest, inside), kind, name)
        raise last or SkillFailure(reason)

    def _place_transit(self, name, surface, rest, rot, yaws, apart, depart, reason, inside, quick=False):
        """Carry along a searched joint path to just above ``rest``, then lower onto it.

        ``quick`` only looks for a shorter way than a slide that already works.
        """
        bottom = self._lowest_local(name)
        hover_z = float(rest[2]) + 0.08
        if inside:
            _, rim = self.collision.z_bounds(surface)
            hover_z = max(hover_z, float(rim) - bottom + 0.03)
        hover = np.array([rest[0], rest[1], hover_z])
        seed = self._qs()
        start_rot = None if self.held_rot is None else np.asarray(self.held_rot).copy()
        free = self._transit_check(apart=apart, depart=depart, upright=TRANSIT_TILT)
        budget = TRANSIT_QUICK if quick else TRANSIT_BUDGET
        for yaw in yaws[:1] if quick else yaws:
            aimed = _rotation([0.0, 0.0, 1.0], yaw) @ np.asarray(rot, dtype=float)
            tcp_pos, tcp_rot = self._tcp_from_object(hover, aimed)
            for goal in self._goal_configs(tcp_pos, tcp_rot, seed, free, limit=4):
                self.model.apply(seed)
                self.held_rot = start_rot
                qs = self._transit(goal, free, budget=budget)
                if qs is None:
                    continue
                frames = self._travel_frames(qs)
                self.model.apply(goal)
                try:
                    return self._settle_down(
                        frames, name, surface, rest, aimed, set(), apart, depart, reason, inside,
                    )
                except SkillFailure:
                    continue
        self.model.apply(seed)
        self.held_rot = start_rot
        return None

    def _let_go(self, frames, name, surface, rest, inside):
        """Release at the last frame, back the jaw off, then settle it on its rest pose."""
        self.held_rot = np.asarray(frames[-1]["poses"][name][1], dtype=float).copy()
        cursor = frames[-1]["poses"][name][0]
        released = self._release_frame(frames[-1], name, cursor, surface)
        retreat = self._retreat(released, name, surface)
        tail = [released] + retreat
        if not inside:
            base = retreat[-1] if retreat else released
            tail += self._drop_home(name, cursor, rest, base)
        return tail

    def _retrace(self, name, surface, rest):
        """Step the stepped carry backward, when it began where this place ends.

        Only the arm and the held object follow the old frames. Anything poured out
        stays where it fell, and every frame is checked against the scene as it is now.
        """
        trail = self.trail
        if not trail or any(
            frame["held"] != name or name not in frame["poses"] for frame in trail
        ):
            return None
        current = trail[-1]
        pos, _ = self._pose(name)
        if (
            not np.allclose(current["qs"], self._qs(), atol=1e-4)
            or float(np.linalg.norm(np.asarray(current["poses"][name][0]) - pos)) > 1e-3
        ):
            return None
        # The first held frame is already a little lifted; the same drop place_on allows.
        lift = np.asarray(trail[0]["poses"][name][0], dtype=float) - np.asarray(rest, dtype=float)
        if float(np.linalg.norm(lift[:2])) > 0.01 or not -0.003 <= float(lift[2]) <= 0.04:
            return None
        moving = {name, *self._children(name)}
        family = set(self._family())
        frames = []
        for frame in reversed(trail[:-1]):
            obj_pos = np.asarray(frame["poses"][name][0], dtype=float)
            obj_rot = np.asarray(frame["poses"][name][1], dtype=float)
            poses = {name: (obj_pos, obj_rot), **self._follow(name, obj_pos, obj_rot)}
            if self._hits(frame["qs"], poses, moving, set(), family, depart=(surface,)):
                self.model.apply(current["qs"])
                return None
            self.held_rot = obj_rot.copy()
            baked = self._frame(frame["qs"], poses, name)
            baked["jaw"], baked["jaw_gap"] = frame["jaw"], frame["jaw_gap"]
            frames.append(baked)
        return frames

    def _lower(self, name, surface, rest, obj_pos, rot, sink, apart, depart, reason, inside):
        """Raise, slide across at constant height, then descend.

        A straight slide can leave the wrist outside the arm's reach, so the
        slide may step out toward the front of the table and back in. The
        object stays at one height the whole way; it does not dive.
        """
        bottom = self._lowest_local(name)
        if inside:
            _, rim = self.collision.z_bounds(surface)
            clear = float(rim) - bottom + 0.08
            travel_z = max(float(obj_pos[2]) + 0.08, clear)
        else:
            travel_z = max(float(obj_pos[2]) + 0.06, float(rest[2]) + 0.12)
        up = np.array([obj_pos[0], obj_pos[1], travel_z])
        above = np.array([rest[0], rest[1], travel_z])
        paths = []
        direct = []
        if up[2] > float(obj_pos[2]) + 0.02:
            direct.append((up, rot))
        if float(np.linalg.norm(above[:2] - obj_pos[:2])) > 0.02:
            direct.append((above, rot))
        if direct:
            paths.append(direct)
        if float(np.linalg.norm(above[:2] - obj_pos[:2])) > 0.05:
            for reach in (0.16, 0.26, 0.36):
                front = max(float(obj_pos[0]), float(rest[0])) + reach
                out = np.array([front, obj_pos[1], travel_z])
                over = np.array([front, rest[1], travel_z])
                path = []
                if up[2] > float(obj_pos[2]) + 0.02:
                    path.append((up, rot))
                path.extend([(out, rot), (over, rot), (above, rot)])
                paths.append(path)
        frames = None
        last = None
        seed = self._qs()
        for waypoints in paths:
            self.model.apply(seed)
            try:
                frames = self._carry_object(waypoints, sink=sink, reason=reason, apart=apart)
                break
            except SkillFailure as exc:
                last = exc
                frames = None
        if frames is None:
            if not paths:
                frames = [self._frame(seed, {name: (np.asarray(obj_pos, dtype=float), rot)}, name)]
            else:
                raise last or SkillFailure(reason)
        return self._settle_down(frames, name, surface, rest, rot, sink, apart, depart, reason, inside)

    def _settle_down(self, frames, name, surface, rest, rot, sink, apart, depart, reason, inside):
        """Lower the held object from the last frame onto ``rest`` in 1 cm steps."""
        cursor, cursor_rot = frames[-1]["poses"][name]
        while cursor[2] - rest[2] > 0.003:
            nxt = cursor.copy()
            nxt[2] = max(float(rest[2]), float(cursor[2]) - 0.01)
            try:
                step = self._carry_object(
                    [(nxt, rot)], sink=sink, reason=reason,
                    start=(cursor, cursor_rot), apart=apart, depart=depart,
                )
            except SkillFailure:
                break
            frames += step
            cursor, cursor_rot = frames[-1]["poses"][name]
        if inside:
            low = float(cursor[2] + self._lowest_local(name))
            _, rim = self.collision.z_bounds(surface)
            if low > rim - 0.01:
                raise SkillFailure(reason)
        elif cursor[2] - rest[2] > 0.04:
            raise SkillFailure(reason)
        self.held_rot = np.asarray(cursor_rot, dtype=float).copy()
        return frames

    def _rest_on(self, name, surface, inside):
        if np.linalg.norm(self.rest[name][0][:2] - self._pose(surface)[0][:2]) < 0.08:
            return self.rest[name][0].copy()
        if inside:
            cavity = self.collision.parts(surface)[1]
            pos, rot = self._pose(surface)
            center, size = cavity
            floor = pos + rot @ np.array([center[0], center[1], center[2] - size[2] / 2])
            _, parts = None, self.collision.parts(name)[0]
            bottom = min(center_z[2] - size_z[2] / 2 for center_z, size_z in parts)
            return np.array([floor[0], floor[1], floor[2] - bottom + 0.002])
        _, top = self.collision.z_bounds(surface)
        bottom, _ = self.collision.z_bounds(name)
        pos, _ = self._pose(name)
        return np.array([self._pose(surface)[0][0], self._pose(surface)[0][1], pos[2] + (top + 0.002 - bottom)])

    def _follow(self, parent, pos, rot):
        poses = {}
        for child in self._children(parent):
            local_pos, local_rot = self.locals[child]
            poses[child] = (pos + rot @ local_pos, rot @ local_rot)
        return poses

    def _drop_home(self, name, start, rest, frame):
        """Set the released object back on the pose it was picked up from."""
        start = np.asarray(start, dtype=float)
        rest = np.asarray(rest, dtype=float)
        if float(np.linalg.norm(start - rest)) < 0.002:
            return []
        rot = np.asarray(self.rest[name][1], dtype=float)
        count = max(1, int(np.ceil(float(np.linalg.norm(rest - start)) / 0.004)))
        frames = []
        for index in range(1, count + 1):
            pos = start + (rest - start) * index / count
            poses = {name: (pos.copy(), rot.copy())}
            poses.update(self._follow(name, pos, rot))
            baked = self._frame(frame["qs"], poses, None)
            baked["grasp"] = None
            baked["offset"] = None
            baked["held_rot"] = rot.copy()
            baked["carriers"] = frame["carriers"]
            baked["locals"] = frame["locals"]
            frames.append(baked)
        return frames

    def _settle(self, name, cursor, rest, q, surface):
        frames = []
        if np.linalg.norm(cursor - rest) < 1e-4:
            return frames
        count = max(1, int(np.ceil(np.linalg.norm(rest - cursor) / 0.01)))
        for index in range(1, count + 1):
            pos = cursor + (rest - cursor) * index / count
            poses = {name: (pos, self.held_rot.copy())}
            poses.update(self._follow(name, pos, self.held_rot))
            if self._hits(q, poses, {name, *self._children(name)}, {surface}, self._family()):
                raise SkillFailure("placement_blocked")
            frames.append(self._frame(q, poses, name))
        return frames

    def _release_frame(self, frame, name, rest, surface):
        released = self._frame(frame["qs"], {name: (rest, self.held_rot.copy()), **self._follow(name, rest, self.held_rot)}, None)
        released["held"] = None
        released["offset"] = None
        released["grasp"] = None
        carriers = dict(self.carriers)
        carriers.pop(name, None)
        locals_ = dict(self.locals)
        for child in list(self._children(name)):
            carriers[child] = name
        if self.collision.parts(surface)[1] is not None:
            carriers[name] = surface
            local_pos = self._pose(surface)[1].T @ (rest - self._pose(surface)[0])
            locals_[name] = (local_pos, self._pose(surface)[1].T @ self.held_rot)
        released["carriers"] = carriers
        released["locals"] = locals_
        released["poses"][name] = (np.asarray(rest, dtype=float).copy(), self.held_rot.copy())
        return released

    def _retreat(self, frame, name, surface):
        """Back the open jaw off the object it just released."""
        self.model.apply(frame["qs"])
        rot = np.asarray(self.model.tcp.tf[:3, :3], dtype=float).copy()
        start = self._tcp()
        back = start - rot[:, 0] * 0.08
        seed = np.asarray(frame["qs"], dtype=float)
        frames = []
        moving = {name, *self._children(name)}
        sink = {name, surface, *moving, *self._children(surface)}
        saved = self.grasp
        self.grasp = None
        try:
            for target in _line(start, back, step=0.04):
                q = self._solve_pose(target, rot, seed)
                if q is None:
                    break
                count = max(1, int(np.ceil(np.max(np.abs(q - seed)) / JOINT_STEP)))
                blocked = False
                for index in range(1, count + 1):
                    sample = seed + (q - seed) * index / count
                    self.model.apply(sample)
                    risen = float(np.linalg.norm(self._tcp() - start))
                    self._set_jaw(max(0.0, 1.0 - risen / 0.05))
                    if self._hits(sample, frame["poses"], moving, sink, self._family()):
                        self._set_jaw(1.0)
                        blocked = True
                        break
                    baked = self._frame(sample, frame["poses"], None)
                    baked["grasp"] = None
                    baked["offset"] = None
                    baked["carriers"] = frame["carriers"]
                    baked["locals"] = frame["locals"]
                    frames.append(baked)
                if blocked:
                    break
                seed = q
        finally:
            self.grasp = saved
        return frames

    def plan_pour(self, args):
        return self._empty(args.source, args.target, "pour")

    def plan_shake_off(self, args):
        """A short side-to-side shake. The object stays held and upright."""
        if self.held != args.object:
            raise SkillFailure("object_not_held")
        start = self._tcp()
        rest = self._qs()
        last = None
        for lift in (0.04, 0.0):
            self.model.apply(rest)
            try:
                frames = []
                if lift > 0.0:
                    frames += self._arm(
                        [start + np.array([0.0, 0.0, lift])],
                        attached=True, sink=set(), reason="shake_unreachable_or_colliding",
                    )
                side = self._tcp().copy()
                for _cycle in range(int(args.times)):
                    for offset in (0.02, -0.02):
                        frames += self._arm(
                            [side + np.array([0.0, offset, 0.0])],
                            attached=True, sink=set(), reason="shake_unreachable_or_colliding",
                        )
                frames += self._arm(
                    [side], attached=True, sink=set(), reason="shake_unreachable_or_colliding",
                )
                return self._commit(frames, "shake_off", args.object)
            except SkillFailure as exc:
                last = exc
        self.model.apply(rest)
        raise last or SkillFailure("shake_unreachable_or_colliding")

    def plan_stir(self, args):
        if self.held != args.tool:
            raise SkillFailure("tool_not_held")
        self._require(args.vessel)
        if self.collision.parts(args.vessel)[1] is None:
            raise SkillFailure("not_a_container")
        center = self._over(args.tool, args.vessel, depth=0.04)
        angles = np.radians(np.arange(0, 361, 45))
        points = [center + np.array([0.02 * np.cos(angle), 0.02 * np.sin(angle), 0.0]) for angle in angles]
        sink = {args.vessel, *self._children(args.vessel)}
        frames = self._arm(points, attached=True, sink=sink, reason="stir_unreachable_or_colliding")
        frames += self._swirl(frames[-1], args.vessel)
        return self._commit(frames, "stir", args.tool)

    def plan_scoop(self, args):
        if self.held != args.tool:
            raise SkillFailure("tool_not_held")
        self._require(args.source)
        self._require(args.target)
        pieces = self._children(args.source)
        if not pieces:
            raise SkillFailure("container_empty")
        dip = self._over(args.tool, args.source, depth=0.04)
        sink = {args.source, *self._children(args.source)}
        frames = self._arm([dip], attached=True, sink=sink, reason="scoop_unreachable_or_colliding")
        caught = pieces[0]
        frames[-1] = self._catch(frames[-1], args.tool, caught)
        self._saved = None
        try:
            self._adopt(frames[-1])
            source_sink = {args.source, *self._children(args.source)}
            frames += self._arm(
                [self._tcp() + np.array([0.0, 0.0, 0.18])], attached=True, sink=source_sink,
                reason="scoop_unreachable_or_colliding",
            )
            _, top = self.collision.z_bounds(args.target)
            target_pos, _ = self._pose(args.target)
            goal_object = np.array([
                target_pos[0], target_pos[1],
                top + 0.06 - self._lowest_local(args.tool),
            ])
            frames += self._arm(
                [goal_object - self.offset], attached=True,
                sink={args.target, *self._children(args.target)},
                reason="scoop_unreachable_or_colliding",
            )
            spill = self._spill(frames[-1], args.tool, args.target, liquid_only=False, only={caught})
        finally:
            if self._saved is not None:
                self._restore_hold()
        return self._commit(frames + spill, "scoop", args.tool)

    def plan_reorient(self, args):
        if self.held != args.object:
            raise SkillFailure("object_not_held")
        self.goal_orientation = args.orientation
        pos, rot = self._pose(args.object)
        upright = rot[2, 2] > 0.99
        if args.orientation == "upright" and upright:
            return self._commit([self._frame(self._qs(), {}, args.object)], "reorient", args.object)
        if args.orientation == "upside_down" and rot[2, 2] < -0.99:
            return self._commit([self._frame(self._qs(), {}, args.object)], "reorient", args.object)
        target = np.eye(3) if args.orientation == "upright" else _rotation([1.0, 0.0, 0.0], 180.0)
        frames = []
        q = self._qs()
        for index in range(1, 19):
            angle = 10.0 * index if args.orientation == "upside_down" else -10.0 * index
            step_rot = _rotation([1.0, 0.0, 0.0], angle) @ rot
            if args.orientation == "upright" and step_rot[2, 2] > 0.99:
                step_rot = np.eye(3)
            poses = {args.object: (pos.copy(), step_rot)}
            frames.append(self._frame(q, poses, args.object))
            if args.orientation == "upright" and step_rot[2, 2] > 0.99:
                break
        if args.orientation == "upside_down":
            frames += self._spill(frames[-1], args.object, None, liquid_only=False)
        self.held_rot = frames[-1]["poses"].get(args.object, (pos, target))[1]
        frames[-1]["held_rot"] = self.held_rot.copy()
        return self._commit(frames, "reorient", args.object)

    def _moving(self):
        moving = set()
        if self.held:
            moving.add(self.held)
            moving.update(self._children(self.held))
        return moving

    def _carried(self, q):
        if not self.held:
            return {}
        self.model.apply(q)
        return self._attached(
            q, self.held, self.offset, self.held_rot,
            {child: self.locals[child] for child in self._children(self.held)},
        )

    def _joint_frames(self, start, goal, moving):
        """Joint interpolation. None when any sample meets the scene."""
        span = float(np.max(np.abs(np.asarray(goal) - np.asarray(start))))
        if span < 1e-4:
            q = np.asarray(start, dtype=float)
            return [self._frame(q, self._carried(q), self.held)]
        count = max(8, int(np.ceil(span / 0.08)))
        frames = []
        for index in range(1, count + 1):
            q = start + (goal - start) * index / count
            poses = self._carried(q)
            if self._hits(q, poses, moving, set(), self._family()):
                return None
            frames.append(self._frame(q, poses, self.held))
        return frames

    def plan_move_named_pose(self, args):
        if args.pose not in self.model.poses:
            raise SkillFailure("unknown_fafu_pose")
        goal = np.asarray(self.model.poses[args.pose], dtype=float)
        start = self._qs()
        moving = self._moving()
        frames = self._joint_frames(start, goal, moving)
        if frames is None:
            # A straight blend swings the wrist through whatever was just set down.
            # Rise first, then blend from the raised joints.
            self.model.apply(start)
            rot = np.asarray(self.model.tcp.tf[:3, :3], dtype=float).copy()
            origin = self._tcp().copy()
            seed = np.asarray(start, dtype=float)
            raised = []
            frames = None
            for step in range(1, 8):
                q = self._solve_pose(
                    origin + np.array([0.0, 0.0, 0.04 * step]), rot, seed, wide=True,
                )
                if q is None:
                    break
                segment = self._joint_frames(seed, q, moving)
                if segment is None:
                    break
                raised.extend(segment)
                seed = np.asarray(q, dtype=float)
                tail = self._joint_frames(seed, goal, moving)
                if tail is not None:
                    frames = raised + tail
                    break
        if frames is None:
            self.model.apply(start)
            free = self._transit_check(upright=TRANSIT_TILT if self.held else None)
            reachable = free(goal)
            self.model.apply(start)
            qs = self._transit(goal, free) if reachable else None
            if qs:
                self.model.apply(start)
                frames = self._travel_frames(qs)
        if frames is None:
            raise SkillFailure("pose_colliding")
        return self._commit(frames, "move_named_pose", self.held)

    def plan_set_gripper(self, args):
        """Open or close the jaw. Opening while holding lets the object go."""
        target = 0.0 if args.command == "open" else 1.0
        start_jaw = float(self.jaw)
        qs = self._qs()
        releasing = args.command == "open" and self.held is not None
        poses = {}
        if releasing:
            pos, rot = self._pose(self.held)
            poses = {self.held: (pos.copy(), np.asarray(rot, dtype=float).copy())}
            poses.update(self._follow(self.held, pos, rot))
        frames = []
        try:
            for index in range(1, 7):
                self._set_jaw(start_jaw + (target - start_jaw) * index / 6)
                frame = self._frame(qs, poses, None if releasing else self.held)
                if releasing:
                    frame["held"] = None
                    frame["offset"] = None
                    frame["grasp"] = None
                    frame["held_rot"] = None
                frames.append(frame)
            if releasing:
                # Leave the object where it is and back the hand off, so the next
                # named pose does not swing the wrist through it.
                frames.extend(self._retreat(frames[-1], self.held, self.held))
        finally:
            self._set_jaw(start_jaw)
        return self._commit(frames, "set_gripper", None if releasing else self.held)

    def plan_move_relative(self, args):
        delta = np.array([args.dx, args.dy, args.dz], dtype=float)
        start = self._qs()
        self.model.apply(start)
        current = np.asarray(self.model.tcp.tf, dtype=float)
        goal = self._solve_pose(current[:3, 3] + delta, current[:3, :3], start, wide=True)
        if goal is None:
            raise SkillFailure("relative_target_unreachable")
        count = max(8, int(np.ceil(float(np.max(np.abs(goal - start))) / 0.08)))
        frames = []
        moving = set()
        if self.held:
            moving.add(self.held)
            moving.update(self._children(self.held))
        for index in range(1, count + 1):
            q = start + (goal - start) * index / count
            poses = {}
            if self.held:
                self.model.apply(q)
                poses = self._attached(
                    q, self.held, self.offset, self.held_rot,
                    {child: self.locals[child] for child in self._children(self.held)},
                )
            if self._hits(q, poses, moving, set(), self._family()):
                raise SkillFailure("relative_target_colliding")
            frames.append(self._frame(q, poses, self.held))
        return self._commit(frames, "move_relative", self.held)

    def plan_observe(self, args):
        return self._commit([self._frame(self._qs(), {}, self.held)], "observe", self.held)

    def _lowest_local(self, name):
        parts = self.collision.parts(name)[0]
        return min(float(center[2] - size[2] / 2) for center, size in parts)

    def _over(self, tool, vessel, depth):
        """TCP position that puts the tool's low end ``depth`` meters below the rim."""
        _, top = self.collision.z_bounds(vessel)
        vessel_pos, _ = self._pose(vessel)
        tool_pos, _ = self._pose(tool)
        goal = np.array([
            vessel_pos[0], vessel_pos[1], top - depth - self._lowest_local(tool),
        ])
        return self._tcp_for(tool_pos, goal)

    def _tcp_for(self, current_object, goal_object):
        return self._tcp() + (np.asarray(goal_object, dtype=float) - np.asarray(current_object, dtype=float))

    def _catch(self, frame, tool, piece):
        """Parent ``piece`` to the tool at the dipped pose."""
        tool_pos, tool_rot = frame["poses"].get(tool, self._pose(tool))
        piece_rot = np.eye(3)
        # Seat the portion in the tool, at its low end, instead of leaving it in the vessel.
        half = 0.01
        geometry = self.model.objects[piece][0].geometry
        if geometry is not None and geometry.kind == "box":
            half = float(geometry.xyz_lengths[2] / 2)
        local = (np.array([0.0, 0.0, self._lowest_local(tool) + half]), piece_rot)
        piece_pos = tool_pos + tool_rot @ local[0]
        caught = self._frame(frame["qs"], frame["poses"], tool)
        carriers = dict(caught["carriers"])
        carriers[piece] = tool
        locals_ = dict(caught["locals"])
        locals_[piece] = local
        caught["carriers"] = carriers
        caught["locals"] = locals_
        caught["poses"][piece] = (np.asarray(piece_pos, dtype=float).copy(), np.asarray(piece_rot, dtype=float).copy())
        return caught

    def _adopt(self, frame):
        """Use a frame's hold while planning the next segment, then restore."""
        self._saved = (
            self.held, self.offset, self.held_rot,
            None if self.grasp is None else self.grasp.copy(),
            dict(self.carriers), deepcopy(self.locals),
        )
        self.held = frame["held"]
        self.offset = frame["offset"]
        self.held_rot = frame["held_rot"]
        grasp = frame.get("grasp")
        self.grasp = None if grasp is None else np.asarray(grasp, dtype=float).copy()
        self.carriers = dict(frame["carriers"])
        self.locals = deepcopy(frame["locals"])
        self.model.apply(frame["qs"])
        for name, (pos, rot) in frame["poses"].items():
            self._set(name, pos, rot)

    def _restore_hold(self):
        self.held, self.offset, self.held_rot, self.grasp, self.carriers, self.locals = self._saved
        # Poses and joints are restored by ``_commit``.

    def _empty(self, source, target, kind):
        if self.held != source:
            raise SkillFailure("source_not_held")
        self._require(target)
        if self.collision.parts(source)[1] is None:
            raise SkillFailure("source_not_a_container")
        source_pos, source_rot = self._pose(source)
        target_pos, target_rot = self._pose(target)
        cavity = self.collision.parts(target)[1]
        opening = target_pos if cavity is None else target_pos + target_rot @ cavity[0]
        sink = set()
        seed_q = self._qs()
        last = None
        for frames in self._approach_mouth(source, target, source_pos, source_rot, opening, sink):
            self.model.apply(frames[-1]["qs"])
            try:
                frames += self._tilt_out(frames[-1], source, target)
                return self._commit(frames, kind, source)
            except SkillFailure as exc:
                last = exc
                self.model.apply(seed_q)
        raise last or SkillFailure("pour_position_unreachable")

    def _approach_mouth(self, source, target, source_pos, source_rot, opening, sink):
        """Lift, then slide the opening up to the other mouth. Joint 6 pours after this.

        The slide is what brings the bowl to the skimmer (and the skimmer to the
        bowl). It uses the whole arm, not a wrist roll.
        """
        toward = np.asarray(opening, dtype=float) - np.asarray(source_pos, dtype=float)
        toward[2] = 0.0
        span = float(np.linalg.norm(toward))
        direction = np.array([1.0, 0.0, 0.0]) if span < 1e-6 else toward / span
        local_cavity = self.collision.parts(source)[1][0]
        self.model.apply(self._qs())
        finger = np.asarray(self.model.tcp.tf[:3, 0], dtype=float)
        aimed = self._yaw_onto(source_rot, finger, direction)
        seed = self._qs()
        start_rot = None if self.held_rot is None else np.asarray(self.held_rot).copy()
        goals = []
        for rotation in (aimed, source_rot):
            for standoff in (0.10, 0.12, 0.16):
                base = np.asarray(opening, dtype=float) - direction * standoff - rotation @ local_cavity
                for extra in (0.10, 0.14):
                    for drop in (0.18, 0.12, 0.0):
                        goal = np.array(base, dtype=float, copy=True)
                        # Fold the arm down toward the bowl. A full drop can
                        # hit the rim, so shorter drops stay available.
                        goal[2] = max(float(opening[2]) + extra, float(source_pos[2]) - drop)
                        if self._overlaps(source, target, {source: (goal, rotation)}):
                            continue
                        if self._aside(source, {source: (goal, rotation)}, {source, *self._children(source)}):
                            continue
                        goals.append((goal, rotation))
                        travel_z = max(float(source_pos[2]), float(goal[2]))
                        up = np.array([source_pos[0], source_pos[1], travel_z])
                        across = np.array([goal[0], goal[1], travel_z])
                        waypoints = []
                        if travel_z > float(source_pos[2]) + 0.02:
                            waypoints.append((up, rotation))
                        if float(np.linalg.norm(across[:2] - source_pos[:2])) > 0.02:
                            waypoints.append((across, rotation))
                        if abs(float(goal[2]) - travel_z) > 0.02:
                            waypoints.append((goal, rotation))
                        if not waypoints:
                            yield [self._frame(
                                seed, {source: (source_pos.copy(), rotation)}, source,
                            )]
                            continue
                        self.model.apply(seed)
                        self.held_rot = start_rot
                        try:
                            yield self._carry_object(
                                waypoints, sink=sink, reason="pour_position_unreachable",
                                apart=(target,), enter=(target,),
                            )
                        except SkillFailure:
                            self.model.apply(seed)
        # Every lift-and-slide is blocked. Search a clear joint path to the nearest
        # mouth poses instead; the vessel stays upright on the way.
        self.model.apply(seed)
        self.held_rot = start_rot
        free = self._transit_check(sink=sink, enter=(target,), apart=(target,), upright=TRANSIT_TILT)
        for goal, rotation in goals[:6]:
            self.model.apply(seed)
            self.held_rot = start_rot
            tcp_pos, tcp_rot = self._tcp_from_object(goal, rotation)
            for q in self._goal_configs(tcp_pos, tcp_rot, seed, free, limit=2):
                self.model.apply(seed)
                self.held_rot = start_rot
                qs = self._transit(q, free, budget=1.0)
                if qs is None:
                    continue
                yield self._travel_frames(qs)
                self.model.apply(seed)
                self.held_rot = start_rot

    def _yaw_onto(self, rot, finger, direction):
        """Turn the vessel in the horizontal move so joint 6 can tip toward the mouth."""
        flat = np.asarray(finger, dtype=float).copy()
        flat[2] = 0.0
        if float(np.linalg.norm(flat)) < 0.4:
            return np.asarray(rot, dtype=float)
        flat = flat / np.linalg.norm(flat)
        want = np.cross(np.array([0.0, 0.0, 1.0]), direction)
        if float(np.linalg.norm(want)) < 1e-6:
            return np.asarray(rot, dtype=float)
        want = want / np.linalg.norm(want)
        best = None
        for candidate in (want, -want):
            cross = float(flat[0] * candidate[1] - flat[1] * candidate[0])
            dot = float(np.dot(flat, candidate))
            yaw = float(np.degrees(np.arctan2(cross, dot)))
            if best is None or abs(yaw) < abs(best):
                best = yaw
        if best is None or abs(best) < 8.0:
            return np.asarray(rot, dtype=float)
        return _rotation([0.0, 0.0, 1.0], best) @ np.asarray(rot, dtype=float)

    def _aside(self, name, poses, ignore):
        """True when ``name`` passes through some other solid."""
        skip = set(ignore)
        skip.add(name)
        for other in self.model.objects:
            if other in skip:
                continue
            if self._overlaps(name, other, poses):
                return True
        return False

    def _aim_wrist(self, frame, source, target, axis):
        """Yaw a side-grasped vessel so joint 6's axis is the pour axis.

        Returns the carry, an empty list when it is already aligned, or None
        when no clear yaw exists. A vertical finger axis cannot be yawed onto
        a horizontal pour axis, so the caller leaves that grasp alone.
        """
        self.model.apply(frame["qs"])
        tool_x = np.asarray(self.model.tcp.tf[:3, 0], dtype=float)
        flat = tool_x.copy()
        flat[2] = 0.0
        if float(np.linalg.norm(flat)) < 0.4:
            return []
        flat = flat / np.linalg.norm(flat)
        want = np.asarray(axis, dtype=float)
        want[2] = 0.0
        want = want / np.linalg.norm(want)
        pos, rot = frame["poses"][source]
        # Prefer the direction that leaves joint 6 room to roll.
        room = (
            float(self.limits[5, 1] - frame["qs"][5]),
            float(frame["qs"][5] - self.limits[5, 0]),
        )
        order = (want, -want) if room[0] >= room[1] else (-want, want)
        for candidate in order:
            cross = float(flat[0] * candidate[1] - flat[1] * candidate[0])
            dot = float(np.dot(flat, candidate))
            yaw = float(np.degrees(np.arctan2(cross, dot)))
            if abs(yaw) < 8.0:
                return []
            aimed = _rotation([0.0, 0.0, 1.0], yaw) @ rot
            self.model.apply(frame["qs"])
            try:
                return self._carry_object(
                    [(pos, aimed)], sink=set(), reason="pour_position_unreachable",
                    apart=(target,), enter=(target,),
                )
            except SkillFailure:
                self.model.apply(frame["qs"])
        return None

    def _tilt_out(self, frame, source, target):
        pos, rot = frame["poses"][source]
        grasp = frame.get("grasp")
        if grasp is None:
            raise SkillFailure("pour_tilt_unreachable")
        self.grasp = np.asarray(grasp, dtype=float).copy()
        cavity = self.collision.parts(source)[1]
        center, size = cavity
        handle = np.asarray(self.grasps[source][0], dtype=float)
        pivot = pos + rot @ handle
        target_cavity = self.collision.parts(target)[1]
        target_pos, target_rot = self._pose(target)
        target_mouth = target_pos if target_cavity is None else target_pos + target_rot @ target_cavity[0]
        toward = target_mouth - pivot
        toward[2] = 0.0
        if float(np.linalg.norm(toward)) < 1e-6:
            toward = np.array([1.0, 0.0, 0.0])
        toward = toward / np.linalg.norm(toward)
        # Positive rotation swings the opening (+Z) toward the other mouth, then downward.
        axis = np.cross(np.array([0.0, 0.0, 1.0]), toward)
        axis = axis / np.linalg.norm(axis)
        particles = self._particles(source, pos, rot)
        frames = []
        seed = np.asarray(frame["qs"], dtype=float)
        stacks = {}
        sink = set()
        family = set(self._family())
        names = list(particles)
        for index, left in enumerate(names):
            for right in names[index + 1:]:
                family.add(frozenset((left, right)))

        def bake(q, src_pos, src_rot):
            poses = {source: (src_pos.copy(), src_rot.copy())}
            carriers = {child: parent for child, parent in self.carriers.items() if parent != source}
            for name, particle in particles.items():
                if particle["mode"] == "in":
                    local_pos, local_rot = particle["local"]
                    poses[name] = (src_pos + src_rot @ local_pos, src_rot @ local_rot)
                    carriers[name] = source
                else:
                    poses[name] = (particle["pos"].copy(), particle["rot"].copy())
                    if particle["mode"] == "rest" and particle["vessel"] is not None:
                        carriers[name] = particle["vessel"]
            baked = self._frame(q, poses, source)
            baked["carriers"] = carriers
            locals_ = dict(self.locals)
            for name, particle in particles.items():
                if particle["mode"] == "in":
                    locals_[name] = particle["local"]
                elif particle["mode"] == "rest" and particle["vessel"] is not None:
                    vessel_pos, vessel_rot = self._pose(particle["vessel"])
                    locals_[name] = (
                        vessel_rot.T @ (particle["pos"] - vessel_pos),
                        vessel_rot.T @ particle["rot"],
                    )
            baked["locals"] = locals_
            baked["offset"] = src_pos - self._tcp_at(q)
            baked["held_rot"] = src_rot.copy()
            frames.append(baked)
            return poses

        def solve(degrees, seed_q):
            if use_j6:
                q = np.array(j6_origin, dtype=float, copy=True)
                q[5] = j6_origin[5] + j6_sign * np.radians(degrees)
                if q[5] < self.limits[5, 0] or q[5] > self.limits[5, 1]:
                    return None
                return q
            turned = _rotation(axis, degrees)
            src_rot = turned @ rot
            base = pivot + turned @ (pos - pivot)
            for lift in (0.0, 0.03, 0.06):
                src_pos = np.array(base, dtype=float, copy=True)
                src_pos[2] += lift
                if self._overlaps(source, target, {source: (src_pos, src_rot)}):
                    continue
                tcp_pos, tcp_rot = self._tcp_from_object(src_pos, src_rot)
                q = self._solve_pose(tcp_pos, tcp_rot, seed_q)
                if q is None:
                    q = self._solve_pose(tcp_pos, tcp_rot, seed_q, wide=True)
                if q is None:
                    q = self._solve_pose(tcp_pos, tcp_rot, seed_q, allow_roll=True, wide=True)
                if q is not None:
                    return q
            return None

        def alignment(src_pos, src_rot):
            opening_dir = src_rot @ np.array([0.0, 0.0, 1.0])
            mouth = src_pos + src_rot @ center
            aim = target_mouth - mouth
            span = float(np.linalg.norm(aim))
            if span < 1e-6:
                return -1.0
            return float(np.dot(opening_dir, aim / span))

        # Side grasp: joint 6 is horizontal, so the pour is a roll of that
        # joint alone. A vertical bowl grasp has joint 6 along the fingers,
        # straight down, and rolling it only spins the bowl.
        self.model.apply(seed)
        finger = np.asarray(self.model.tcp.tf[:3, 0], dtype=float)
        use_j6 = abs(float(finger[2])) < 0.45
        j6_sign = 1.0
        j6_origin = np.asarray(seed, dtype=float).copy()
        if use_j6:
            mouth_now = pos + rot @ center
            nudge = np.cross(finger, rot[:, 2])
            nudge_span = float(np.linalg.norm(nudge))
            aim_now = target_mouth - mouth_now
            aim_span = float(np.linalg.norm(aim_now))
            useful = (
                nudge_span > 1e-6 and aim_span > 1e-6
                and abs(float(np.dot(nudge / nudge_span, aim_now / aim_span))) > 0.25
            )
            aimed = [] if useful else self._aim_wrist(frame, source, target, axis)
            if aimed is None:
                raise SkillFailure("pour_tilt_unreachable")
            if aimed:
                frames.extend(aimed)
                seed = np.asarray(aimed[-1]["qs"], dtype=float)
                pos, rot = aimed[-1]["poses"][source]
                for particle in particles.values():
                    if particle["mode"] != "in":
                        continue
                    local_pos, local_rot = particle["local"]
                    particle["pos"] = pos + rot @ local_pos
                    particle["rot"] = rot @ local_rot
            self.model.apply(seed)
            finger = np.asarray(self.model.tcp.tf[:3, 0], dtype=float)
            j6_origin = np.asarray(seed, dtype=float).copy()
            mouth = pos + rot @ center
            nudge = np.cross(finger, rot[:, 2])
            j6_sign = 1.0 if float(np.dot(nudge, target_mouth - mouth)) >= 0.0 else -1.0

        sign = 1.0

        def go(degrees, repeats):
            nonlocal seed
            q = solve(sign * degrees, seed)
            if q is None:
                return None
            count = max(1, int(np.ceil(np.max(np.abs(q - seed)) / JOINT_STEP)))
            path = [seed + (q - seed) * index / count for index in range(1, count + 1)]
            stuck = []
            for sample in path:
                self.model.apply(sample)
                obj = np.asarray(self.model.tcp.tf, dtype=float) @ self.grasp
                src_pos = obj[:3, 3].copy()
                src_rot = obj[:3, :3].copy()
                poses = {source: (src_pos, src_rot)}
                for name, particle in particles.items():
                    if particle["mode"] == "in":
                        local_pos, local_rot = particle["local"]
                        poses[name] = (src_pos + src_rot @ local_pos, src_rot @ local_rot)
                    else:
                        poses[name] = (particle["pos"].copy(), particle["rot"].copy())
                # Food is kept out of utensil solids by the slide itself. Counting
                # those pieces here stops the wrist as soon as they leave the bowl.
                if self._hits(sample, poses, {source}, sink, family, enter=(target,)):
                    return None
                # Contents may enter the target. The vessel being poured must stay clear of it.
                if self._overlaps(source, target, {source: (src_pos, src_rot)}):
                    return None
                if self._aside(source, {source: (src_pos, src_rot)}, set(particles)):
                    return None
                stuck.append((sample, src_pos, src_rot))
            for sample, src_pos, src_rot in stuck:
                bake(sample, src_pos, src_rot)
            seed = np.asarray(q, dtype=float)
            src_pos, src_rot = stuck[-1][1], stuck[-1][2]
            for _ in range(repeats):
                self._physics(particles, src_pos, src_rot, target, source, stacks)
                bake(q, src_pos, src_rot)
            return src_pos, src_rot

        reached = 0.0
        held_pose = (pos, rot)
        best_align = alignment(pos, rot)
        gap = float(np.linalg.norm(target_mouth - (pos + rot @ center)))
        flat_gap = float(np.linalg.norm(target_mouth[:2] - (pos[:2] + (rot @ center)[:2])))
        trail = [np.asarray(seed, dtype=float).copy()]
        for degrees in np.arange(STEP_ANGLE, 141.0, STEP_ANGLE):
            # Food stays in the vessel until the pour angle is reached.
            arrived = go(float(degrees), 0)
            if arrived is None:
                break
            reached = float(degrees)
            held_pose = arrived
            best_align = alignment(*held_pose)
            trail.append(np.asarray(seed, dtype=float).copy())
            gap = float(np.linalg.norm(target_mouth - (held_pose[0] + held_pose[1] @ center)))
            flat = target_mouth[:2] - (held_pose[0][:2] + (held_pose[1] @ center)[:2])
            flat_gap = float(np.linalg.norm(flat))
            # The mouth has to actually be at the other opening. A roll that
            # only aims from across the table still drops the food on it.
            if best_align >= 0.55 and reached >= 55.0 and (gap <= 0.24 or flat_gap <= 0.19):
                break
        if best_align < 0.55 or reached < 55.0 or (gap > 0.24 and flat_gap > 0.19):
            raise SkillFailure("pour_tilt_unreachable")
        # Hold the tilted pose, then let the pieces slide. They stay put while
        # the vessel is still turning.
        for index in range(170):
            if index >= 12 and all(particle["mode"] == "rest" for particle in particles.values()):
                break
            if index >= 12:
                self._physics(particles, held_pose[0], held_pose[1], target, source, stacks)
            bake(seed, held_pose[0], held_pose[1])
        # Replay the tip backwards. That path is already clear, and solving the
        # return from the tipped wrist often is not.
        for q in reversed(trail[:-1]):
            count = max(1, int(np.ceil(np.max(np.abs(q - seed)) / JOINT_STEP)))
            for index in range(1, count + 1):
                sample = seed + (q - seed) * index / count
                self.model.apply(sample)
                obj = np.asarray(self.model.tcp.tf, dtype=float) @ self.grasp
                bake(sample, obj[:3, 3], obj[:3, :3])
            seed = np.asarray(q, dtype=float).copy()
            self._physics(particles, obj[:3, 3], obj[:3, :3], target, source, stacks)
        for _ in range(80):
            if all(particle["mode"] != "fall" for particle in particles.values()):
                break
            self.model.apply(seed)
            obj = np.asarray(self.model.tcp.tf, dtype=float) @ self.grasp
            self._physics(particles, obj[:3, 3], obj[:3, :3], target, source, stacks)
            bake(seed, obj[:3, 3], obj[:3, :3])
        return frames

    def _tcp_at(self, q):
        self.model.apply(q)
        return self._tcp()

    def _particles(self, source, pos, rot):
        particles = {}
        for index, child in enumerate(self._children(source)):
            local = self.locals.get(child) or self._relative(source, child)
            child_pos = pos + rot @ local[0]
            half_z = 0.006
            geometry = self.model.objects[child][0].geometry
            margin = 0.008
            half = np.array([0.01, 0.01, half_z])
            if geometry is not None and geometry.kind == "box":
                size = np.asarray(geometry.xyz_lengths, dtype=float)
                half_z = float(size[2] / 2)
                margin = float(max(size[0], size[1]) / 2)
                half = size / 2
            particles[child] = {
                "mode": "in", "local": (local[0].copy(), local[1].copy()),
                "pos": child_pos.copy(), "vel": np.zeros(3), "rot": (rot @ local[1]).copy(),
                "half_z": half_z, "half": half, "margin": margin, "vessel": None,
                "liquid": child.startswith("liquid_"), "slot": index,
            }
        return particles

    def _mouth(self, name):
        pos, rot = self._pose(name)
        cavity = self.collision.parts(name)[1]
        if cavity is None:
            return pos, np.zeros(3)
        center, size = cavity
        return pos + rot @ center, np.asarray(size, dtype=float)

    def _food_blocked(self, pos, rot, half, source, target, overrides=None):
        """True when a piece would pass through a utensil instead of its opening."""
        pos = np.asarray(pos, dtype=float)
        rot = np.asarray(rot, dtype=float)
        half = np.asarray(half, dtype=float)
        overrides = overrides or {}
        over_target = target is not None and self.collision.floor_z(target, pos[:2]) is not None
        rim = None
        if over_target:
            mouth, cavity_size = self._mouth(target)
            rim = float(mouth[2] + cavity_size[2] / 2)
        for name in self.model.objects:
            if name == source or name.startswith("bowl_food") or name.startswith("liquid_"):
                continue
            if name == target and over_target:
                continue
            # The stand's box reaches up into the basket. Once the piece is
            # over the opening, that box is the inside of the vessel, not a wall.
            if rim is not None:
                _, top = self.collision.z_bounds(name)
                if top < rim - 0.01:
                    continue
            for center, box_rot, box_half in self.collision._boxes(name, overrides):
                if boxes_overlap(pos, rot, half, center, box_rot, box_half, pad=0.0):
                    return True
        for name, (center, link_half) in self.collision.arm.items():
            tf = np.asarray(self.collision.links[name].tf, dtype=float)
            link_rot = tf[:3, :3]
            link_pos = tf[:3, 3] + link_rot @ center
            if boxes_overlap(pos, rot, half, link_pos, link_rot, link_half, pad=0.0):
                return True
        return False

    def _release(self, particle, local, src_pos, src_rot, target, source, center, half):
        """Leave across the lip, then arc toward the other mouth. Not a straight drop."""
        lip = np.array(local, dtype=float, copy=True)
        lip[2] = float(center[2] + half[2] + 0.012)
        world = src_pos + src_rot @ lip
        mouth, cavity_size = (world, np.zeros(3)) if target is None else self._mouth(target)
        rim_z = float(mouth[2] + cavity_size[2] / 2)
        delta = mouth[:2] - world[:2]
        dist = float(np.linalg.norm(delta))
        if dist < 1e-4:
            heading = src_rot[:2, 2]
            span = float(np.linalg.norm(heading))
            heading = np.array([1.0, 0.0]) if span < 1e-4 else heading / span
        else:
            heading = delta / dist
        drop = max(0.04, float(world[2] - rim_z))
        flight = float(np.sqrt(2.0 * drop / GRAVITY))
        speed = min(1.1, max(0.25, dist / flight))
        overrides = {source: (src_pos, src_rot)}
        for _ in range(6):
            if not self._food_blocked(world, src_rot, particle["half"], source, target, overrides):
                break
            world = world + np.array([heading[0], heading[1], 0.0]) * 0.008
            world[2] += 0.004
        particle["mode"] = "fall"
        particle["pos"] = world
        particle["rot"] = src_rot.copy()
        particle["vel"] = np.array([heading[0] * speed, heading[1] * speed, -0.15])

    def _physics(self, particles, src_pos, src_rot, target, source, stacks):
        cavity = self.collision.parts(source)[1]
        center, size = cavity
        gravity = src_rot.T @ np.array([0.0, 0.0, -1.0])
        slope = gravity.copy()
        slope[2] = 0.0
        slope_norm = float(np.linalg.norm(slope))
        half = size / 2
        overrides = {source: (np.asarray(src_pos, dtype=float), np.asarray(src_rot, dtype=float))}
        for particle in particles.values():
            if particle["mode"] == "fall":
                over = None if target is None else self.collision.floor_z(target, particle["pos"][:2])
                if target is not None and over is None:
                    mouth, _ = self._mouth(target)
                    delta = mouth[:2] - particle["pos"][:2]
                    dist = float(np.linalg.norm(delta))
                    if dist > 0.01:
                        particle["vel"][:2] = particle["vel"][:2] + delta / dist * 2.5 * DT
                        horizontal = float(np.linalg.norm(particle["vel"][:2]))
                        if horizontal > 1.1:
                            particle["vel"][:2] *= 1.1 / horizontal
                elif over is not None:
                    slot = self._seat_xy(target, particle["slot"])
                    if slot is not None:
                        delta = slot - particle["pos"][:2]
                        dist = float(np.linalg.norm(delta))
                        if dist > 0.008:
                            step = min(0.01, dist)
                            particle["pos"][:2] = particle["pos"][:2] + delta / dist * step
                    particle["vel"][:2] *= 0.6
                particle["vel"] = particle["vel"] + np.array([0.0, 0.0, -GRAVITY]) * DT
                rot = _rotation([1.0, 0.0, 0.0], 6.0) @ particle["rot"]
                moved = False
                for scale in (1.0, 0.5):
                    trial = particle["pos"] + particle["vel"] * DT * scale
                    if not self._food_blocked(trial, rot, particle["half"], source, target, overrides):
                        particle["pos"] = trial
                        moved = True
                        break
                if not moved:
                    for mask in (np.array([1.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])):
                        trial = particle["pos"] + particle["vel"] * mask * DT
                        if not self._food_blocked(trial, rot, particle["half"], source, target, overrides):
                            particle["pos"] = trial
                            particle["vel"] = particle["vel"] * mask
                            moved = True
                            break
                if not moved:
                    particle["vel"][:2] *= 0.4
                particle["rot"] = rot
                self._land(particle, target, stacks)
                continue
            # About 50° from upright. Shallower than this, the pieces stay where they sit.
            if particle["mode"] != "in" or slope_norm < 0.75:
                continue
            direction = slope / slope_norm
            local = np.array(particle["local"][0], dtype=float, copy=True)
            local[:2] = local[:2] + direction[:2] * 0.01
            lo = center - half + particle["margin"]
            hi = center + half - particle["margin"]
            local[:2] = np.clip(local[:2], lo[:2], hi[:2])
            floor_z = float(center[2] - half[2] + particle["half_z"])
            local[2] = max(float(local[2]), floor_z)
            # Against the low wall, ride up the inside and over the lip.
            on_lip = float(np.dot(local[:2] - center[:2], direction[:2])) > float(min(half[0], half[1])) * 0.55
            if on_lip and slope_norm > 0.8:
                local[2] += 0.012
            opening = float(center[2] + half[2] - particle["half_z"])
            if local[2] >= opening and slope_norm > 0.8:
                self._release(particle, local, src_pos, src_rot, target, source, center, half)
                continue
            particle["local"] = (local, particle["local"][1])

    def _land(self, particle, target, stacks):
        xy = particle["pos"][:2]
        vessel = None
        floor = None
        if target is not None:
            floor = self.collision.floor_z(target, xy)
            if floor is not None:
                vessel = target
        if floor is None:
            floor = self.collision.z_bounds("table")[1]
        if vessel is not None:
            surface = floor + particle["half_z"]
        else:
            cell = (round(float(particle["pos"][0]) / 0.03), round(float(particle["pos"][1]) / 0.03))
            stacked = stacks.get(("table", cell), 0)
            surface = floor + particle["half_z"] + stacked * particle["half_z"] * 1.6
        if particle["pos"][2] > surface or particle["vel"][2] > 0:
            return
        if vessel is not None:
            seat = self._seat_xy(vessel, particle["slot"])
            if seat is not None and self.collision.floor_z(vessel, seat) is not None:
                delta = seat - particle["pos"][:2]
                dist = float(np.linalg.norm(delta))
                if dist > 1e-6:
                    step = min(0.01, dist)
                    particle["pos"][:2] = particle["pos"][:2] + delta / dist * step
                floor = self.collision.floor_z(vessel, particle["pos"][:2]) or floor
                surface = floor + particle["half_z"]
        particle["pos"][2] = surface
        particle["vel"] = np.zeros(3)
        particle["rot"] = np.eye(3)
        particle["mode"] = "rest"
        particle["vessel"] = vessel
        if vessel is None:
            stacks[("table", cell)] = stacked + 1

    def _seat_xy(self, vessel, slot):
        """A point on the inner floor, around the middle of the opening."""
        cavity = self.collision.parts(vessel)[1]
        if cavity is None:
            return None
        center, size = cavity
        pos, rot = self._pose(vessel)
        radius = min(0.028, float(min(size[0], size[1])) * 0.22)
        angle = float(slot) * (np.pi / 2.0) + 0.4
        local = np.array([
            center[0] + radius * np.cos(angle),
            center[1] + radius * np.sin(angle),
            0.0,
        ])
        world = pos + rot @ local
        return world[:2]

    def _spill(self, frame, source, target, *, liquid_only, only=None):
        """Drop carried pieces from a held object. ``target`` may be None."""
        pos, rot = frame["poses"].get(source, self._pose(source))
        particles = self._particles(source, pos, rot)
        if only is not None:
            particles = {name: particles[name] for name in only if name in particles}
        if liquid_only:
            particles = {name: particle for name, particle in particles.items() if particle["liquid"]}
        for particle in particles.values():
            particle["mode"] = "fall"
            particle["vel"] = np.array([0.0, 0.0, -0.4])
        frames = []
        q = frame["qs"]
        stacks = {}
        for _ in range(50):
            if particles and all(particle["mode"] != "fall" for particle in particles.values()):
                break
            for particle in particles.values():
                if particle["mode"] != "fall":
                    continue
                particle["vel"] = particle["vel"] + np.array([0.0, 0.0, -GRAVITY]) * DT
                particle["pos"] = particle["pos"] + particle["vel"] * DT
                self._land(particle, target, stacks)
            poses = dict(frame["poses"])
            poses[source] = (pos.copy(), rot.copy())
            carriers = dict(self.carriers)
            for name, particle in particles.items():
                poses[name] = (particle["pos"].copy(), particle["rot"].copy())
                if particle["mode"] == "rest" and particle["vessel"] is not None:
                    carriers[name] = particle["vessel"]
                elif particle["mode"] == "fall":
                    carriers.pop(name, None)
            baked = self._frame(q, poses, frame["held"])
            baked["carriers"] = carriers
            frames.append(baked)
        return frames

    def _swirl(self, frame, vessel):
        frames = []
        pieces = self._children(vessel)
        if not pieces:
            return [frame]
        q = frame["qs"]
        for index in range(1, 13):
            angle = np.radians(30 * index)
            shift = np.array([0.012 * np.cos(angle), 0.012 * np.sin(angle), 0.0])
            poses = dict(frame["poses"])
            carriers = dict(self.carriers)
            for piece in pieces:
                base, rot = self._pose(piece)
                if piece in frame["poses"]:
                    base, rot = frame["poses"][piece]
                poses[piece] = (base + shift, rot.copy())
            baked = self._frame(q, poses, frame["held"])
            baked["carriers"] = carriers
            frames.append(baked)
        return frames

    def step(self, index):
        frame = self.path[index]
        self._set_jaw(frame.get("jaw", 0.0), frame.get("jaw_gap"))
        self.model.apply(frame["qs"])
        for name, (pos, rot) in frame["poses"].items():
            self._set(name, pos, rot)
        self.held = frame["held"]
        self.offset = frame["offset"]
        self.held_rot = frame["held_rot"]
        grasp = frame.get("grasp")
        self.grasp = None if grasp is None else np.asarray(grasp, dtype=float).copy()
        self.carriers = dict(frame["carriers"])
        self.locals = deepcopy(frame["locals"])
        if self.held is None or len(self.trail) >= TRAIL_LIMIT:
            self.trail = []
        if self.held is not None:
            self.trail.append(frame)
        data = None
        if self.focus in self.model.objects:
            data = self.model.objects[self.focus][0]
        return self.model.read(), data, self.held

    def verify(self):
        if not self.path:
            return False
        last = self.path[-1]
        if not np.allclose(self._qs(), last["qs"], atol=1e-4):
            return False
        if self.held != last["held"]:
            return False
        if self.kind == "reorient" and self.goal_orientation == "upside_down" and self.held:
            return self._pose(self.held)[1][2, 2] < -0.99
        if self.held is not None:
            return bool(self._pose(self.held)[1][2, 2] > 0.99)
        return True

    def relations(self, fresh=False):
        """Containment and support the planner cannot read off coordinates.

        Supports are searched again only when a hold or a containment changes, or
        when a skill ends, not on every streamed frame.
        """
        key = (self.held, tuple(sorted(self.carriers.items())))
        if fresh or key != self._relation_key:
            supports = self._find_supports()
            self._relation_key = key
            self._relations = {}
            for name in self.model.objects:
                loose = name != self.held
                self._relations[name] = {
                    "shape": "container" if self.collision.parts(name)[1] is not None else "solid",
                    "inside": self.carriers.get(name) if loose else None,
                    "resting_on": supports.get(name) or self._stand_under(name)
                    if loose and name not in self.carriers else None,
                    "home": self.home.get(name),
                }
        return self._relations

    def _stand_under(self, name):
        """A stand the body hangs into, so their boxes overlap and no gap is found.

        The skimmer's net sits inside its holder. Takes the highest object right under
        the body's origin; the table is the default surface and is not named.
        """
        pos, _ = self._pose(name)
        best, best_top = None, None
        for other in self.model.objects:
            if other in (name, "table"):
                continue
            other_pos, _ = self._pose(other)
            if float(np.linalg.norm(other_pos[:2] - pos[:2])) > 0.08 or other_pos[2] >= pos[2]:
                continue
            _, top = self.collision.z_bounds(other)
            if top < pos[2] and (best_top is None or top > best_top):
                best, best_top = other, top
        return best

    def contents_of(self, name):
        return [child for child, parent in self.carriers.items() if parent == name]

    def record(self, sequence):
        """Plan and play ``sequence``, then rewind. Returns every posed frame."""
        start_qs = self._qs()
        start_poses = {name: self._pose(name) for name in list(self.model.objects)}
        start = (self.held, None if self.offset is None else self.offset.copy(),
                 None if self.held_rot is None else self.held_rot.copy(),
                 None if self.grasp is None else self.grasp.copy(),
                 dict(self.carriers), deepcopy(self.locals))
        movie = [self._capture(None)]
        try:
            for name, arguments in sequence:
                count = self.plan_skill(name, arguments)
                for index in range(count):
                    self.step(index)
                    movie.append(self._capture(name))
                if not self.verify():
                    raise SkillFailure(f"{name}_not_verified")
        finally:
            self.held, self.offset, self.held_rot, self.grasp, self.carriers, self.locals = start
            self._set_jaw(0.0)
            self.model.apply(start_qs)
            for name, (pos, rot) in start_poses.items():
                if name in self.model.objects:
                    self._set(name, pos, rot)
        return movie

    def _capture(self, skill):
        return {
            "skill": skill,
            "qs": self._qs(),
            "jaw": float(self.jaw),
            "jaw_gap": float(self.jaw_gap),
            "poses": {name: self._pose(name) for name in self.model.objects},
        }

    def apply_capture(self, frame):
        self._set_jaw(frame.get("jaw", 0.0), frame.get("jaw_gap"))
        self.model.apply(frame["qs"])
        for name, (pos, rot) in frame["poses"].items():
            self._set(name, pos, rot)


def attach_kitchen(model):
    """Kitchen skills on the model that already loaded the scene. Starts at joint zero."""
    motion = KitchenMotion(model)
    motion.model.apply([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    return motion


def publish_kitchen(motion, state, index=None):
    """Copy one posed frame into the node snapshot the UI viewer polls."""
    if index is not None:
        motion.step(index)
    state.kinematics = motion.model.read()
    relations = motion.relations(fresh=index is None or index == len(motion.path) - 1)
    posed = {}
    for name, data in state.objects.items():
        if name not in motion.model.objects:
            posed[name] = data
            continue
        pos, rot = motion._pose(name)
        posed[name] = data.model_copy(update={
            "pos": [float(value) for value in pos],
            "rotmat": [[float(value) for value in row] for row in rot],
            **relations.get(name, {}),
        })
    state.objects = posed
    state.held = motion.held
    state.facts["jaw"] = float(motion.jaw)
    state.facts["jaw_gap"] = float(motion.jaw_gap)
    state.pose = None
    state.version += 1


async def play_kitchen_skill(call, motion, skill, state, args, stop, progress):
    """Plan one registered skill, then step it slowly enough for the viewer to follow."""
    count = await call(motion.plan_skill, skill, args.model_dump())
    if count <= 0:
        return False
    for index in range(count):
        if stop.is_set():
            return False
        await call(publish_kitchen, motion, state, index)
        progress((index + 1) / count)
        try:
            await asyncio.wait_for(stop.wait(), 0.05)
        except TimeoutError:
            pass
    if stop.is_set():
        return False
    return bool(await call(motion.verify))


def _restoring(method):
    """Planning may move the model while it searches. Put that search back."""

    def wrapped(self, args):
        saved = self._snapshot()
        try:
            return method(self, args)
        finally:
            self._rollback(saved)

    return wrapped


for _skill in SKILLS_HANDLED:
    _method = f"plan_{_skill}"
    setattr(KitchenMotion, _method, _restoring(getattr(KitchenMotion, _method)))
