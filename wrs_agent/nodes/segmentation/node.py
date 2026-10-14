"""One segmentation node lifecycle, with explicitly selected resident model backends."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from wrs_agent.errors import AgentError
from wrs_agent.executor import ActionExecutor, SkillFailure
from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.nodes.node import Node
from wrs_agent.nodes.segmentation.data import SegmentData, SegmentInfo, pack_segment
from wrs_agent.nodes.vision.artifacts import ArtifactCache, ArtifactRequest, artifact_owner
from wrs_agent.nodes.vision.locations import read_location
from wrs_agent.schemas import Boundary, Name, VisionData, new_id
from wrs_agent.skills.vision.grasping import SEGMENT, SEGMENT_REGION, SegmentOutput


class SegmentationOptions(Boundary):
    backend: Literal["sam3", "fastsam", "fastsam-box"] = "sam3"
    weights: Path = Field(strict=False)
    weights_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    device: Literal["cpu", "cuda"] = "cuda"
    confidence: float = Field(default=0.5, gt=0, le=1)
    camera: Name = "camera"
    locator: Name = "yolo"
    clip_weights: Path | None = Field(default=None, strict=False)
    text_similarity: float = Field(default=.28, gt=0, le=1)
    text_margin: float = Field(default=.03, ge=0, le=1)

    @model_validator(mode="after")
    def check_backend(self):
        if self.backend == "fastsam" and self.clip_weights is None:
            raise ValueError("fastsam_requires_local_clip_weights")
        if self.backend != "fastsam" and self.clip_weights is not None:
            raise ValueError("clip_weights_require_fastsam_backend")
        return self


@dataclass
class SegmentationState:
    version: int = 0
    completed: int = 0

    def snapshot(self):
        return VisionData(completed=self.completed, health="ready")


class SegmentationNode(Node):
    node_type = "vision"
    options_type = SegmentationOptions
    action_service = True

    def _segmenter(self):
        if self.options.backend in {"fastsam", "fastsam-box"}:
            from wrs_agent.nodes.segmentation.fastsam import FastSamSegmenter

            return FastSamSegmenter(
                self.options.weights, self.options.weights_sha256,
                clip_weights=self.options.clip_weights, device=self.options.device,
                confidence=self.options.confidence, minimum=self.options.text_similarity,
                margin=self.options.text_margin,
            )
        from wrs_agent.nodes.segmentation.sam3 import Sam3Segmenter

        return Sam3Segmenter(self.options.weights, self.options.weights_sha256,
                             device=self.options.device, confidence=self.options.confidence)

    async def setup(self):
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="segmentation")
        self.on_close(self._close_worker)
        self.loop = asyncio.get_running_loop()
        self.segmenter = await self.loop.run_in_executor(self.worker, self._segmenter)
        self.cache = ArtifactCache()
        skill = SEGMENT_REGION if self.options.backend == "fastsam-box" else SEGMENT
        self.actions(ActionExecutor(
            self.journal, state=SegmentationState(), skills=(skill.bind(self.segment),),
            backend=self.options.backend, features_extra={"robot_controls": False,
                                          "controller_flush": False,
                                          "verification": "task_scoped_segmentation",
                                          "stop_scope": "segmentation_result"},
        ))
        self.transport.register_handler("request/segmentation/data", self.read, binary=True)
        self.transport.register_handler("request/segmentation/info", self.info_result)
        self.discover()
        self.spawn(self._expire())

    async def _close_worker(self):
        await asyncio.to_thread(self.worker.shutdown, wait=True, cancel_futures=True)

    async def _expire(self):
        while True:
            await asyncio.sleep(1)
            self.cache.expire()

    async def segment(self, state, args, stop, progress):
        owner = artifact_owner(self.executor)
        try:
            location = None
            if self.options.backend == "fastsam-box":
                await self.registry.refresh([self.options.locator])
                located, age = await read_location(self.connect(self.options.locator),
                                                   args.location_id, owner.task_id)
                location = located.info
                info, arrays, target = location.observation, located.arrays, location.target
                inference = partial(self.segmenter.segment_box, arrays["rgb"], location.object.box)
            else:
                await self.registry.refresh([self.options.camera])
                info, arrays, age = await CameraClient(self.connect(self.options.camera)).read()
                if age > 2:
                    raise SkillFailure("camera_stale")
                target = args.target
                inference = partial(self.segmenter.segment, arrays["rgb"], target, args.selection)
            observed_at = time.monotonic() - age
            if stop.is_set():
                return False
            mask, score = await self.loop.run_in_executor(self.worker, inference)
            if stop.is_set():
                return False
            data = SegmentData(SegmentInfo(
                segment_id=new_id(), owner=owner, observation=info,
                target=target, location=location,
                model=self.segmenter.name, weights_sha256=self.segmenter.sha256,
                score_kind=self.segmenter.score_kind,
                text_weights_sha256=getattr(self.segmenter, "text_sha256", None),
                score=score, pixel_count=int(mask.sum()),
            ), arrays, mask)
            self.cache.put(data.info.segment_id, owner, data, nbytes=data.nbytes,
                           observed_at=observed_at)
        except (AgentError, ValueError, TimeoutError) as exc:
            raise SkillFailure(str(exc)[:200]) from exc
        state.completed += 1
        return SegmentOutput(segment_id=data.info.segment_id, observation_id=info.observation_id)

    async def read(self, payload):
        request = ArtifactRequest.model_validate(payload)
        data, age = self.cache.get(request, self.executor)
        packet = await asyncio.to_thread(pack_segment, data, age)
        # Stop may have arrived while serializing the image; do not reply with revoked data.
        self.cache.get(request, self.executor)
        return packet

    async def info_result(self, payload):
        data, age = self.cache.get(ArtifactRequest.model_validate(payload), self.executor)
        return {"segment": data.info.model_dump(), "age_s": age}


# Keep existing SAM3 launch scripts working; there is still only one node implementation.
Sam3Node = SegmentationNode
Sam3Options = SegmentationOptions
