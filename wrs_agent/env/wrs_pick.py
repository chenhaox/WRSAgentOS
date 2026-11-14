"""Name-addressed pick and surface placement in the WRS virtual scene.

Upright boxes only. The jaw-width rule is a virtual contact assumption, never a sensed grasp.
"""

import asyncio
import contextlib
from functools import partial

import numpy as np

from wrs_agent.env.wrs_grasp import box_capture_width
from wrs_agent.executor import ExecutionUnknown, SkillFailure

SKILLS = ("pick", "place_on")

APPROACH = .06
LIFT = .08
# The held box is lowered to just above the surface, then settles onto it on release.
CLEARANCE = .002
# A resting box floats this far above its support; flush contact counts as penetration.
REST_GAP = .001


def _upright(data):
    return np.asarray(data.rotmat)[2, 2] > .999


def _footprint(data):
    """Half extents of an upright box's world-axis bounding rectangle."""
    rotmat = np.abs(np.asarray(data.rotmat)[:2, :2])
    return rotmat @ (np.asarray(data.geometry.xyz_lengths[:2]) / 2)


def top_grasp(data):
    """Grasp-center pose that descends along -Z and closes across the narrower side face."""
    if not _upright(data):
        raise SkillFailure("object_not_upright")
    rotmat = np.asarray(data.rotmat)
    lengths = np.asarray(data.geometry.xyz_lengths)
    sides = [axis for axis in (0, 1) if .003 <= lengths[axis] <= .045]
    if not sides:
        raise SkillFailure("object_exceeds_dh50_opening")
    closing = rotmat[:, min(sides, key=lambda axis: lengths[axis])]
    approach = np.array([0., 0., -1.])
    pose = np.eye(4)
    pose[:3, :3] = np.column_stack((closing, np.cross(approach, closing), approach))
    half = lengths[2] / 2
    pose[:3, 3] = np.asarray(data.pos) + [0., 0., half - min(half, .015)]
    return pose


