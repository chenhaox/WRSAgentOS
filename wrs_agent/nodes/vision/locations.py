"""A task's detected object plus its retained RGB, depth and native XYZ."""

import struct
import time
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
from pydantic import Field, model_validator

from wrs_agent.nodes.camera.frames import Observation, pack_frame, unpack_frame
from wrs_agent.nodes.vision.artifacts import ArtifactOwner, ArtifactRequest
from wrs_agent.nodes.vision.results import Object2D
from wrs_agent.nodes.vision.selection import select_mask
from wrs_agent.schemas import Boundary, Name


class LocationInfo(Boundary):
    location_id: Name
    owner: ArtifactOwner
    observation: Observation
    target: str = Field(min_length=1, max_length=64)
    object: Object2D
    model: str = Field(min_length=1, max_length=100)
    weights_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    text_weights_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def check_object(self):
        if (self.object.box[2] > self.observation.width
                or self.object.box[3] > self.observation.height
                or self.object.label.casefold() != self.target.strip().casefold()):
            raise ValueError("location_object_mismatch")
        if (self.object.label_source == "prompt") != (self.text_weights_sha256 is not None):
            raise ValueError("location_text_provenance_mismatch")
        return self


@dataclass(frozen=True)
class LocationData:
    info: LocationInfo
    arrays: dict

    def __post_init__(self):
        if set(self.arrays) != {"rgb", "depth", "points"}:
            raise ValueError("location_requires_complete_observation")

    @property
    def nbytes(self):
        return 16 * 1024 + sum(array.nbytes for array in self.arrays.values())


def select_object(items, target, selection):
    matches = [item for item in items if item.label.casefold() == target.strip().casefold()]
    boxes = np.asarray([item.box for item in matches]).reshape(-1, 4)
    scores = np.asarray([item.score for item in matches])
    return matches[select_mask(boxes, scores, selection)]


def pack_location(data, age_s):
    header = data.info.model_dump_json().encode()
    frame = pack_frame(data.info.observation, SimpleNamespace(**data.arrays),
                       ["rgb", "depth", "points"], age_s=age_s)
    return struct.pack("<I", len(header)) + header + frame


def unpack_location(payload):
    if not 4 < len(payload) <= 36 * 1024 * 1024:
        raise ValueError("invalid_location_packet")
    size, = struct.unpack_from("<I", payload)
    if not 0 < size <= 8192 or 4 + size >= len(payload):
        raise ValueError("invalid_location_header")
    info = LocationInfo.model_validate_json(payload[4:4 + size])
    observation, arrays, age = unpack_frame(memoryview(payload)[4 + size:])
    if observation != info.observation:
        raise ValueError("location_observation_mismatch")
    return LocationData(info, arrays), age


async def read_location(transport, location_id, task_id):
    began = time.monotonic()
    request = ArtifactRequest(artifact_id=location_id, task_id=task_id)
    packet = await transport.request_bytes("request/vision/location", request.model_dump())
    data, age = unpack_location(packet)
    if data.info.location_id != location_id or data.info.owner.task_id != task_id:
        raise ValueError("unexpected_location_reply")
    return data, age + time.monotonic() - began
