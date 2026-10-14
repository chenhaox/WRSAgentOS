"""One complete camera sample, independent of transport and task identities."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CameraFrame:
    rgb: np.ndarray
    depth: np.ndarray
    points: np.ndarray
    captured_at_ns: int
    calibration: dict
    frame_id: str
    source: str
    alignment: str = "depth_grid"

    def __post_init__(self):
        height, width = self.depth.shape
        if not 0 < height <= 1080 or not 0 < width <= 1920:
            raise ValueError("camera_resolution_out_of_bounds")
        for array, dtype, shape in (
            (self.rgb, np.dtype("uint8"), (height, width, 3)),
            (self.depth, np.dtype("<u2"), (height, width)),
            (self.points, np.dtype("<f4"), (height, width, 3)),
        ):
            if array.dtype != dtype or array.shape != shape or not array.flags.c_contiguous:
                raise ValueError("invalid_camera_array")
            # Drivers transfer ownership; consumers must explicitly copy to edit.
            array.setflags(write=False)

    @property
    def nbytes(self):
        return self.rgb.nbytes + self.depth.nbytes + self.points.nbytes

    @property
    def point_colors(self):
        """Color and XYZ share the declared pixel grid; no duplicate color buffer."""
        return self.rgb.reshape(-1, 3)
