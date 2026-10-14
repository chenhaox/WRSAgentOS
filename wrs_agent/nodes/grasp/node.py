"""Grasp inference owns bounded proposals and their scene data, never a robot handle."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from pydantic import Field

from wrs_agent.errors import AgentError
from wrs_agent.executor import ActionExecutor, SkillFailure
from wrs_agent.nodes.grasp.data import GraspData, GraspSet, LatestGrasps, pack_grasps
from wrs_agent.nodes.node import Node
from wrs_agent.nodes.segmentation.data import SegmentInfo, read_segment
from wrs_agent.nodes.vision.artifacts import ArtifactCache, ArtifactRequest, artifact_owner
from wrs_agent.schemas import Boundary, Empty, Name, VisionData, new_id
from wrs_agent.skills.vision.grasping import INFER_GRASPS, GraspsOutput


class GraspNetOptions(Boundary):
    weights: Path = Field(strict=False)
    weights_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Path = Field(strict=False)
    source_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    segmentation: Name = "sam3"
    max_width_m: float = Field(default=0.05, gt=0, le=0.1)


@dataclass
class GraspState:
    version: int = 0
    completed: int = 0

    def snapshot(self):
        return VisionData(completed=self.completed, health="ready")


class GraspNetNode(Node):
    node_type = "vision"
    action_service = True
    options_type = GraspNetOptions

    def _predictor(self):
        from wrs_agent.nodes.grasp.graspnet import GraspNetPredictor

        return GraspNetPredictor(
            self.options.weights, self.options.weights_sha256, source=self.options.source,
            source_revision=self.options.source_revision, max_width_m=self.options.max_width_m,
        )

    async def setup(self):
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="graspnet")
        self.on_close(self._close_worker)
        self.loop = asyncio.get_running_loop()
        self.predictor = await self.loop.run_in_executor(self.worker, self._predictor)
        self.cache = ArtifactCache()
        self.latest_request = None
        self.actions(ActionExecutor(
            self.journal, state=GraspState(), skills=(INFER_GRASPS.bind(self.infer),),
            backend="graspnet", features_extra={"robot_controls": False, "controller_flush": False,
                                              "verification": "task_scoped_grasp_proposals",
                                              "stop_scope": "grasp_proposals"},
        ))
        self.transport.register_handler("request/grasp/data", self.read, binary=True)
        self.transport.register_handler("request/grasp/latest", self.latest)
        self.discover()
        self.spawn(self._expire())

    async def _close_worker(self):
        await asyncio.to_thread(self.worker.shutdown, wait=True, cancel_futures=True)

    async def _expire(self):
        while True:
            await asyncio.sleep(1)
            self.cache.expire()

    async def _check_segment(self, segment):
        request = ArtifactRequest(artifact_id=segment.segment_id, task_id=segment.owner.task_id)
        reply = await self.connect(self.options.segmentation).request(
            "request/segmentation/info", request.model_dump(),
        )
        if SegmentInfo.model_validate(reply["segment"]) != segment:
            raise AgentError("segment_changed")

    async def infer(self, state, args, stop, progress):
        self.latest_request = None  # A new attempt must not display an older task's proposals.
        owner = artifact_owner(self.executor)
        try:
            await self.registry.refresh([self.options.segmentation])
            segment, age = await read_segment(self.connect(self.options.segmentation),
                                              args.segment_id, owner.task_id)
            observed_at = time.monotonic() - age
            if age >= self.cache.ttl:
                raise SkillFailure("observation_stale")
            if stop.is_set():
                return False
            candidates = await self.loop.run_in_executor(
                self.worker, partial(self.predictor.infer, segment.arrays["points"], segment.mask),
            )
            if stop.is_set():
                return False
            await self._check_segment(segment.info)
            data = GraspData(GraspSet(
                candidate_set_id=new_id(), owner=owner, segment=segment.info,
                model=self.predictor.name, weights_sha256=self.predictor.sha256,
                source_revision=self.predictor.revision, candidates=candidates,
            ), segment)
            size = segment.nbytes + len(data.info.model_dump_json().encode())
            self.cache.put(data.info.candidate_set_id, owner, data, nbytes=size,
                           observed_at=observed_at)
        except (AgentError, ValueError, TimeoutError) as exc:
            raise SkillFailure(str(exc)[:200]) from exc
        state.completed += 1
        self.latest_request = ArtifactRequest(artifact_id=data.info.candidate_set_id,
                                             task_id=owner.task_id)
        return GraspsOutput(candidate_set_id=data.info.candidate_set_id,
                            observation_id=segment.info.observation.observation_id,
                            count=len(candidates))

    async def latest(self, payload):
        Empty.model_validate(payload)
        request = self.latest_request
        if request is None:
            raise AgentError("grasp_not_available")
        data, _ = self.cache.get(request, self.executor)
        await self._check_segment(data.segment.info)
        data, age = self.cache.get(request, self.executor)
        if request != self.latest_request:
            raise AgentError("grasp_changed")
        return LatestGrasps(candidate_set_id=data.info.candidate_set_id,
                            task_id=data.info.owner.task_id,
                            observation_id=data.info.segment.observation.observation_id,
                            age_s=age).model_dump()

    async def read(self, payload):
        request = ArtifactRequest.model_validate(payload)
        data, age = self.cache.get(request, self.executor)
        await self._check_segment(data.segment.info)
        packet = await asyncio.to_thread(pack_grasps, data, age)
        self.cache.get(request, self.executor)
        return packet
