"""Kitchen transit planning on recognized objects, inside the real joint limits.

All methods run on one owner thread. Planning never moves the arm; it leaves joint
frames that the node sends to hardware, then steps the model along as they finish.
"""

import math
from types import SimpleNamespace

import numpy as np

from wrs_agent.env.fafu_backend import FafuModel
from wrs_agent.env.kitchen_motion import JOINT_WEIGHT, TRANSIT_TILT, KitchenMotion
from wrs_agent.executor import SkillFailure
from wrs_agent.nodes.fafu import world as perception
from wrs_agent.nodes.fafu.collision import bodies
from wrs_agent.nodes.fafu.handeye import transform

LOOK = "__look__"


class RealMotion(KitchenMotion):
    """The kitchen planner without its widened sim limits and with measured grasps."""

    def _relax_limits(self):
        limits = np.asarray(self.model.real_limits, dtype=float)
        chain = self.model.chain
        chain.lmt_lo[:] = limits[:, 0].astype(np.float32)
        chain.lmt_up[:] = limits[:, 1].astype(np.float32)
        self.limits = limits.copy()
        self.model.limits = self.limits
        robot, indices = self.model.robot, self.model.indices

        def apply(q):
            q = np.asarray(q, dtype=float)
            if q.shape != (6,) or not np.isfinite(q).all():
                raise ValueError("关节角必须是 6 个有限数值。")
            if np.any(q < limits[:, 0] - 1e-5) or np.any(q > limits[:, 1] + 1e-5):
                raise ValueError("关节角超出了实机限位。")
            full = np.zeros_like(robot.qs)
            full[indices] = q
            robot.fk(full)

        self.model.apply = apply

    def _rest_on(self, name, surface, inside):
        """The table is as wide as the view; "put it down" means where it was seen."""
        if surface == "table":
            return np.asarray(self.rest[name][0], dtype=float).copy()
        return super()._rest_on(name, surface, inside)

    def _pour_axis(self, name):
        """Contents leave over the rim opposite the hand, wherever GraspNet put it."""
        spec = self.grasps.get(name)
        if spec is None:
            return super()._pour_axis(name)
        flat = np.array([spec[0][0], spec[0][1], 0.0])
        length = float(np.linalg.norm(flat))
        return super()._pour_axis(name) if length < 1e-6 else -flat / length


def real_limits(urdf, hardware, margin_deg):
    margin = math.radians(margin_deg)
    lo = np.maximum(urdf[:, 0], hardware[:, 0]) + margin
    hi = np.minimum(urdf[:, 1], hardware[:, 1]) - margin
    if np.any(lo >= hi):
        raise ValueError("URDF 与 robot.cfg 的关节范围没有交集")
    return np.column_stack((lo, hi))


