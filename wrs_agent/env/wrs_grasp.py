"""WRS virtual grasp adapter. Known geometry/contact assumptions never enable hardware."""

import asyncio
import time
from dataclasses import dataclass, field

import numpy as np

from wrs_agent.env.fixed_camera import load_calibration, validate_observation
from wrs_agent.env.wrs import open_model
from wrs_agent.executor import ActionExecutor, ExecutionUnknown, SkillFailure
from wrs_agent.schemas import RobotData, SceneData
from wrs_agent.skills.robot import SKILLS
from wrs_agent.skills.vision.grasping import VIRTUAL_GRASP, GraspOutput


def points_inside(points, obj, tolerance=.003):
    local = (points - np.asarray(obj.pos)) @ np.asarray(obj.rotmat)
    return (np.abs(local) <= np.asarray(obj.geometry.xyz_lengths) / 2 + tolerance).all(axis=1)


def match_scene(segment, calibration, objects):
    """Virtual profile requires the entire valid observed scene to match configured boxes."""
    cloud = segment.arrays["points"]
    valid = np.isfinite(cloud).all(axis=2) & (cloud[:, :, 2] > 0)
    matrix = calibration.matrix()
    points = cloud[valid] @ matrix[:3, :3].T + matrix[:3, 3]
    selected = segment.mask[valid]
    if np.count_nonzero(selected) < 64:
        raise SkillFailure("insufficient_target_depth")
    known = np.zeros(len(points), dtype=bool)
    matches = []
    for name, obj in objects.items():
        if not obj.valid or obj.geometry is None or obj.pos is None or obj.rotmat is None:
            continue
        contained = points_inside(points, obj)
        known |= contained
        if contained[selected].all():
            matches.append(name)
    if not known.all():
        raise SkillFailure("unmodeled_scene_points")
    if len(matches) != 1:
        raise SkillFailure("virtual_target_not_uniquely_localized")
    return matches[0]


def dh50_pose(candidate, calibration):
    """GraspNet +x approach/+y closing -> DH50 +z approach/+x closing.

    Translation keeps the upstream target-point origin. This virtual geometric mapping
    is not a measured hardware tool calibration; candidate.depth_m remains a proposal.
    """
    camera = calibration.matrix()
    grasp_rotation = np.asarray(candidate.rotation).reshape(3, 3)
    pose = np.eye(4)
    pose[:3, :3] = camera[:3, :3] @ grasp_rotation[:, [1, 2, 0]]
    pose[:3, 3] = camera[:3, :3] @ np.asarray(candidate.translation) + camera[:3, 3]
    return pose


def box_capture_width(pose, obj):
    """Conservative virtual contact rule: opposing faces of an axis-aligned box grasp."""
    axes = np.asarray(obj.rotmat)
    closing = axes.T @ pose[:3, 0]
    axis = int(np.argmax(np.abs(closing)))
    if abs(closing[axis]) < .999:
        raise SkillFailure("virtual_box_grasp_not_face_aligned")
    local = axes.T @ (pose[:3, 3] - np.asarray(obj.pos))
    half = np.asarray(obj.geometry.xyz_lengths) / 2
    if abs(local[axis]) > .002 or (np.abs(local) > half - .002).any():
        raise SkillFailure("virtual_grasp_outside_capture_region")
    width = float(2 * half[axis])
    if not .003 <= width <= .045:
        raise SkillFailure("object_exceeds_dh50_opening")
    return width


