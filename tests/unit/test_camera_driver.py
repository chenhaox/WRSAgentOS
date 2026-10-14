import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from wrs_agent.drivers.cameras.realsense import RealSenseCamera
from wrs_agent.nodes.camera.node import CameraNode


@pytest.fixture
def sdk(monkeypatch):
    devices = ["default-camera", "other-camera"]
    calls = []

    def profile(serial):
        return SimpleNamespace(get_device=lambda: SimpleNamespace(
            get_info=lambda key: serial,
            first_depth_sensor=lambda: SimpleNamespace(get_depth_scale=lambda: 0.001),
        ))

    class Config:
        def __init__(self):
            self.serial = None

        def enable_device(self, serial):
            assert serial
            self.serial = serial
            calls.append(("select", serial))

        def enable_stream(self, *args):
            pass

        def resolve(self, pipeline):
            calls.append(("resolve", self.serial))
            return profile(self.serial or devices[0])

    class Pipeline:
        def start(self, config):
            calls.append(("start", config.serial))
            if config.serial not in devices:
                raise RuntimeError("device_unavailable")
            return profile(config.serial)

        def stop(self):
            calls.append(("stop", None))

    monkeypatch.setitem(sys.modules, "pyrealsense2", SimpleNamespace(
        config=Config, pipeline=Pipeline, pipeline_wrapper=lambda pipeline: pipeline,
        stream=SimpleNamespace(depth="depth", color="color"),
        format=SimpleNamespace(z16="z16", rgb8="rgb8"),
        camera_info=SimpleNamespace(serial_number="serial_number"),
        align=lambda stream: calls.append(("align", stream)), pointcloud=object,
    ))
    return devices, calls


def test_default_device_is_resolved_without_streaming_and_pinned_across_reconnects(sdk):
    devices, calls = sdk
    camera = RealSenseCamera()
    assert camera.select_serial() == "default-camera"
    assert calls == [("resolve", None)]  # No stream or empty enable_device call.
    camera.open()
    camera.close()
    camera.open()
    camera.close()
    assert calls.count(("resolve", None)) == 1
    assert calls.count(("start", "default-camera")) == 2
    devices.remove("default-camera")
    with pytest.raises(RuntimeError, match="device_unavailable"):
        camera.open()
    assert camera.serial == "default-camera" and camera.pipeline is None
    assert calls[-1] == ("stop", None)
    assert ("start", "other-camera") not in calls


def test_explicit_serial_never_selects_default(sdk):
    _, calls = sdk
    camera = RealSenseCamera("other-camera")
    camera.open()
    assert camera.serial == "other-camera"
    assert calls == [("select", "other-camera"), ("start", "other-camera"), ("align", "color")]
    camera.close()


def test_camera_worker_keeps_selected_device_lock_across_reconnects(monkeypatch):
    events = []
    stopped = False

    def select():
        events.append("select")
        return "actual-device"

    def lock(name):
        events.append(name)
        return SimpleNamespace(close=lambda: events.append("unlock"))

    def read():
        nonlocal stopped
        stopped = events.count("open") == 2
        raise RuntimeError("disconnected")

    driver = SimpleNamespace(select_serial=select, open=lambda: events.append("open"),
                             read=read, close=lambda: events.append("close"))
    node = SimpleNamespace(options=SimpleNamespace(backend="realsense"),
                           _stop=SimpleNamespace(is_set=lambda: stopped, wait=lambda _: None),
                           _driver=Mock(return_value=driver), _offer=Mock())
    monkeypatch.setattr("wrs_agent.nodes.camera.node.InstanceLock", lock)
    CameraNode._capture(node)
    node._driver.assert_called_once_with()
    assert events == ["select", "realsense-actual-device", "open", "close", "open", "close",
                      "unlock"]


def test_camera_worker_cannot_stream_if_selected_device_is_owned(monkeypatch):
    stopped = False

    def lock(name):
        nonlocal stopped
        stopped = True
        raise RuntimeError("node_already_running")

    driver = SimpleNamespace(select_serial=lambda: "owned-device", open=Mock(), close=Mock())
    node = SimpleNamespace(options=SimpleNamespace(backend="realsense"),
                           _stop=SimpleNamespace(is_set=lambda: stopped, wait=lambda _: None),
                           _driver=lambda: driver, _offer=Mock())
    monkeypatch.setattr("wrs_agent.nodes.camera.node.InstanceLock", lock)
    CameraNode._capture(node)
    driver.open.assert_not_called()
    driver.close.assert_called_once_with()
    assert node._offer.call_args.args[0][-1] == "camera_disconnected"


def test_realsense_reads_one_frameset_and_copies_sdk_owned_buffers():
    rgb = np.full((4, 8, 3), [10, 20, 30], dtype=np.uint8)
    depth = np.full((4, 8), 123, dtype=np.uint16)
    points = np.full((4, 8, 3), [0., 0., 0.123], dtype=np.float32)
    intrinsics = SimpleNamespace(height=4, width=8, fx=5., fy=5., ppx=4., ppy=2.,
                                 model="none", coeffs=[0.] * 5)
    video = SimpleNamespace(intrinsics=intrinsics)
    profile = SimpleNamespace(as_video_stream_profile=lambda: video)
    dframe = SimpleNamespace(profile=profile, get_data=lambda: depth)
    cframe = SimpleNamespace(get_data=lambda: rgb)
    frames = SimpleNamespace(get_depth_frame=lambda: dframe, get_color_frame=lambda: cframe)
    calls = []

    def wait(timeout):
        calls.append(timeout)
        return frames

    camera = RealSenseCamera("fixture")
    camera.pipeline = SimpleNamespace(wait_for_frames=wait, stop=lambda: calls.append("stop"))
    camera.align = SimpleNamespace(process=lambda value: value)
    camera.cloud = SimpleNamespace(calculate=lambda value:
                                   SimpleNamespace(get_vertices=lambda: points))
    camera.scale = 0.001
    frame = camera.read()
    rgb[:] = 0
    depth[:] = 0
    points[:] = 0
    assert calls == [1000]
    assert frame.rgb[0, 0].tolist() == [10, 20, 30]
    assert frame.depth[0, 0] == 123 and frame.points[0, 0, 2] == pytest.approx(0.123)
    assert frame.calibration["depth_scale_m"] == 0.001
    assert frame.frame_id == "realsense-fixture-color-optical"
    assert frame.alignment == "color_grid"
    camera.close()
    camera.close()
    assert calls == [1000, "stop"]
    with pytest.raises(RuntimeError, match="camera_not_open"):
        camera.read()
