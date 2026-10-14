"""One resident YOLO worker and a small description skill; no robot or speech ownership."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from pydantic import Field, model_validator

from wrs_agent.errors import AgentError
from wrs_agent.executor import ActionExecutor, SkillFailure
from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.nodes.camera.frames import pack_frame
from wrs_agent.nodes.node import Node
from wrs_agent.nodes.vision.artifacts import ArtifactCache, ArtifactRequest, artifact_owner
from wrs_agent.nodes.vision.locations import (
    LocationData,
    LocationInfo,
    pack_location,
    select_object,
)
from wrs_agent.nodes.vision.results import Objects2D
from wrs_agent.schemas import Boundary, Name, VisionData, new_id
from wrs_agent.skills.vision import DESCRIBE
from wrs_agent.skills.vision.grasping import LOCATE, LocationOutput


class YoloOptions(Boundary):
    weights: Path = Field(strict=False)
    weights_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    camera: Name = "camera"
    confidence: float = Field(default=0.35, gt=0, le=1)
    device: str = Field(default="cpu", pattern=r"^(cpu|[0-9])$")
    clip_weights: Path | None = Field(default=None, strict=False)
    max_hz: float = Field(default=5.0, gt=0, le=15)
    table_roi: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def region(self):
        if self.table_roi is not None:
            x1, y1, x2, y2 = self.table_roi
            if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
                raise ValueError("invalid_normalized_table_roi")
        return self


@dataclass
class VisionState:
    version: int = 0
    completed: int = 0
    latest: Objects2D | None = None
    observed_at: float = 0
    health: str = "camera_unavailable"

    def snapshot(self):
        result = self.latest
        return VisionData(
            completed=self.completed, health=self.health,
            result_id=result.result_id if result else None,
            observation_id=result.observation_id if result else None,
            count=len(result.items) if result else 0,
        )


class ResultRequest(Boundary):
    result_id: Name


def items_in_roi(items, width, height, roi):
    if roi is None:
        return items
    x1, y1, x2, y2 = roi
    return [item for item in items if
            x1 <= (item.box[0] + item.box[2]) / (2 * width) <= x2
            and y1 <= (item.box[1] + item.box[3]) / (2 * height) <= y2]


class YoloNode(Node):
    node_type = "vision"
    action_service = True
    options_type = YoloOptions

    def _detector(self):
        from wrs_agent.nodes.vision.yolo import YoloDetector

        return YoloDetector(self.options.weights, self.options.weights_sha256,
                            confidence=self.options.confidence, device=self.options.device,
                            clip_weights=self.options.clip_weights)

    async def setup(self):
        self.state = VisionState()
        self.results = {}
        self.frames = {}  # At most two RGB-only inference frames; never retain depth/points.
        self.locations = ArtifactCache()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yolo")
        self.on_close(self._close_worker)
        self.loop = asyncio.get_running_loop()
        self.detector = await self.loop.run_in_executor(self.worker, self._detector)
        locate = LOCATE
        if self.options.clip_weights is None:
            locate = replace(LOCATE, instructions=LOCATE.instructions +
                             " This detector requires an exact label from: " +
                             ", ".join(self.detector.labels) +
                             ". Do not discard attributes to force a class match.")
        self.actions(ActionExecutor(self.journal, state=self.state,
                                    skills=(DESCRIBE, locate.bind(self.locate)),
                                    backend="yolo-world" if self.options.clip_weights else "yolo",
                                    features_extra={
                                        "robot_controls": False, "controller_flush": False,
                                        "verification": "observation_bound_perception",
                                        "stop_scope": "vision_results",
                                    }))
        self.transport.register_handler("request/vision/result", self.result)
        self.transport.register_handler("request/vision/frame", self.result_frame, binary=True)
        self.transport.register_handler("request/vision/location", self.location, binary=True)
        self.discover()
        self.spawn(self._observe())

    async def _close_worker(self):
        await asyncio.to_thread(self.worker.shutdown, wait=True, cancel_futures=True)
        self.frames.clear()

    def retain_frame(self, result_id, info, rgb, observed_at):
        # A camera restart must not leave images from the preceding stream readable.
        self.frames = {key: value for key, value in self.frames.items()
                       if value[0].stream_id == info.stream_id
                       and time.monotonic() - value[2] < 2.0}
        self.frames[result_id] = (info, SimpleNamespace(rgb=rgb), observed_at)
        while len(self.frames) > 2:
            del self.frames[next(iter(self.frames))]

    async def result_frame(self, payload):
        request = ResultRequest.model_validate(payload)
        data = self.frames.get(request.result_id)
        if self.state.health != "ready" or data is None:
            raise AgentError("vision_result_expired")
        info, frame, observed_at = data
        age = time.monotonic() - observed_at
        if age >= 2.0:
            self.frames.pop(request.result_id, None)
            raise AgentError("vision_result_expired")
        return await asyncio.to_thread(pack_frame, info, frame, ["rgb"], age)

    async def result(self, payload):
        request = ResultRequest.model_validate(payload)
        result = self.results.get(request.result_id)
        if result is None:
            raise AgentError("vision_result_expired")
        return result.model_dump()

    async def location(self, payload):
        request = ArtifactRequest.model_validate(payload)
        data, age = self.locations.get(request, self.executor)
        packet = await asyncio.to_thread(pack_location, data, age)
        self.locations.get(request, self.executor)
        return packet

    async def locate(self, state, args, stop, progress):
        owner = artifact_owner(self.executor)
        try:
            await self.registry.refresh([self.options.camera])
            info, arrays, age = await CameraClient(self.connect(self.options.camera)).read()
            observed_at = time.monotonic() - age
            if age > 2:
                raise SkillFailure("camera_stale")
            if stop.is_set():
                return False
            items = await self.loop.run_in_executor(
                self.worker, partial(self.detector.locate, arrays["rgb"], args.target),
            )
            if stop.is_set():
                return False
            if len(items) >= 32:
                raise SkillFailure("too_many_detection_candidates")
            items = items_in_roi(items, info.width, info.height, self.options.table_roi)
            selected = select_object(items, args.target, args.selection)
            data = LocationData(LocationInfo(
                location_id=new_id(), owner=owner, observation=info, target=args.target,
                object=selected, model=self.detector.name, weights_sha256=self.detector.sha256,
                text_weights_sha256=self.detector.text_sha256,
            ), arrays)
            self.locations.put(data.info.location_id, owner, data, nbytes=data.nbytes,
                               observed_at=observed_at)
        except (AgentError, ValueError, TimeoutError) as exc:
            raise SkillFailure(str(exc)[:200]) from exc
        state.completed += 1
        return LocationOutput(location_id=data.info.location_id, observation_id=info.observation_id)

    async def _observe(self):
        previous = None
        while True:
            began = time.monotonic()
            self.locations.expire()
            self.frames = {key: value for key, value in self.frames.items()
                           if began - value[2] < 2.0}
            try:
                await self.registry.refresh([self.options.camera])
                client = CameraClient(self.connect(self.options.camera))
                info, arrays, age = await client.read(components=("rgb",), max_age_s=1.0)
                if info.observation_id != previous:
                    observed_at = time.monotonic() - age
                    items = await self.loop.run_in_executor(
                        self.worker, partial(self.detector.detect, arrays["rgb"]),
                    )
                    items = items_in_roi(items, info.width, info.height, self.options.table_roi)
                    result = Objects2D(
                        result_id=new_id(), observation_id=info.observation_id,
                        source_stream_id=info.stream_id, captured_at_ns=info.captured_at_ns,
                        source=info.source,
                        width=info.width, height=info.height, model=self.detector.name,
                        weights_sha256=self.detector.sha256,
                        scope="table_region" if self.options.table_roi else "image", items=items,
                    )
                    self.state.latest = result
                    self.state.observed_at = observed_at
                    self.state.health = "ready"
                    self.results[result.result_id] = result
                    self.retain_frame(result.result_id, info, arrays["rgb"], observed_at)
                    while len(self.results) > 8:
                        del self.results[next(iter(self.results))]
                    self.transport.publish("stream/vision/objects", result.model_dump())
                    previous = info.observation_id
            except (AgentError, TimeoutError, ValueError):
                self.frames.clear()
                self.state.latest = None
                self.state.health = "camera_or_detection_unavailable"
            await asyncio.sleep(max(0.01, 1 / self.options.max_hz - (time.monotonic() - began)))
