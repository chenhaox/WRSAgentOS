"""RealSense RGB-D + XYZ capture. All methods belong to one acquisition thread.

Capture flow adapted from Chen Hao's realsense_d400s.py in the legacy WRS
checkout (MIT, copyright 2020 Weiwei Wan); see docs/CAMERA.md for provenance.
No WRS import, window, inference, network transport, or automatic device reset.
"""

import time

import numpy as np

from wrs_agent.drivers.cameras.frame import CameraFrame


class RealSenseCamera:
    def __init__(self, serial="", *, width=848, height=480, fps=15):
        self.serial = serial
        self.width, self.height, self.fps = width, height, fps
        self.pipeline = None

    def _config(self, rs):
        config = rs.config()
        if self.serial:
            config.enable_device(self.serial)
        config.enable_stream(rs.stream.depth, self.width, self.height, rs.format.z16, self.fps)
        config.enable_stream(rs.stream.color, self.width, self.height, rs.format.rgb8, self.fps)
        return config

    def select_serial(self):
        """Resolve the default without streaming; pin it for ownership and future reconnects."""
        if not self.serial:
            import pyrealsense2 as rs

            profile = self._config(rs).resolve(rs.pipeline_wrapper(rs.pipeline()))
            self.serial = profile.get_device().get_info(rs.camera_info.serial_number)
            if not self.serial:
                raise RuntimeError("camera_serial_unavailable")
        return self.serial

    def open(self):
        import pyrealsense2 as rs

        if self.pipeline is not None:
            raise RuntimeError("camera_already_open")
        self.select_serial()
        pipeline, config = rs.pipeline(), self._config(rs)
        try:
            profile = pipeline.start(config)
            self.scale = profile.get_device().first_depth_sensor().get_depth_scale()
            # Preserve RGB even where depth is missing; resample depth onto color.
            self.align = rs.align(rs.stream.color)
            self.cloud = rs.pointcloud()
        except BaseException:
            try:
                pipeline.stop()
            except RuntimeError:
                pass  # start may have failed before acquiring the device.
            raise
        self.pipeline = pipeline

    def read(self):
        if self.pipeline is None:
            raise RuntimeError("camera_not_open")
        frames = self.align.process(self.pipeline.wait_for_frames(1000))
        captured = time.time_ns()
        depth, color = frames.get_depth_frame(), frames.get_color_frame()
        if not depth or not color:
            raise RuntimeError("camera_incomplete_frame")
        intr = depth.profile.as_video_stream_profile().intrinsics
        vertices = self.cloud.calculate(depth)
        # Own the memory before SDK frame/points handles leave this function.
        xyz = np.asanyarray(vertices.get_vertices()).view("<f4").reshape(intr.height, intr.width, 3)
        return CameraFrame(
            rgb=np.asanyarray(color.get_data()).copy(),
            depth=np.asanyarray(depth.get_data()).astype("<u2", copy=True),
            points=xyz.copy(),
            captured_at_ns=captured,
            calibration={
                "fx": intr.fx, "fy": intr.fy, "cx": intr.ppx, "cy": intr.ppy,
                "distortion": str(intr.model), "coefficients": list(intr.coeffs),
                "depth_scale_m": self.scale,
            },
            frame_id=f"realsense-{self.serial}-color-optical",
            source="realsense",
            alignment="color_grid",
        )

    def close(self):
        pipeline, self.pipeline = self.pipeline, None
        if pipeline is not None:
            pipeline.stop()