class VirtualGrasp:
    """Owned by the same WRS worker as the robot. Planning always restores model state."""

    def __init__(self, model):
        self.model = model
        # Ordinary viewer objects are visual-only. This profile creates actual collision bodies.
        for name, (data, previous) in list(model.objects.items()):
            obj = model.wrs.wssop.box(
                pos=data.pos, rotmat=data.rotmat, xyz_lengths=data.geometry.xyz_lengths,
                rgb=data.rgb, name=name, collision_type=model.wrs.wuc.CollisionType.AABB,
            )
            previous.remove_from_scene(model.scene)
            obj.add_to_scene(model.scene)
            model.objects[name] = (data, obj)
        self.held = None
        self.object_id = None
        self.path = []
        self.attach_at = None
        self.initial_height = None

    def plan(self, data, calibration, object_id):
        model, robot, gripper = self.model, self.model.robot, self.model.gripper
        if self.held is not None:
            raise SkillFailure("gripper_already_holding")
        obj_data, obj = model.objects[object_id]
        initial_qs, initial_width, initial_tf = robot.qs.copy(), model.width, obj.tf.copy()
        initial_group = obj.collision_group
        fixtures = [value[1] for key, value in model.objects.items() if key != object_id]
        # Include the target during travel and descent with the jaws open.
        collider = robot.build_collider(fixtures=fixtures, objects=[obj])
        # This WRS converter only visits scene.mecbas when applying structural ignores;
        # the mounted DH50 is not in that list. Preserve its declared finger-pair ignores.
        collider.acm += tuple((gripper.runtime_lnks[a], gripper.runtime_lnks[b])
                              for a, b in gripper.structure.compiled.collision_ignores_idx)
        collider.refresh()
        structural_excludes = collider.acm
        tcp = gripper.tcp("grasp_center")
        reason = "no_compatible_virtual_grasp"
        try:
            for candidate in data.info.candidates[:8]:
                robot.fk(initial_qs)
                gripper.set_opening(initial_width)
                collider.acm = structural_excludes
                collider.refresh()
                pose = dh50_pose(candidate, calibration)
                try:
                    width = box_capture_width(pose, obj_data)
                except SkillFailure as exc:
                    reason = str(exc)
                    continue
                if candidate.width_m < width - .003:
                    continue
                gripper.set_opening(float(gripper.jaw_range[1]))
                collider.set_mecba_qpos(gripper, gripper.qs)
                pre = pose.copy()
                pre[:3, 3] -= .06 * pose[:3, 2]
                travel = robot.moveto(pre, collider=collider, tcp=tcp, start_qs=initial_qs,
                                      ee_qpos=gripper.qs.copy(), time_limit=1, max_iters=1000)
                if travel is None:
                    reason = "virtual_pregrasp_unreachable_or_colliding"
                    continue
                approach = robot.insert(pose[:3, 3], pose[:3, :3], collider=collider, tcp=tcp,
                                        start_qs=travel.robot_qpos_list[-1],
                                        ee_qpos=gripper.qs.copy(), granularity=.005)
                if approach is None:
                    reason = "virtual_approach_unreachable_or_colliding"
                    continue
                robot.fk(approach.robot_qpos_list[-1])
                # Only the two fingers may touch the target while closing. Check the swept jaw.
                collider.acm += tuple((obj, link) for link in gripper.runtime_lnks[1:])
                collider.refresh()
                closing = np.linspace(float(gripper.jaw_range[1]), width,
                                      2 + int((float(gripper.jaw_range[1]) - width) / .001))
                closure_valid = True
                for opening in closing:
                    gripper.set_opening(float(opening))
                    collider.set_mecba_qpos(gripper, gripper.qs)
                    if collider.is_collided(robot.qs):
                        closure_valid = False
                        break
                if not closure_valid:
                    reason = "virtual_jaw_closure_colliding"
                    continue
                gripper.set_opening(width)
                # Rigid virtual attachment is only made after the explicit box capture check.
                gripper.hold(obj)
                obj.collision_group = model.wrs.wuc.CollisionGroup.ACTIVE
                collider.refresh()
                collider.set_mecba_qpos(gripper, gripper.qs)
                lifted = pose[:3, 3] + np.array([0., 0., .08])
                try:
                    lift = robot.insert(lifted, pose[:3, :3], collider=collider, tcp=tcp,
                                        start_qs=approach.robot_qpos_list[-1],
                                        ee_qpos=gripper.qs.copy(), granularity=.005)
                finally:
                    gripper.release(obj)
                    obj.collision_group = initial_group
                    obj.set_pos_rotmat(initial_tf[:3, 3], initial_tf[:3, :3])
                    collider.refresh()
                if lift is None:
                    reason = "virtual_lift_unreachable_or_colliding"
                    continue
                path = [(q, float(e[0] * 2)) for motion in (travel, approach)
                        for q, e in zip(motion.robot_qpos_list, motion.ee_qpos_list, strict=True)]
                path += [(approach.robot_qpos_list[-1], float(opening)) for opening in closing]
                self.attach_at = len(path)
                path += [(q, float(e[0] * 2)) for q, e in
                         zip(lift.robot_qpos_list, lift.ee_qpos_list, strict=True)]
                self.path, self.object_id = path, object_id
                self.initial_height = float(initial_tf[2, 3])
                return len(path)
            raise SkillFailure(reason)
        finally:
            if obj.mounted_by is gripper:
                gripper.release(obj)
            obj.collision_group = initial_group
            robot.fk(initial_qs)
            gripper.set_opening(initial_width)
            obj.set_pos_rotmat(initial_tf[:3, 3], initial_tf[:3, :3])

    def step(self, index):
        qs, width = self.path[index]
        self.model.sync(qs, width)
        obj = self.model.objects[self.object_id][1]
        if index == self.attach_at:
            self.model.gripper.hold(obj)
            obj.collision_group = self.model.wrs.wuc.CollisionGroup.ACTIVE
            self.held = self.object_id
        original = self.model.objects[self.object_id][0]
        updated = original.model_copy(update={"pos": obj.pos.tolist(),
                                             "rotmat": obj.rotmat.tolist()})
        self.model.objects[self.object_id] = (updated, obj)
        return self.model.read(), updated, self.held

    def verify(self):
        obj = self.model.objects[self.object_id][1]
        return (self.held == self.object_id and np.isfinite(obj.tf).all()
                and float(obj.pos[2]) - self.initial_height >= .075
                and np.allclose(self.model.robot.qs, self.path[-1][0], atol=1e-5))


