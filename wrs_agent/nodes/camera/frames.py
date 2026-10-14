"""Camera wire contract and a bounded recent-frame cache. No model or device SDK."""

import struct
import time
from collections import deque
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from wrs_agent.errors import AgentError
from wrs_agent.schemas import Boundary, Name

Component = Literal["rgb", "depth", "points"]
FORMATS = {"rgb": ("u1", 3), "depth": ("<u2", 1), "points": ("<f4", 3)}


class Calibration(Boundary):
    fx: float = Field(gt=0)
    fy: float = Field(gt=0)
    cx: float
    cy: float
    distortion: str = Field(max_length=80)
    coefficients: list[float] = Field(min_length=5, max_length=5)
    depth_scale_m: float = Field(gt=0, le=1)


class Observation(Boundary):
    observation_id: Name
    stream_id: Name
    sequence: int = Field(ge=1)
    captured_at_ns: int = Field(gt=0)
    clock: Literal["host_unix_ns"] = "host_unix_ns"
    source: Literal["synthetic", "realsense", "replay"]
    frame_id: Name
    width: int = Field(gt=0, le=1920)
    height: int = Field(gt=0, le=1080)
    alignment: Literal["depth_grid", "color_grid"] = "depth_grid"
    point_unit: Literal["m"] = "m"
    invalid_points: Literal["z_le_zero"] = "z_le_zero"
    calibration: Calibration


class FrameRequest(Boundary):
    observation_id: Name | None = None
    components: list[Component] = Field(default_factory=lambda: ["rgb", "depth", "points"],
                                        min_length=1, max_length=3)
    max_age_s: float = Field(default=2.0, gt=0, le=30)

    @model_validator(mode="after")
    def unique_components(self):
        if len(set(self.components)) != len(self.components):
            raise ValueError("duplicate_component")
        return self


class FrameHeader(Boundary):
    version: Literal[1] = 1
    observation: Observation
    age_s: float = Field(default=0.0, ge=0)
    components: list[Component] = Field(min_length=1, max_length=3)


def pack_frame(info, frame, components, age_s=0.0):
    header = FrameHeader(observation=info, components=components,
                         age_s=age_s).model_dump_json().encode()
    return b"".join((struct.pack("<I", len(header)), header,
                     *(getattr(frame, name).tobytes() for name in components)))


def unpack_frame(payload):
    """Validate sizes before creating read-only array views into the received bytes."""
    if len(payload) < 4:
        raise ValueError("invalid_camera_packet")
    size, = struct.unpack_from("<I", payload)
    if not 0 < size <= 8192 or 4 + size > len(payload):
        raise ValueError("invalid_camera_header")
    header = FrameHeader.model_validate_json(bytes(payload[4:4 + size]))
    names = header.components
    if len(set(names)) != len(names):
        raise ValueError("duplicate_component")
    info = header.observation
    offset, arrays = 4 + size, {}
    for name in names:
        dtype, channels = FORMATS[name]
        shape = (info.height, info.width) + ((channels,) if channels > 1 else ())
        count = info.height * info.width * channels
        end = offset + count * np.dtype(dtype).itemsize
        if end > len(payload):
            raise ValueError("truncated_camera_packet")
        array = np.frombuffer(payload, dtype=dtype, count=count, offset=offset)
        arrays[name] = array.reshape(shape)
        offset = end
    if offset != len(payload):
        raise ValueError("unexpected_camera_bytes")
    return info, arrays, header.age_s


class FrameCache:
    """Two recent complete frames, bounded by bytes and age; no implicit history retention."""

    def __init__(self, *, capacity=2, max_bytes=64 * 1024 * 1024, ttl=5.0):
        self.capacity, self.max_bytes, self.ttl = capacity, max_bytes, ttl
        self.frames = deque()
        self.nbytes = 0

    def clear(self):
        self.frames.clear()
        self.nbytes = 0

    def _expire(self, now):
        while self.frames and now - self.frames[0][2] > self.ttl:
            self.nbytes -= self.frames.popleft()[1].nbytes

    def put(self, info, frame, received_at):
        self._expire(time.monotonic())
        if frame.nbytes > self.max_bytes:
            raise AgentError("camera_frame_too_large")
        while self.frames and (len(self.frames) >= self.capacity
                               or self.nbytes + frame.nbytes > self.max_bytes):
            self.nbytes -= self.frames.popleft()[1].nbytes
        self.frames.append((info, frame, received_at))
        self.nbytes += frame.nbytes

    def get(self, request):
        now = time.monotonic()
        self._expire(now)
        for info, frame, received in reversed(self.frames):
            if request.observation_id is None or request.observation_id == info.observation_id:
                if now - received > request.max_age_s:
                    raise AgentError("camera_stale")
                return info, frame, now - received
        raise AgentError("observation_expired" if request.observation_id else "camera_no_frame")
