"""Computed pour, stir, scoop and shake_off for a held upright box in the WRS virtual scene.

Paths are generated from the objects' current poses and sizes, solved frame by frame with
the arm's IK and collision-checked. Contents (water, noodles) are not simulated: a pour or a
scoop moves only the held object, never what is inside it. Open containers are bounded by
their box outline; their walls are not modeled, so the tool may enter that outline.
"""

from functools import partial

import numpy as np

from wrs_agent.env.wrs_pick import VirtualPickPlace, run_plan
from wrs_agent.executor import SkillFailure

SKILLS = ("pour", "stir", "scoop", "shake_off")

ABOVE = .03  # Clearance between the held object and the rim it works over.
TILT = (120., 90.)  # Pour/scoop tilt tried in order, in degrees.
STEP_ANGLE = 5.
STEP_LENGTH = .005
MAX_JUMP = .5  # Radians between consecutive frames; larger means a different IK branch.
JOINT_STEP = .02  # Collision-check spacing along each joint-interpolated segment.
DWELL = 10  # Frames held at full tilt.
WALL = .005
STIR_TURNS = 2
SHAKE = (.05, .02, 3)  # Lift, side amplitude and cycles.


def _rotation(axis, degrees):
    axis = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    x, y, z = axis
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    angle = np.radians(degrees)
    return np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * (k @ k)


def _line(start, stop):
    count = max(1, int(np.ceil(np.linalg.norm(stop[:3, 3] - start[:3, 3]) / STEP_LENGTH)))
    poses = []
    for index in range(1, count + 1):
        pose = start.copy()
        pose[:3, 3] = start[:3, 3] + (stop[:3, 3] - start[:3, 3]) * index / count
        poses.append(pose)
    return poses


def _top(data):
    return data.pos[2] + data.geometry.xyz_lengths[2] / 2


