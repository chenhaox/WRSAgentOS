"""Real-arm skills: recognition builds the world, the model chooses skills, joints go out.

The node owns the arm. Detection, segmentation and grasp inference stay in their own
nodes; pick reads the grasp set that infer_grasps produced in the same task.
"""

import asyncio
import time
from dataclasses import replace
from typing import Literal

import numpy as np

from wrs_agent.env.kitchen_motion import publish_kitchen
from wrs_agent.errors import AgentError
from wrs_agent.executor import ExecutionUnknown, SkillFailure
from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.nodes.fafu.arm import Stopped
from wrs_agent.nodes.grasp.data import read_grasps
from wrs_agent.nodes.vision.results import Objects2D
from wrs_agent.schemas import Boundary, Empty, Name, NodeSnapshot
from wrs_agent.skills.contracts import Skill
from wrs_agent.skills.robot import SKILLS
from wrs_agent.skills.vision.grasping import GraspSetId

# Grasps older than this, or taken before the arm last stopped, no longer match the scene.
GRASP_MAX_AGE = 20.0
PERCEPTION_WAIT = 6.0
MOTION_TIMEOUT = 240.0


class HomeArgs(Boundary):
    pose: Literal["home"]


class VisualPickArgs(Boundary):
    object: Name
    candidate_set_id: GraspSetId


OBSERVE = Skill(
    name="observe", arguments=Empty, resources=("arm",), timeout=MOTION_TIMEOUT,
    verification="world_rebuilt_from_detection",
    description="Turn the wrist camera toward the table, detect objects and rebuild the "
                "world snapshot from depth. Run it before anything else, and again before "
                "the perception steps of each pick, since the camera rides on the arm",
    instructions="Object ids, sizes and relations in the snapshot come only from this "
                 "recognition. An object that was not detected is not in the world; ask the "
                 "user to move it into view rather than inventing an id.",
)

PICK = Skill(
    name="pick", arguments=VisualPickArgs, result_parameters=("candidate_set_id",),
    resources=("arm", "gripper"), timeout=MOTION_TIMEOUT,
    verification="gripper_force_and_joint_readback",
    preconditions=("empty_gripper", "object_visible", "fresh_grasp_candidates"),
    description="Grasp an observed object with a GraspNet candidate and lift it",
    instructions="Every pick is five steps in one task: observe; locate_object with the "
                 "target and selection written in that object's label; segment_region bound "
                 "to the location_id; infer_grasps bound to the segment_id; then pick with "
                 "object set to the snapshot id and candidate_set_id bound to infer_grasps. "
                 "A loose piece inside a container is not picked; pick the container and pour. "
                 "When the label says the object cannot be located alone, ask the user.",
)


def _bound(name, **changes):
    return replace(SKILLS[name], timeout=MOTION_TIMEOUT, **changes)


CONTRACTS = {
    "observe": OBSERVE,
    "move_named_pose": _bound("move_named_pose", arguments=HomeArgs,
                              description="Move to the folded home pose. Manipulation skills "
                                          "plan their own approach; use it only when asked"),
    "move_relative": _bound("move_relative"),
    "set_gripper": _bound("set_gripper"),
    "pick": PICK,
    "place_on": _bound("place_on"),
    "seat_in": _bound("seat_in"),
    "pour": _bound("pour"),
    "shake_off": _bound("shake_off"),
}


