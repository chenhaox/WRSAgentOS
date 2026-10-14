"""GraspNet frame convention is preserved explicitly until a tool-specific conversion."""

import struct
import time
from dataclasses import dataclass
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from wrs_agent.nodes.segmentation.data import SegmentData, SegmentInfo, pack_segment, unpack_segment
from wrs_agent.nodes.vision.artifacts import ArtifactOwner, ArtifactRequest
from wrs_agent.schemas import Boundary, Name


class GraspCandidate(Boundary):
    # Upstream GraspNet's translation and rotation, not a robot flange/TCP pose.
    translation: list[float] = Field(min_length=3, max_length=3)
    rotation: list[float] = Field(min_length=9, max_length=9)
    width_m: float = Field(gt=0, le=0.2)
    height_m: float = Field(gt=0, le=0.2)
    depth_m: float = Field(gt=0, le=0.2)
    score: float = Field(ge=0)

    @model_validator(mode="after")
    def rotation_matrix(self):
        matrix = np.asarray(self.rotation).reshape(3, 3)
        if (not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-4)
                or not np.isclose(np.linalg.det(matrix), 1, atol=1e-4)):
            raise ValueError("invalid_grasp_rotation")
        return self


class GraspSet(Boundary):
    candidate_set_id: Name
    owner: ArtifactOwner
    segment: SegmentInfo
    model: str = Field(min_length=1, max_length=80)
    weights_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    convention: Literal["graspnet_camera_x_approach_y_closing"] = (
        "graspnet_camera_x_approach_y_closing"
    )
    score_kind: Literal["graspnet_quality"] = "graspnet_quality"
    collision_checked: Literal[False] = False
    candidates: list[GraspCandidate] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def same_task(self):
        if self.owner.task_id != self.segment.owner.task_id:
            raise ValueError("grasp_segment_task_mismatch")
        return self


@dataclass(frozen=True)
class GraspData:
    info: GraspSet
    segment: SegmentData


class LatestGrasps(Boundary):
    """Read-only discovery; the referenced artifact still requires all normal checks."""

    candidate_set_id: Name
    task_id: Name
    observation_id: Name
    age_s: float = Field(ge=0)


def pack_grasps(data, age_s):
    header = data.info.model_dump_json().encode()
    return b"".join((struct.pack("<I", len(header)), header, pack_segment(data.segment, age_s)))


def unpack_grasps(payload):
    if len(payload) < 4:
        raise ValueError("invalid_grasp_packet")
    size, = struct.unpack_from("<I", payload)
    if not 0 < size <= 60 * 1024 or 4 + size > len(payload):
        raise ValueError("invalid_grasp_header")
    info = GraspSet.model_validate_json(payload[4:4 + size])
    segment, age = unpack_segment(payload[4 + size:])
    if segment.info != info.segment:
        raise ValueError("grasp_segment_mismatch")
    return GraspData(info, segment), age


async def read_grasps(transport, candidate_set_id, task_id):
    began = time.monotonic()
    query = ArtifactRequest(artifact_id=candidate_set_id, task_id=task_id)
    packet = await transport.request_bytes("request/grasp/data", query.model_dump())
    data, age = unpack_grasps(packet)
    if data.info.candidate_set_id != candidate_set_id or data.info.owner.task_id != task_id:
        raise ValueError("unexpected_grasp_reply")
    return data, age + time.monotonic() - began
