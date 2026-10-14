"""Exercise the actual SDK alignment with injected pixels, never physical devices."""

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from wrs_agent.drivers.cameras.realsense import RealSenseCamera
from wrs_agent.nodes.camera.frames import FrameCache, FrameRequest, Observation, unpack_frame
from wrs_agent.nodes.camera.node import CameraNode


@pytest.fixture
def color_frame(monkeypatch):
    rs = pytest.importorskip("pyrealsense2")
    device, sync = rs.software_device(), rs.syncer()
    sensors, profiles, pixels = [], [], []
    for uid, (stream, fmt, width, height, bpp) in enumerate((
        (rs.stream.depth, rs.format.z16, 16, 8, 2),
        (rs.stream.color, rs.format.rgb8, 32, 16, 3),
    ), start=1):
        sensor = device.add_sensor(str(stream))
        intr = rs.intrinsics()
        intr.width, intr.height = width, height
        intr.fx, intr.fy, intr.ppx, intr.ppy = width, width, width / 2, height / 2
        intr.model, intr.coeffs = rs.distortion.none, [0.] * 5
        spec = rs.video_stream()
        spec.type, spec.uid, spec.index = stream, uid, 0
        spec.width, spec.height, spec.fps, spec.bpp = width, height, 15, bpp
        spec.fmt, spec.intrinsics = fmt, intr
        profile = sensor.add_video_stream(spec).as_video_stream_profile()
        data = (np.zeros((height, width), dtype=np.uint16) if bpp == 2
                else np.full((height, width, 3), [31, 101, 211], dtype=np.uint8))
        if bpp == 2:
            data[2:6, 4:12] = 300  # Most pixels lack depth, but every RGB pixel is valid.
            sensor.add_read_only_option(rs.option.depth_units, 0.001)
        sensors.append(sensor)
        profiles.append(profile)
        pixels.append(data)
    extrinsics = rs.extrinsics()
    extrinsics.rotation, extrinsics.translation = [1., 0., 0., 0., 1., 0., 0., 0., 1.], [0.] * 3
    profiles[0].register_extrinsics_to(profiles[1], extrinsics)
    device.create_matcher(rs.matchers.default)
    try:
        for sensor, profile in zip(sensors, profiles, strict=True):
            sensor.open(profile)
            sensor.start(sync)
        frames = None
        for number in range(1, 4):
            for sensor, profile, data in zip(sensors, profiles, pixels, strict=True):
                frame = rs.software_video_frame()
                frame.pixels, frame.stride = data, data.strides[0]
                frame.bpp = 2 if data.ndim == 2 else 3
                frame.timestamp, frame.frame_number = number * 1000 / 15, number
                frame.domain = rs.timestamp_domain.hardware_clock
                frame.profile, frame.depth_units = profile, 0.001
                sensor.on_video_frame(frame)
            frames = sync.wait_for_frames(1000)
            if frames.get_depth_frame() and frames.get_color_frame():
                break
        assert frames.get_depth_frame() and frames.get_color_frame()
        # This is exactly what the old alignment did to the otherwise complete RGB.
        old = rs.align(rs.stream.depth).process(frames)
        assert np.any(np.all(np.asanyarray(old.get_color_frame().get_data()) == 0, axis=-1))
        pipeline = SimpleNamespace(
            start=lambda config: SimpleNamespace(get_device=lambda: device),
            wait_for_frames=lambda timeout: frames, stop=lambda: None,
        )
        monkeypatch.setattr(rs, "pipeline", lambda: pipeline)
        camera = RealSenseCamera("software")
        monkeypatch.setattr(camera, "_config", lambda sdk: None)
        camera.open()
        try:
            yield camera.read()
        finally:
            camera.close()
    finally:
        for sensor in sensors:
            sensor.stop()
            sensor.close()


def test_rgb_keeps_pixels_without_depth_and_xyz_uses_color_grid(color_frame):
    frame = color_frame
    assert frame.rgb.shape == frame.points.shape == (16, 32, 3)
    assert frame.depth.shape == (16, 32)
    assert np.all(frame.rgb == [31, 101, 211])
    missing = frame.depth == 0
    assert missing.any() and (~missing).any()
    assert np.all(frame.points[missing] == 0)
    np.testing.assert_allclose(frame.points[..., 2], frame.depth * 0.001)
    v, u = np.mgrid[:16, :32]
    np.testing.assert_allclose(frame.points[..., 0], (u - 16) / 32 * frame.points[..., 2])
    np.testing.assert_allclose(frame.points[..., 1], (v - 8) / 32 * frame.points[..., 2])
    assert frame.calibration["fx"] == frame.calibration["fy"] == 32
    assert frame.frame_id == "realsense-software-color-optical"
    assert frame.alignment == "color_grid"


@pytest.mark.asyncio
async def test_color_grid_survives_node_event_and_binary_query(color_frame):
    node = CameraNode()
    # Supply the worker's mailbox directly: no transport, thread, or camera startup.
    node.cache, node.transport = FrameCache(), SimpleNamespace(publish=Mock())
    node._lock, node._changed = threading.Lock(), asyncio.Event()
    node._pending = ("session", color_frame, time.monotonic(), "")
    node._notified, node.stream_id = True, None
    node._changed.set()
    task = asyncio.create_task(node._pump())
    try:
        await asyncio.sleep(0)
        info = Observation.model_validate(node.transport.publish.call_args.args[1])
        assert info.alignment == "color_grid"
        assert info.frame_id == color_frame.frame_id
        assert (info.width, info.height) == (32, 16)
        received, arrays, _ = unpack_frame(await node.read_frame(FrameRequest().model_dump()))
        assert received == info
        for name in ("rgb", "depth", "points"):
            np.testing.assert_array_equal(arrays[name], getattr(color_frame, name))
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