class RealSkills:
    def __init__(self, node, call, planner, arm, state, config):
        self.node, self.call, self.planner, self.arm = node, call, planner, arm
        self.state, self.config = state, config
        self.still_since_ns = time.time_ns()

    def bound(self):
        handlers = {"observe": self.observe, "pick": self.pick}
        return [contract.bind(handlers.get(name) or self._planned(name))
                for name, contract in CONTRACTS.items()]

    # -- Motion -----------------------------------------------------------------------

    async def _sync(self):
        q = await asyncio.to_thread(self.arm.joints)
        self.state.kinematics = await self.call(self.planner.sync, q)

    async def _run(self, stop, progress):
        """Send the planned frames. The gripper acts where the plan picks up or lets go."""
        frames = await self.call(self.planner.frames)
        held = await self.call(lambda: self.planner.motion.held)
        segments, begin = [], 0
        for index, (_, holding) in enumerate(frames):
            if holding != held:
                segments.append((begin, index, "close" if holding else "open"))
                begin, held = index, holding
        segments.append((begin, len(frames), None))
        motion = self.planner.motion
        self.state.pose = None
        try:
            for begin, end, event in segments:
                if end > begin:
                    path = [frames[i][0] for i in range(begin, end)]
                    await asyncio.to_thread(self.arm.move, path, stop=stop)
                    for index in range(begin, end):
                        await self.call(publish_kitchen, motion, self.state, index)
                    progress(end / max(1, len(frames)))
                if event == "close":
                    if not await asyncio.to_thread(self.arm.grasp):
                        await asyncio.to_thread(self.arm.open)
                        await self.call(motion._set_jaw, 0.0)
                        raise SkillFailure("grasp_missed")
                elif event == "open":
                    await asyncio.to_thread(self.arm.open)
        except Stopped:
            await self._sync()
            return False
        finally:
            self.still_since_ns = time.time_ns()
        reached = np.asarray(await asyncio.to_thread(self.arm.joints))
        if float(np.max(np.abs(reached - np.asarray(frames[-1][0])))) > float(
                self.config["arm"]["arrive_tolerance"]):
            await self._sync()
            raise ExecutionUnknown("joint_readback_off_target", reason="joint_target_not_reached")
        return bool(await self.call(motion.verify))

    def _planned(self, name):
        async def handler(state, args, stop, progress):
            await self._sync()
            arguments = args.model_dump()
            await self.call(self.planner.plan, name, arguments)
            done = await self._run(stop, progress)
            if done and name == "set_gripper" and self.planner.motion.held is None:
                # Nothing held: the jaw itself is the whole effect.
                if args.command == "close":
                    await asyncio.to_thread(self.arm.grasp)
                else:
                    await asyncio.to_thread(self.arm.open)
            if done and name == "move_named_pose":
                state.pose = args.pose
            return done

        return handler

    # -- Perception -------------------------------------------------------------------

    async def _detections(self, after_ns):
        """The first detection result captured after the arm stopped, with its cloud."""
        perceive = self.config["perception"]
        registry = self.node.discover()
        await registry.refresh([perceive["detector"], perceive["camera"]])
        detector = self.node.connect(perceive["detector"])
        camera = CameraClient(self.node.connect(perceive["camera"]))
        deadline = time.monotonic() + PERCEPTION_WAIT
        while time.monotonic() < deadline:
            snapshot = NodeSnapshot.model_validate(await detector.request("request/snapshot", {}))
            result_id = getattr(snapshot.data, "result_id", None)
            if result_id:
                result = Objects2D.model_validate(await detector.request(
                    "request/vision/result", {"result_id": result_id}, timeout=1.0))
                if result.captured_at_ns > after_ns:
                    info, arrays, _ = await camera.read(components=("points",), max_age_s=1.0)
                    if (info.captured_at_ns > after_ns and info.stream_id == result.source_stream_id
                            and (info.width, info.height) == (result.width, result.height)):
                        return result, arrays["points"], info.captured_at_ns
            await asyncio.sleep(0.1)
        raise SkillFailure("perception_unavailable")

    async def observe(self, state, args, stop, progress):
        await self._sync()
        await self.call(self.planner.plan_look)
        if not await self._run(stop, lambda value: progress(value * 0.8)):
            return False
        await asyncio.sleep(float(self.config["observe"]["settle_s"]))
        still = time.time_ns()
        before = np.asarray(await asyncio.to_thread(self.arm.joints))
        try:
            result, points, captured = await self._detections(still)
        except (AgentError, TimeoutError, ValueError) as exc:
            raise SkillFailure("perception_unavailable") from exc
        after = np.asarray(await asyncio.to_thread(self.arm.joints))
        if float(np.max(np.abs(after - before))) > 0.01:
            raise SkillFailure("arm_moved_during_observe")
        if self.planner.motion.held is None:
            # Holding something: keep the world, the hand would hide part of it.
            objects = await self.call(self.planner.perceive, result.items, points, captured)
            state.objects = {name: data.model_copy(deep=True) for name, data in objects.items()}
            state.facts["detections"] = len(result.items)
            state.facts["objects"] = len(objects) - 1
        await self.call(publish_kitchen, self.planner.motion, state)
        progress(1.0)
        return True

    async def pick(self, state, args, stop, progress):
        request = self.node.executor.active_request()
        grasp_node = self.config["perception"]["grasps"]
        await self._sync()
        if self.planner.motion.held is not None:
            raise SkillFailure("gripper_already_holding")
        if args.object not in self.planner.objects or args.object == "table":
            raise SkillFailure(f"{args.object}_not_in_scene")
        try:
            await self.node.discover().refresh([grasp_node])
            data, age = await read_grasps(self.node.connect(grasp_node), args.candidate_set_id,
                                          request.task_id)
        except (AgentError, TimeoutError, ValueError) as exc:
            raise SkillFailure("grasp_candidates_unavailable") from exc
        observation = data.info.segment.observation
        if age > GRASP_MAX_AGE:
            raise SkillFailure("observation_stale")
        if observation.captured_at_ns < self.still_since_ns:
            raise SkillFailure("grasps_taken_before_arm_stopped")
        world_from_cam = await self.call(self.planner.world_from_cam)
        seen = await self.call(self.planner.segment_object, data.segment.arrays["points"],
                               data.segment.mask, world_from_cam)
        if seen != args.object:
            raise SkillFailure("grasp_target_mismatch")
        entry = self.planner.catalog.classes[self.planner.classes[args.object]]
        if not entry.graspable:
            raise SkillFailure("object_not_graspable")
        if args.object in self.planner.motion.carriers and not entry.vessel:
            raise SkillFailure("contents_move_with_container")
        candidates = sorted(data.info.candidates, key=lambda c: -c.score)
        await self.call(self.planner.plan_pick, args.object, candidates, world_from_cam)
        await asyncio.to_thread(self.arm.open)
        return await self._run(stop, progress)