class VirtualKitchen(VirtualPickPlace):
    """pick/place_on plus computed handling motions; the held object stays held throughout."""

    def _held(self, object_id, reason):
        if self.held != object_id:
            raise SkillFailure(reason)
        data = self.model.object_data(object_id)
        if data.rotmat is None or np.asarray(data.rotmat)[2, 2] <= .999:
            raise SkillFailure("held_object_not_upright")
        return data

    def _container(self, name):
        if name not in self.model.objects:
            raise SkillFailure(f"{name}_not_in_scene")
        return self.model.object_data(name)

    def _frames(self):
        """Grasp-center pose and the held box pose relative to it."""
        tcp = self.model.gripper.tcp("grasp_center")
        obj = self.model.objects[self.held][1]
        return tcp, tcp.tf.copy(), np.linalg.inv(tcp.tf) @ obj.tf

    def _reach(self, data, grasp):
        """Radius around the grasp center that the held box sweeps while it turns."""
        half = np.asarray(data.geometry.xyz_lengths) / 2
        return float(np.linalg.norm(grasp[:3, 3]) + np.linalg.norm(half))

    def _tip_pose(self, grasp, current, data, tip):
        """Grasp pose that puts the held box's bottom center at world point ``tip``."""
        target = np.eye(4)
        target[:3, :3] = (current @ grasp)[:3, :3]
        target[:3, 3] = np.asarray(tip) + target[:3, 2] * data.geometry.xyz_lengths[2] / 2
        return target @ np.linalg.inv(grasp)

    def _inside(self, tool, vessel):
        """Stir/scoop radius inside the vessel outline and the tip depth below its rim."""
        tool_half = np.linalg.norm(np.asarray(tool.geometry.xyz_lengths[:2]) / 2)
        vessel_half = min(vessel.geometry.xyz_lengths[:2]) / 2
        radius = vessel_half - tool_half - WALL
        if radius < WALL:
            raise SkillFailure("vessel_too_small_for_tool")
        depth = min(.03, .6 * vessel.geometry.xyz_lengths[2])
        return radius, _top(vessel) - depth

    def _follow(self, poses, collider, start, tcp):
        """IK each frame from the previous solution; joint-interpolate and collision-check."""
        robot = self.model.robot
        lower, upper = robot.chain_joint_limits("main")
        width = self.model.width
        path, current = [], np.asarray(start)
        for pose in poses:
            solutions = robot.ik(pose[:3, 3], pose[:3, :3], tcp=tcp, ref_qs=current)
            candidates = [
                q for q in solutions
                if np.isfinite(q).all() and (q >= lower).all() and (q <= upper).all()
                and np.max(np.abs(q - current)) <= MAX_JUMP
            ]
            if not candidates:
                return None
            goal = min(candidates, key=lambda q: np.linalg.norm(q - current))
            count = max(1, int(np.ceil(np.max(np.abs(goal - current)) / JOINT_STEP)))
            for index in range(1, count + 1):
                q = current + (goal - current) * index / count
                if collider.is_collided(q):
                    return None
                path.append((q, width))
            current = goal
        return path

    def _travel(self, goal, collider, start, tcp, reason):
        robot, gripper = self.model.robot, self.model.gripper
        motion = robot.moveto(goal, collider=collider, tcp=tcp, start_qs=start,
                              ee_qpos=gripper.qs.copy(), time_limit=1, max_iters=1000)
        if motion is None:
            raise SkillFailure(reason)
        return self._segment(motion)

    def _tilt(self, over, collider, start, tcp, reason):
        """Turn about the jaw's closing axis at ``over``, dwell, and turn back upright."""
        axis = over[:3, 0]
        for degrees in TILT:
            for sign in (1, -1):
                frames = []
                for index in range(1, int(degrees / STEP_ANGLE) + 1):
                    pose = over.copy()
                    pose[:3, :3] = _rotation(axis, sign * index * STEP_ANGLE) @ over[:3, :3]
                    frames.append(pose)
                forward = self._follow(frames, collider, start, tcp)
                if forward is not None:
                    back = [(q, width) for q, width in reversed(forward[:-1])]
                    return forward + [forward[-1]] * DWELL + back + [(start, forward[0][1])]
        raise SkillFailure(reason)

    def _plan(self, object_id, build):
        model, robot, gripper = self.model, self.model.robot, self.model.gripper
        initial_qs, initial_width = robot.qs.copy(), model.width
        try:
            path = build(*self._frames(), initial_qs)
        finally:
            robot.fk(initial_qs)
            gripper.set_opening(initial_width)
        if not path:
            raise SkillFailure("empty_motion")
        self.kind, self.object_id, self.switch_at, self.path = "motion", object_id, None, path
        self.goal = None
        return len(path)

    def plan_pour(self, source, target):
        data = self._held(source, "source_not_held")
        bowl = self._container(target)

        def build(tcp, current, grasp, start):
            collider = self._collider(source)
            over = current.copy()
            over[:3, 3] = [bowl.pos[0], bowl.pos[1], _top(bowl) + ABOVE + self._reach(data, grasp)]
            path = self._travel(over, collider, start, tcp, "pour_position_unreachable")
            return path + self._tilt(over, collider, path[-1][0], tcp, "pour_tilt_unreachable")

        return self._plan(source, build)

    def plan_shake_off(self, object_id, cycles=None):
        self._held(object_id, "object_not_held")
        lift, amplitude, default_cycles = SHAKE
        cycles = default_cycles if cycles is None else int(cycles)

        def build(tcp, current, grasp, start):
            collider = self._collider(object_id)
            lifted = current.copy()
            lifted[2, 3] += lift
            frames = _line(current, lifted)
            side = lifted[:3, 3].copy()
            for offset in [amplitude, -amplitude] * cycles + [0.]:
                pose = lifted.copy()
                pose[:3, 3] = side + [0., offset, 0.]
                frames += _line(frames[-1], pose)
            path = self._follow(frames, collider, start, tcp)
            if path is None:
                raise SkillFailure("shake_unreachable_or_colliding")
            return path

        return self._plan(object_id, build)

    def _dip(self, data, vessel, sweep, collider, tcp, current, grasp, start, reason):
        """Enter the vessel outline from above, run ``sweep`` at depth, and come back out."""
        radius, depth = self._inside(data, vessel)
        center = np.array(vessel.pos[:2])
        above = self._tip_pose(grasp, current, data, [*center, _top(vessel) + ABOVE])
        path = self._travel(above, collider, start, tcp, reason)
        frames = _line(above, self._tip_pose(grasp, current, data, [*center, depth]))
        for x, y in sweep(radius):
            frames += _line(frames[-1], self._tip_pose(
                grasp, current, data, [center[0] + x, center[1] + y, depth]))
        frames += _line(frames[-1], above)
        inside = self._follow(frames, collider, path[-1][0], tcp)
        if inside is None:
            raise SkillFailure(reason)
        return path + inside

    def plan_stir(self, tool, vessel):
        data = self._held(tool, "tool_not_held")
        pot = self._container(vessel)

        def circle(radius):
            angles = np.radians(np.arange(0, 360 * STIR_TURNS + 1, 10))
            return [(radius, 0.), *zip(radius * np.cos(angles), radius * np.sin(angles),
                                       strict=True), (0., 0.)]

        def build(tcp, current, grasp, start):
            collider = self._collider(tool, skip=(vessel,))
            return self._dip(data, pot, circle, collider, tcp, current, grasp, start,
                             "stir_unreachable_or_colliding")

        return self._plan(tool, build)

    def plan_scoop(self, tool, source, target):
        data = self._held(tool, "tool_not_held")
        pot, bowl = self._container(source), self._container(target)

        def build(tcp, current, grasp, start):
            dipping = self._collider(tool, skip=(source,))
            path = self._dip(data, pot, lambda radius: [(radius, 0.), (0., 0.)], dipping,
                             tcp, current, grasp, start, "scoop_unreachable_or_colliding")
            collider = self._collider(tool)
            over = current.copy()
            over[:3, 3] = [bowl.pos[0], bowl.pos[1], _top(bowl) + ABOVE + self._reach(data, grasp)]
            path += self._travel(over, collider, path[-1][0], tcp, "scoop_target_unreachable")
            return path + self._tilt(over, collider, path[-1][0], tcp, "scoop_tilt_unreachable")

        return self._plan(tool, build)

    def verify(self):
        if self.kind != "motion":
            return super().verify()
        model = self.model
        obj = model.objects[self.object_id][1]
        return bool(np.allclose(model.robot.qs, self.path[-1][0], atol=1e-5)
                    and self.held == self.object_id and obj.mounted_by is model.gripper
                    and np.isfinite(obj.tf).all() and obj.rotmat[2, 2] > .999)


async def advance_motion(call, planner, skill, state, args, stop, progress):
    if skill == "pour":
        held, plan = args.source, partial(planner.plan_pour, args.source, args.target)
    elif skill == "stir":
        held, plan = args.tool, partial(planner.plan_stir, args.tool, args.vessel)
    elif skill == "scoop":
        held, plan = args.tool, partial(planner.plan_scoop, args.tool, args.source, args.target)
    else:
        held, plan = args.object, partial(planner.plan_shake_off, args.object, args.times)
    if state.held != held:
        raise SkillFailure("object_not_held")
    return await run_plan(call, planner, plan, held, state, stop, progress)
