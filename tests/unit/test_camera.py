import json
import struct
import time

import numpy as np
import pytest

from wrs_agent.drivers.cameras.synthetic import SyntheticCamera
from wrs_agent.errors import AgentError
from wrs_agent.nodes.camera.frames import (
    FrameCache,
    FrameRequest,
    Observation,
    pack_frame,
    unpack_frame,
)
from wrs_agent.nodes.camera.node import CameraOptions
from wrs_agent.nodes.camera.preview import make_preview


@pytest.fixture
def sample():
    camera = SyntheticCamera(width=32, height=24)
    camera.open()
    frame = camera.read()
    info = Observation(
        observation_id="session.1", stream_id="session", sequence=1,
        captured_at_ns=frame.captured_at_ns, source=frame.source, frame_id=frame.frame_id,
        width=32, height=24, calibration=frame.calibration,
    )
    return info, frame


def test_complete_frame_matches_point_cloud_and_owns_read_only_data(sample):
    _, frame = sample
    np.testing.assert_allclose(frame.points[:, :, 2], frame.depth * 0.001)
    assert np.shares_memory(frame.point_colors, frame.rgb)
    for array in (frame.rgb, frame.depth, frame.points):
        assert array.flags.owndata and not array.flags.writeable


@pytest.mark.parametrize("components", [["rgb"], ["points", "depth"], ["rgb", "depth", "points"]])
def test_binary_packet_pairs_selected_components(sample, components):
    info, frame = sample
    restored, arrays, age = unpack_frame(pack_frame(info, frame, components, 0.25))
    assert age == 0.25
    assert restored == info and list(arrays) == components
    for name, array in arrays.items():
        np.testing.assert_array_equal(array, getattr(frame, name))
        assert not array.flags.writeable


def test_packet_rejects_truncation_trailing_bytes_and_oversized_dimensions(sample):
    info, frame = sample
    packet = pack_frame(info, frame, ["rgb"])
    for data in (b"", b"\xff" * 4, packet[:-1], packet + b"x"):
        with pytest.raises(ValueError):
            unpack_frame(data)
    size, = struct.unpack_from("<I", packet)
    header = json.loads(packet[4:4 + size])
    header["observation"]["width"] = 999999
    body = json.dumps(header).encode()
    with pytest.raises(ValueError):
        unpack_frame(struct.pack("<I", len(body)) + body)


def test_cache_bound_by_bytes_and_count_and_never_substitutes_another_frame(sample):
    info, frame = sample
    cache = FrameCache(capacity=2, max_bytes=frame.nbytes * 2)
    for i in range(1, 100):
        cache.put(info.model_copy(update={"observation_id": f"session.{i}", "sequence": i}),
                  frame, time.monotonic())
    assert len(cache.frames) == 2 and cache.nbytes == frame.nbytes * 2
    assert cache.get(FrameRequest())[0].sequence == 99
    with pytest.raises(AgentError, match="observation_expired"):
        cache.get(FrameRequest(observation_id="session.1"))
    with pytest.raises(AgentError, match="camera_frame_too_large"):
        FrameCache(max_bytes=1).put(info, frame, time.monotonic())


def test_stale_and_expired_frames_are_distinct(sample):
    info, frame = sample
    cache = FrameCache(ttl=10)
    cache.put(info, frame, time.monotonic() - 3)
    with pytest.raises(AgentError, match="camera_stale"):
        cache.get(FrameRequest(max_age_s=1.0))
    cache.ttl = 1
    with pytest.raises(AgentError, match="observation_expired"):
        cache.get(FrameRequest(observation_id=info.observation_id))
    assert cache.nbytes == 0


def test_hardware_requires_opt_in_but_allows_default_device():
    for args in ({}, {"serial": "123"}):
        with pytest.raises(ValueError, match="realsense_requires"):
            CameraOptions(backend="realsense", **args)
    assert CameraOptions(backend="realsense", serial="123", allow_hardware=True)
    assert CameraOptions(backend="realsense", allow_hardware=True).serial == ""


def test_preview_is_bounded_and_keeps_source_and_coordinate_frame(sample):
    info, frame = sample
    result = make_preview(info, {name: getattr(frame, name) for name in ("rgb", "depth", "points")})
    assert result["source"] == "synthetic" and result["frame_id"] == info.frame_id
    assert result["observation_id"] == info.observation_id
    assert len(result["points"]) <= 2048
    assert result["rgb"].startswith("data:image/jpeg;base64,")
    assert result["depth"].startswith("data:image/png;base64,")
    json.dumps(result, allow_nan=False)


def test_viewer_closes_preview_when_page_binding_fails(monkeypatch):
    from wrs_agent.env import viewer
    from wrs_agent.nodes.camera import preview

    closed = []

    class Preview:
        async def start(self):
            self.worker = object()

        async def close(self):
            closed.append(True)

        def http_response(self, *_):
            raise AssertionError("no HTTP request expected")

    async def occupied(*args, **kwargs):
        raise OSError("port_occupied")

    monkeypatch.setattr(preview, "CameraPreview", Preview)
    monkeypatch.setattr(viewer, "serve_viewer_hub", occupied)
    with pytest.raises(OSError, match="port_occupied"), viewer.viewer_hub(8000, camera={}):
        raise AssertionError("binding failure must prevent entering the viewer")
    assert closed == [True]