@dataclass
class VisualGraspState:
    kinematics: dict
    objects: dict
    facts: dict = field(default_factory=dict)
    version: int = 0
    pose: str | None = "home"

    def snapshot(self):
        return SceneData(
            robot=RobotData(pose=self.pose, kinematics=self.kinematics,
                            held_object=self.facts.get("held_object")),
            objects={name: obj.model_copy(deep=True) for name, obj in self.objects.items()},
            facts=self.facts,
        )


async def make_visual_environment(journal_path, *, scene, calibration_path, read_data):
    calibration = await asyncio.to_thread(load_calibration, calibration_path)
    call, model, initial, close = await open_model(scene)
    state = VisualGraspState(initial.kinematics, initial.objects)
    try:
        grasp = await call(VirtualGrasp, model)
    except BaseException:
        await close()
        raise
    state.facts.update(visual_grasp="virtual_box_capture", calibration=calibration.calibration_id)

    async def observe(state, args, stop, progress):
        state.kinematics = await call(model.read)
        return state.kinematics["valid"]

    async def execute(state, args, stop, progress):
        request = executor.active_request()
        data, age = await read_data(args.candidate_set_id, request.task_id)
        observed_at = time.monotonic() - age
        try:
            validate_observation(calibration, data.info.segment.observation, age)
            if data.info.owner.task_id != request.task_id:
                raise SkillFailure("grasp_task_mismatch")
            object_id = await asyncio.to_thread(
                match_scene, data.segment, calibration, state.objects,
            )
            try:
                count = await call(grasp.plan, data, calibration, object_id)
            except SkillFailure:
                raise
            except Exception as exc:
                state.kinematics = {**state.kinematics, "valid": False}
                raise ExecutionUnknown("virtual_planning_state_unknown") from exc
            validate_observation(calibration, data.info.segment.observation,
                                 time.monotonic() - observed_at)
        except ValueError as exc:
            raise SkillFailure(str(exc)) from exc
        if stop.is_set():
            return False
        # Re-query after planning: cancellation/restart of the producing node revokes its result.
        checked, checked_age = await read_data(args.candidate_set_id, request.task_id)
        if checked.info != data.info:
            raise SkillFailure("grasp_proposal_changed")
        try:
            validate_observation(calibration, checked.info.segment.observation,
                                 max(checked_age, time.monotonic() - observed_at))
        except ValueError as exc:
            raise SkillFailure(str(exc)) from exc
        state.pose = None
        for index in range(count):
            if stop.is_set():
                return False
            try:
                kinematics, obj, held = await call(grasp.step, index)
            except Exception as exc:
                state.kinematics = {**state.kinematics, "valid": False}
                raise ExecutionUnknown("virtual_grasp_state_unknown") from exc
            state.kinematics, state.objects[object_id] = kinematics, obj
            state.facts.update(held_object=held, collision_checked=True)
            state.version += 1
            progress((index + 1) / count)
            await asyncio.sleep(.02)
        try:
            verified = await call(grasp.verify)
        except Exception as exc:
            state.kinematics = {**state.kinematics, "valid": False}
            raise ExecutionUnknown("virtual_verification_unavailable") from exc
        if not verified:
            raise ExecutionUnknown("virtual_attachment_verification_failed")
        label = state.objects[object_id].label or object_id
        return GraspOutput(object_id=object_id,
                           observation_id=data.info.segment.observation.observation_id,
                           text=f"虚拟场景中已夹持并抬起{label}。")

    executor = ActionExecutor(
        journal_path, state=state, close_backend=close,
        skills=(SKILLS["observe"].bind(observe), VIRTUAL_GRASP.bind(execute)), backend="wrs-grasp",
        features_extra={"verification": "wrs_virtual_attachment_and_lift",
                        "stop_scope": "virtual_fk_boundary", "controller_flush": False,
                        "unsupported": {"hardware": "No measured grasp/tool/contact verification.",
                                        "place": "This profile only validates grasp and lift."}},
    )
    return executor
