"""One selected mask and its complete camera observation, transported together."""

import struct
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from wrs_agent.nodes.camera.frames import Observation, pack_frame, unpack_frame
from wrs_agent.nodes.vision.artifacts import ArtifactOwner, ArtifactRequest
from wrs_agent.nodes.vision.locations import LocationInfo
from wrs_agent.schemas import Boundary, Name


class SegmentInfo(Boundary):
    segment_id: Name
    owner: ArtifactOwner
    observation: Observation
    target: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=80)
    weights_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    text_weights_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    score: float = Field(ge=0, le=1)
    score_kind: Literal["sam3_presence_class", "clip_cosine", "box_iou", "fixture"] = (
        "sam3_presence_class"
    )
    location: LocationInfo | None = None
    pixel_count: int = Field(gt=0, le=1920 * 1080)

    @model_validator(mode="after")
    def check_text_provenance(self):
        if (self.score_kind == "clip_cosine") != (self.text_weights_sha256 is not None):
            raise ValueError("text_weights_and_score_kind_mismatch")
        if self.score_kind == "box_iou" and self.location is None:
            raise ValueError("box_mask_requires_location")
        if self.location is not None and (
            self.location.observation != self.observation
            or self.location.owner.task_id != self.owner.task_id
            or self.location.target != self.target
            or self.score_kind not in {"box_iou", "fixture"}
        ):
            raise ValueError("segment_location_mismatch")
        return self


@dataclass(frozen=True)
class SegmentData:
    info: SegmentInfo
    arrays: dict
    mask: np.ndarray

    def __post_init__(self):
        obs = self.info.observation
        if (self.mask.dtype != np.bool_ or self.mask.shape != (obs.height, obs.width)
                or set(self.arrays) != {"rgb", "depth", "points"}
                or np.count_nonzero(self.mask) != self.info.pixel_count):
            raise ValueError("invalid_segment_data")
        self.mask.setflags(write=False)

    @property
    def nbytes(self):
        # Received arrays can retain a packet header and packed mask through their base buffer.
        return (16 * 1024 + self.mask.nbytes + (self.mask.size + 7) // 8
                + sum(array.nbytes for array in self.arrays.values()))


def pack_segment(data, age_s):
    header = data.info.model_dump_json().encode()
    frame = pack_frame(data.info.observation, SimpleNamespace(**data.arrays),
                       ["rgb", "depth", "points"], age_s=age_s)
    mask = np.packbits(data.mask, bitorder="little").tobytes()
    return b"".join((struct.pack("<II", len(header), len(frame)), header, frame, mask))


def unpack_segment(payload):
    if len(payload) < 8:
        raise ValueError("invalid_segment_packet")
    header_size, frame_size = struct.unpack_from("<II", payload)
    if not 0 < header_size <= 8192 or not 0 < frame_size <= 32 * 1024 * 1024:
        raise ValueError("invalid_segment_header")
    offset = 8 + header_size
    if offset + frame_size > len(payload):
        raise ValueError("truncated_segment_packet")
    info = SegmentInfo.model_validate_json(payload[8:offset])
    observation, arrays, age_s = unpack_frame(memoryview(payload)[offset:offset + frame_size])
    if observation != info.observation:
        raise ValueError("segment_observation_mismatch")
    count = observation.height * observation.width
    packed = payload[offset + frame_size:]
    if len(packed) != (count + 7) // 8:
        raise ValueError("invalid_segment_mask_size")
    mask = np.unpackbits(np.frombuffer(packed, dtype="u1"), count=count,
                         bitorder="little").astype(bool).reshape(observation.height,
                                                               observation.width)
    return SegmentData(info, arrays, mask), age_s


async def read_segment(transport, segment_id, task_id):
    began = time.monotonic()
    request = ArtifactRequest(artifact_id=segment_id, task_id=task_id)
    packet = await transport.request_bytes("request/segmentation/data", request.model_dump())
    data, age = unpack_segment(packet)
    if data.info.segment_id != segment_id or data.info.owner.task_id != task_id:
        raise ValueError("unexpected_segment_reply")
    return data, age + time.monotonic() - began
