"""Explicit fixed-camera extrinsics. This file never supplies an identity fallback."""

import json
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from wrs_agent.schemas import Boundary, Name


class FixedCameraCalibration(Boundary):
    calibration_id: Name
    camera_frame: Name
    robot_frame: Literal["world"] = "world"
    source: Literal["measured", "virtual_fixture"]
    # Row-major homogeneous transform: p_world = T_world_camera @ p_camera.
    world_from_camera: list[float] = Field(min_length=16, max_length=16)
    max_observation_age_s: float = Field(default=5.0, gt=0, le=10)

    @model_validator(mode="after")
    def rigid_transform(self):
        matrix = np.asarray(self.world_from_camera).reshape(4, 4)
        rotation = matrix[:3, :3]
        if (not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-8)
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
                or not np.isclose(np.linalg.det(rotation), 1, atol=1e-5)):
            raise ValueError("invalid_camera_extrinsics")
        return self

    def matrix(self):
        return np.asarray(self.world_from_camera).reshape(4, 4)


def load_calibration(path):
    with Path(path).open("rb") as source:
        payload = source.read(8193)
    if len(payload) > 8192:
        raise ValueError("calibration_file_too_large")
    return FixedCameraCalibration.model_validate(json.loads(payload))


def validate_observation(calibration, observation, age_s, *, hardware=False):
    if observation.frame_id != calibration.camera_frame:
        raise ValueError("camera_calibration_frame_mismatch")
    if age_s > calibration.max_observation_age_s or age_s < 0 or not np.isfinite(age_s):
        raise ValueError("grasp_observation_stale")
    if hardware and (calibration.source != "measured" or observation.source != "realsense"):
        raise ValueError("real_grasp_requires_measured_camera_and_live_observation")
