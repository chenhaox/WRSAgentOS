"""Detector-independent 2D results; an instance ID is local to one result."""

from typing import Literal

from pydantic import Field, model_validator

from wrs_agent.schemas import Boundary, Name


class Object2D(Boundary):
    instance_id: Name
    label: str = Field(min_length=1, max_length=64)
    score: float = Field(ge=0, le=1)
    box: list[float] = Field(min_length=4, max_length=4)
    label_source: Literal["model_class", "prompt"] = "model_class"
    mask_ref: Name | None = None

    @model_validator(mode="after")
    def ordered_box(self):
        x1, y1, x2, y2 = self.box
        if not 0 <= x1 < x2 or not 0 <= y1 < y2:
            raise ValueError("invalid_detection_box")
        return self


class Objects2D(Boundary):
    source: Literal["synthetic", "realsense", "replay"]
    result_id: Name
    observation_id: Name
    source_stream_id: Name
    captured_at_ns: int = Field(gt=0)
    width: int = Field(gt=0, le=1920)
    height: int = Field(gt=0, le=1080)
    model: str = Field(min_length=1, max_length=100)
    weights_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["image", "table_region"] = "image"
    score_kind: Literal["detector_confidence"] = "detector_confidence"
    items: list[Object2D] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def boxes_inside_image(self):
        if any(item.box[2] > self.width or item.box[3] > self.height for item in self.items):
            raise ValueError("box_outside_image")
        return self