class VirtualPickPlace:
    """Owned by the WRS model worker. Planning always restores the model it searched with."""

    def __init__(self, model):
        self.model = model
        # Viewer objects are visual-only; planning needs actual collision bodies.
        for name, (data, previous) in list(model.objects.items()):
            obj = model.wrs.wssop.box(
                pos=data.pos, rotmat=data.rotmat, xyz_lengths=data.geometry.xyz_lengths,
                rgb=data.rgb, name=name, collision_type=model.wrs.wuc.CollisionType.AABB,
            )
            previous.remove_from_scene(model.scene)
            obj.add_to_scene(model.scene)
            model.objects[name] = (data, obj)
        self.held = None
        self.passive = {name: obj.collision_group for name, (_, obj) in model.objects.items()}
        self.kind = self.object_id = self.switch_at = self.goal = None
        self.path = []

    def _collider(self, moving, *, skip=()):
        model, robot, gripper = self.model, self.model.robot, self.model.gripper
        fixtures = [obj for name, (_, obj) in model.objects.items()
                    if name != moving and name not in skip]
        collider = robot.build_collider(fixtures=fixtures, objects=[model.objects[moving][1]])
        # This WRS converter does not apply the mounted DH50's finger-pair ignores.
        collider.acm += tuple((gripper.runtime_lnks[a], gripper.runtime_lnks[b])
                              for a, b in gripper.structure.compiled.collision_ignores_idx)
        collider.refresh()
        return collider

    def _ignore_fingers(self, collider, obj):
        collider.acm += tuple((obj, link) for link in self.model.gripper.runtime_lnks[1:])
        collider.refresh()

    def _jaw(self, start, stop):
        return np.linspace(start, stop, 2 + int(abs(stop - start) / .001))

    def _clear(self, collider, widths):
        gripper = self.model.gripper
        for width in widths:
            gripper.set_opening(float(width))
            collider.set_mecba_qpos(gripper, gripper.qs)
            if collider.is_collided(self.model.robot.qs):
                return False
        return True

    def _segment(self, motion):
        return [(q, float(e[0] * 2))
                for q, e in zip(motion.robot_qpos_list, motion.ee_qpos_list, strict=True)]

    def plan_pick(self, object_id):
        model, robot, gripper = self.model, self.model.robot, self.model.gripper
        if self.held is not None:
            raise SkillFailure("gripper_already_holding")
        if object_id not in model.objects:
            raise SkillFailure("object_not_in_scene")
        # Plan from where the box is now, not where the scene file put it.
        data, obj = model.object_data(object_id), model.objects[object_id][1]
        pose = top_grasp(data)
        width = box_capture_width(pose, data)
        initial_qs, initial_width, initial_tf = robot.qs.copy(), model.width, obj.tf.copy()
        tcp = gripper.tcp("grasp_center")
        try:
            collider = self._collider(object_id)
            gripper.set_opening(float(gripper.jaw_range[1]))
            collider.set_mecba_qpos(gripper, gripper.qs)
            above = pose.copy()
            above[:3, 3] -= APPROACH * pose[:3, 2]
            travel = robot.moveto(above, collider=collider, tcp=tcp, start_qs=initial_qs,
                                  ee_qpos=gripper.qs.copy(), time_limit=1, max_iters=1000)
            if travel is None:
                raise SkillFailure("pregrasp_unreachable_or_colliding")
            approach = robot.insert(pose[:3, 3], pose[:3, :3], collider=collider, tcp=tcp,
                                    start_qs=travel.robot_qpos_list[-1],
                                    ee_qpos=gripper.qs.copy(), granularity=.005)
            if approach is None:
                raise SkillFailure("approach_unreachable_or_colliding")
            grasp_qs = approach.robot_qpos_list[-1]
            robot.fk(grasp_qs)
            # Only the fingers may touch the target while the jaw closes.
            self._ignore_fingers(collider, obj)
            closing = self._jaw(float(gripper.jaw_range[1]), width)
            if not self._clear(collider, closing):
                raise SkillFailure("jaw_closure_colliding")
            gripper.hold(obj)
            obj.collision_group = model.wrs.wuc.CollisionGroup.ACTIVE
            collider.refresh()
            collider.set_mecba_qpos(gripper, gripper.qs)
            lift = robot.insert(pose[:3, 3] + [0., 0., LIFT], pose[:3, :3], collider=collider,
                                tcp=tcp, start_qs=grasp_qs, ee_qpos=gripper.qs.copy(),
                                granularity=.005)
            if lift is None:
                raise SkillFailure("lift_unreachable_or_colliding")
            path = self._segment(travel) + self._segment(approach)
            path += [(grasp_qs, float(opening)) for opening in closing]
            switch_at = len(path)
            path += self._segment(lift)
        finally:
            if obj.mounted_by is gripper:
                gripper.release(obj)
            obj.collision_group = self.passive[object_id]
            robot.fk(initial_qs)
            gripper.set_opening(initial_width)
            obj.set_pos_rotmat(initial_tf[:3, 3], initial_tf[:3, :3])
        self.kind, self.object_id, self.switch_at, self.path = "pick", object_id, switch_at, path
        self.goal = float(initial_tf[2, 3]) + LIFT
        return len(path)

    def place_pose(self, surface_id):
        """Resting pose of the held box at the surface center, keeping its heading."""
        if surface_id not in self.model.objects:
            raise SkillFailure("surface_not_in_scene")
        objects = {name: self.model.object_data(name) for name in self.model.objects}
        held, surface = objects[self.held], objects[surface_id]
        if not _upright(surface):
            raise SkillFailure("surface_not_level")
        if not _upright(held):
            raise SkillFailure("held_object_not_upright")
        half = _footprint(held)
        if (half > _footprint(surface)).any():
            raise SkillFailure("surface_too_small")
        bottom = surface.pos[2] + surface.geometry.xyz_lengths[2] / 2 + REST_GAP
        height = held.geometry.xyz_lengths[2]
        center = np.array([surface.pos[0], surface.pos[1], bottom + height / 2])
        for name, other in objects.items():
            if name in (self.held, surface_id):
                continue
            low = other.pos[2] - other.geometry.xyz_lengths[2] / 2
            high = other.pos[2] + other.geometry.xyz_lengths[2] / 2
            beside = (np.abs(np.asarray(other.pos[:2]) - center[:2])
                      >= half + _footprint(other)).any()
            if not beside and low < bottom + height and high > bottom + 1e-6:
                raise SkillFailure("surface_occupied")
        pose = np.eye(4)
        pose[:3, :3] = np.asarray(held.rotmat)
        pose[:3, 3] = center
        return pose

    def plan_place(self, surface_id):
        model, robot, gripper = self.model, self.model.robot, self.model.gripper
        if self.held is None:
            raise SkillFailure("nothing_held")
        rest = self.place_pose(surface_id)
        object_id = self.held
        obj = model.objects[object_id][1]
        initial_qs, initial_width, initial_tf = robot.qs.copy(), model.width, obj.tf.copy()
        tcp = gripper.tcp("grasp_center")
        # The grasp keeps its pose relative to the box while the box moves to rest.
        target = rest @ np.linalg.inv(initial_tf) @ tcp.tf
        lowered = target.copy()
        lowered[2, 3] += CLEARANCE
        above = lowered.copy()
        above[2, 3] += APPROACH
        try:
            collider = self._collider(object_id)
            collider.set_mecba_qpos(gripper, gripper.qs)
            travel = robot.moveto(above, collider=collider, tcp=tcp, start_qs=initial_qs,
                                  ee_qpos=gripper.qs.copy(), time_limit=1, max_iters=1000)
            if travel is None:
                raise SkillFailure("preplace_unreachable_or_colliding")
            descent = robot.insert(lowered[:3, 3], lowered[:3, :3], collider=collider, tcp=tcp,
                                   start_qs=travel.robot_qpos_list[-1],
                                   ee_qpos=gripper.qs.copy(), granularity=.005)
            if descent is None:
                raise SkillFailure("placement_descent_colliding")
            release_qs = descent.robot_qpos_list[-1]
            robot.fk(release_qs)
            gripper.release(obj)
            obj.set_pos_rotmat(rest[:3, 3], rest[:3, :3])
            obj.collision_group = self.passive[object_id]
            collider.refresh()
            self._ignore_fingers(collider, obj)
            opening = self._jaw(initial_width, float(gripper.jaw_range[1]))
            if not self._clear(collider, opening):
                raise SkillFailure("jaw_opening_colliding")
            retreat = robot.insert(lowered[:3, 3] + [0., 0., APPROACH], lowered[:3, :3],
                                   collider=collider, tcp=tcp, start_qs=release_qs,
                                   ee_qpos=gripper.qs.copy(), granularity=.005)
            if retreat is None:
                raise SkillFailure("retreat_unreachable_or_colliding")
            path = self._segment(travel) + self._segment(descent)
            switch_at = len(path)
            path += [(release_qs, float(width)) for width in opening]
            path += self._segment(retreat)
        finally:
            robot.fk(initial_qs)
            gripper.set_opening(initial_width)
            if obj.mounted_by is not gripper:
                obj.set_pos_rotmat(initial_tf[:3, 3], initial_tf[:3, :3])
                gripper.hold(obj)
            obj.collision_group = model.wrs.wuc.CollisionGroup.ACTIVE
        self.kind, self.object_id, self.switch_at, self.path = "place", object_id, switch_at, path
        self.goal = rest
        return len(path)

    def step(self, index):
        model, gripper = self.model, self.model.gripper
        qs, width = self.path[index]
        _, obj = model.objects[self.object_id]
        if index == self.switch_at and self.kind == "place":
            gripper.release(obj)
            obj.set_pos_rotmat(self.goal[:3, 3], self.goal[:3, :3])
            obj.collision_group = self.passive[self.object_id]
            self.held = None
        model.sync(qs, width)
        if index == self.switch_at and self.kind == "pick":
            gripper.hold(obj)
            obj.collision_group = model.wrs.wuc.CollisionGroup.ACTIVE
            self.held = self.object_id
        return model.read(), model.object_data(self.object_id), self.held

    def verify(self):
        model = self.model
        obj = model.objects[self.object_id][1]
        arrived = np.allclose(model.robot.qs, self.path[-1][0], atol=1e-5)
        if self.kind == "pick":
            return bool(arrived and self.held == self.object_id and np.isfinite(obj.tf).all()
                        and float(obj.pos[2]) >= self.goal - .005)
        return bool(arrived and self.held is None and obj.mounted_by is None
                    and np.allclose(obj.tf, self.goal, atol=1e-6))


async def advance_pick_place(call, planner, skill, state, args, stop, progress):
    if skill == "pick":
        plan = partial(planner.plan_pick, args.object)
    elif state.held != args.object:
        raise SkillFailure("object_not_held")
    else:
        plan = partial(planner.plan_place, args.surface)
    return await run_plan(call, planner, plan, args.object, state, stop, progress)


async def run_plan(call, planner, plan, object_id, state, stop, progress):
    """Plan on the model worker, then play the path one step at a time and verify it."""
    try:
        count = await call(plan)
    except SkillFailure:
        raise  # Planning restored the model; nothing moved.
    except Exception:
        state.kinematics = {**state.kinematics, "valid": False}
        raise ExecutionUnknown("wrs_planning_state_unknown") from None
    state.pose = None
    try:
        for index in range(count):
            if stop.is_set():
                return False
            state.kinematics, obj, state.held = await call(planner.step, index)
            state.objects[object_id] = obj
            state.version += 1
            progress((index + 1) / count)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), .02)
        if stop.is_set():
            return False
        return await call(planner.verify)
    except Exception:
        state.kinematics = {**state.kinematics, "valid": False}
        raise ExecutionUnknown("wrs_model_state_unknown") from None