class Planner:
    def __init__(self, config, catalog, handeye, hardware_limits):
        self.config, self.catalog, self.handeye = config, catalog, handeye
        self.model = FafuModel()
        limits = real_limits(np.asarray(self.model.limits, dtype=float), hardware_limits,
                             config["arm"]["joint_margin_deg"])
        self.model.real_limits = limits
        self.model.poses = {name: self.clip(values) for name, values in config["poses"].items()}
        self.table_z = float(config["table"]["z"])
        self.classes = {}
        self.homes = {}
        self.objects = {}
        self.motion = None

    @property
    def limits(self):
        return self.model.real_limits

    def clip(self, q):
        return np.clip(np.asarray(q, dtype=float), self.limits[:, 0], self.limits[:, 1])

    def start(self, q):
        """First world: only the table, so the arm can move before anything is seen."""
        self.model.apply(self.clip(q))
        target = self.config["observe"]["target"]
        self.rebuild([], self.table_z, target, observed_at_ns=None)
        return self.read()

    def sync(self, q):
        """Plan from where the arm really is; the readback can sit just past a limit."""
        self.motion.model.apply(self.clip(q))
        return self.read()

    def read(self):
        return self.model.read()

    # -- World ------------------------------------------------------------------------

    def world_from_cam(self):
        link6 = next(link for link in self.model.robot.runtime_lnks if link.name == "link6")
        return np.asarray(link6.tf, dtype=float) @ self.handeye

    def perceive(self, detections, points_cam, observed_at_ns):
        """Rebuild every object from one still view. Returns the new object data."""
        perceive = self.config["perception"]
        world_from_cam = self.world_from_cam()
        cloud = points_cam.reshape(-1, 3)
        cloud = cloud[np.isfinite(cloud).all(axis=1) & (cloud[:, 2] > 0)]
        table_z = perception.table_height(transform(world_from_cam, cloud), self.table_z)
        seen = perception.locate(
            detections, points_cam, world_from_cam, self.catalog, table_z=table_z,
            min_score=float(perceive["min_score"]), shrink=float(perceive["box_shrink"]),
        )
        seen = perception.merge(seen, float(perceive["merge_m"]))
        previous = {name: (key, np.asarray(self.objects[name].pos)) for name, key in
                    self.classes.items() if key != "table"}
        seen = perception.name(seen, previous, float(perceive["track_m"]))
        self.rebuild(seen, table_z, self.config["observe"]["target"], observed_at_ns=observed_at_ns)
        return self.objects

    def rebuild(self, seen, table_z, table_xy, *, observed_at_ns):
        model = self.model
        q = np.asarray(model.robot.qs[model.indices], dtype=float).copy()
        for _, obj in model.objects.values():
            obj.remove_from_scene(model.scene)
        hints = perception.selection_hints(seen)
        objects = {"table": perception.table_data(self.catalog, table_z, table_xy)}
        classes = {"table": "table"}
        for item in seen:
            entry = self.catalog.classes[item.key]
            objects[item.name] = perception.object_data(item, entry, hints[item.name],
                                                        observed_at_ns)
            classes[item.name] = item.key
        model.objects = {}
        for name, data in objects.items():
            obj = model.wrs.wssop.box(
                pos=data.pos, rotmat=data.rotmat, xyz_lengths=data.geometry.xyz_lengths,
                rgb=data.rgb, alpha=data.alpha, name=name,
            )
            obj.add_to_scene(model.scene)
            model.objects[name] = (data, obj)
        model.bodies = bodies(self.catalog, classes)
        self.motion = RealMotion(model)
        self.motion.model.apply(self.clip(q))
        # "Put it back" means where it was first seen this run, not where it is now.
        for name in objects:
            remembered = self.homes.get(name)
            if remembered is not None and remembered in objects:
                self.motion.home[name] = remembered
            elif self.motion.home.get(name) is not None:
                self.homes[name] = self.motion.home[name]
        self.table_z, self.classes, self.objects = table_z, classes, objects

    # -- Plans ------------------------------------------------------------------------

    def plan(self, skill, arguments):
        """A registered kitchen skill on the current world. Returns the frame count."""
        if skill == "move_named_pose" and arguments["pose"] not in self.model.poses:
            raise SkillFailure("unknown_pose")
        return self.motion.plan_skill(skill, arguments)

    def _full_pose(self, pos, rot, seed, free):
        """IK where the whole orientation matters: the camera must not roll."""
        motion = self.motion
        guesses = [np.asarray(seed, dtype=float), *list(motion._seeds(seed))[1:3]]
        guesses += [motion.search_rng.uniform(self.limits[:, 0], self.limits[:, 1])
                    for _ in range(24)]
        found = []
        for guess in guesses:
            q = motion._ik_pose(pos, rot, guess)
            if q is None:
                continue
            motion.model.apply(q)
            tf = np.asarray(motion.model.tcp.tf, dtype=float)
            if (float(np.linalg.norm(tf[:3, 3] - pos)) > 0.01
                    or float(np.linalg.norm(tf[:3, :3] - rot)) > 0.08):
                continue
            if any(float(np.max(np.abs(q - other))) < 0.05 for other in found):
                continue
            if free(q):
                found.append(q)
        motion.model.apply(seed)
        return sorted(found, key=lambda q: float(np.sum(np.abs(q - seed) * JOINT_WEIGHT)))

    def plan_look(self):
        """Joints that aim the camera at the table target, then a clear path there."""
        motion, view = self.motion, self.config["observe"]
        seed = motion._qs()
        link6 = next(link for link in self.model.robot.runtime_lnks if link.name == "link6")
        tool_in_link6 = np.linalg.inv(np.asarray(link6.tf, dtype=float)) @ np.asarray(
            self.model.tcp.tf, dtype=float)
        cam_in_link6_inv = np.linalg.inv(self.handeye)
        target = np.array([*view["target"], self.table_z], dtype=float)
        base = np.asarray(self.model.base_pos, dtype=float)
        toward = target[:2] - base[:2]
        toward = toward / max(float(np.linalg.norm(toward)), 1e-6)
        side = np.array([-toward[1], toward[0], 0.0])
        free = motion._transit_check(upright=TRANSIT_TILT if motion.held else None)
        try:
            for tilt in view["tilts_deg"]:
                t = math.radians(tilt)
                look = np.array([toward[0] * math.sin(t), toward[1] * math.sin(t), -math.cos(t)])
                cam_pos = target - look * float(view["height"]) / math.cos(t)
                candidates = []
                for yaw in view["yaws_deg"]:
                    y = math.radians(yaw)
                    x_axis = math.cos(y) * side + math.sin(y) * np.cross(look, side)
                    camera = np.eye(4)
                    camera[:3, :3] = np.column_stack((x_axis, np.cross(look, x_axis), look))
                    camera[:3, 3] = cam_pos
                    tool = camera @ cam_in_link6_inv @ tool_in_link6
                    candidates += self._full_pose(tool[:3, 3], tool[:3, :3], seed, free)
                if candidates:
                    goal = min(candidates,
                               key=lambda q: float(np.sum(np.abs(q - seed) * JOINT_WEIGHT)))
                    self.model.poses[LOOK] = goal
                    return motion.plan_move_named_pose(SimpleNamespace(pose=LOOK))
        finally:
            motion.model.apply(seed)
        raise SkillFailure("observe_pose_unreachable")

    def grasp_spec(self, name, candidate, world_from_cam, flip):
        """GraspNet's x approach and y closing become the tool's x and y on this object.

        GraspNet's translation sits where the fingers begin; the fingertips, which are
        this arm's tool point, are depth_m further along the approach.
        """
        rotation = np.asarray(candidate.rotation, dtype=float).reshape(3, 3)
        tip = np.asarray(candidate.translation, dtype=float) + rotation[:, 0] * candidate.depth_m
        tip = transform(world_from_cam, tip)
        axes = world_from_cam[:3, :3] @ rotation
        approach, closing = axes[:, 0], axes[:, 1] * (-1.0 if flip else 1.0)
        pos, rot = self.motion._pose(name)
        return (tuple(rot.T @ (tip - pos)), tuple(rot.T @ approach), tuple(rot.T @ closing))

    def plan_pick(self, name, candidates, world_from_cam):
        motion = self.motion
        reasons = []
        for candidate in candidates[:8]:
            for flip in (False, True):
                motion.grasps[name] = self.grasp_spec(name, candidate, world_from_cam, flip)
                motion.grasp_gaps[name] = float(np.clip(candidate.width_m / 2, 0.005, 0.03))
                try:
                    return motion.plan_pick(SimpleNamespace(object=name)), candidate
                except SkillFailure as exc:
                    reasons.append(str(exc))
        motion.grasps.pop(name, None)
        raise SkillFailure(max(set(reasons), key=reasons.count) if reasons
                           else "pick_unreachable_or_colliding")

    def segment_object(self, points_cam, mask, world_from_cam):
        """The world object whose box holds most of the segmented points."""
        cloud = points_cam[mask]
        cloud = cloud[np.isfinite(cloud).all(axis=1) & (cloud[:, 2] > 0)]
        if len(cloud) < 30:
            raise SkillFailure("insufficient_target_depth")
        world = transform(world_from_cam, cloud)
        best, best_count = None, 0
        for name, (data, _) in self.model.objects.items():
            if name == "table":
                continue
            local = (world - np.asarray(data.pos)) @ np.asarray(data.rotmat)
            half = np.asarray(data.geometry.xyz_lengths) / 2 + 0.03
            count = int(np.count_nonzero((np.abs(local) <= half).all(axis=1)))
            if count > best_count:
                best, best_count = name, count
        return best if best_count >= len(world) // 3 else None

    def frames(self):
        return [(np.asarray(f["qs"], dtype=float).tolist(), f["held"]) for f in self.motion.path]
