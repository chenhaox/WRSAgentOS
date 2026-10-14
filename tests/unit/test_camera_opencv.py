"""Real pixel conversion and drawing, with offline frame input and no GUI windows."""

import asyncio
import runpy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest


@pytest.fixture
def preview(monkeypatch):
    main = runpy.run_path("examples/camera/06_read_frame.py")["main"]
    cv2 = main.__globals__["cv2"]
    state = SimpleNamespace(index=-1, moments=[], results=[], frames=[], labels=[], closed=False)

    def receive():
        state.index += 1
        return object() if state.results[state.index] is not None else None

    async def read(**kwargs):
        assert kwargs == {"components": ("rgb",), "max_age_s": 1.0}
        result = state.results[state.index]
        if isinstance(result, BaseException):
            raise result
        info = SimpleNamespace(observation_id=result, stream_id="stream", source="synthetic")
        return info, {"rgb": np.full((64, 96, 3), [10, 20, 30], dtype=np.uint8)}, 0.0

    transport = SimpleNamespace(subscribe=Mock(return_value=SimpleNamespace(try_recv=receive)))
    system = SimpleNamespace(registry=SimpleNamespace(
        wait_for=AsyncMock(), transport=Mock(return_value=transport),
    ))

    @asynccontextmanager
    async def connect(*args, **kwargs):
        try:
            yield system
        finally:
            state.closed = True

    def put_text(image, label, *args):
        state.labels.append(label)
        return original_put_text(image, label, *args)

    original_put_text = cv2.putText
    monkeypatch.setattr(cv2, "putText", put_text)
    monkeypatch.setattr(cv2, "namedWindow", Mock())
    monkeypatch.setattr(cv2, "imshow", lambda name, image: state.frames.append(image.copy()))
    monkeypatch.setattr(cv2, "waitKey", lambda _: ord("q") if
                        state.index == len(state.moments) - 1 else -1)
    monkeypatch.setattr(cv2, "getWindowProperty", Mock(return_value=1))
    monkeypatch.setattr(cv2, "destroyAllWindows", Mock())
    values = {
        "time": SimpleNamespace(monotonic=lambda: state.moments[max(0, state.index)]),
        "asyncio": SimpleNamespace(sleep=AsyncMock(), create_task=asyncio.create_task,
                                   CancelledError=asyncio.CancelledError),
        "System": SimpleNamespace(connect=connect),
        "CameraClient": lambda _: SimpleNamespace(read=read),
        "use_local_token": Mock(),
    }
    for name, value in values.items():
        monkeypatch.setitem(main.__globals__, name, value)
    state.yolo = {"result": None, "reason": "Waiting for 02_start_yolo", "received": 0.0,
                  "frame": np.full((64, 96, 3), [50, 60, 70], dtype=np.uint8)}

    def read_yolo(system, view):
        view.update(state.yolo)
        return asyncio.Event().wait()

    monkeypatch.setitem(main.__globals__, "read_yolo", read_yolo)
    state.main, state.cv2 = main, cv2
    return state


async def test_preview_counts_unique_frames_converts_rgb_and_clears_stale_image(preview):
    preview.moments = [0.0, 0.1, 0.2, 1.0, 1.1, 2.3]
    preview.results = ["a", "a", "b", "b", None, None]
    await preview.main()
    assert preview.frames[0][0, 0].tolist() == [30, 20, 10]
    fps = [label for label in preview.labels if label.startswith("FPS:")]
    assert fps[3] == "FPS: 2.0 | synthetic"  # Four reads, only two unique frames.
    assert preview.labels[-1] == "FPS: 0.0 | Waiting for fresh frames"
    assert preview.frames[-1][0, 0].tolist() == [0, 0, 0]
    assert preview.closed
    preview.cv2.destroyAllWindows.assert_called_once_with()


async def test_preview_clears_previous_image_immediately_after_query_failure(preview):
    preview.moments = [0.0, 0.1]
    preview.results = ["a", TimeoutError()]
    await preview.main()
    assert preview.labels[-1] == "FPS: 0.0 | Waiting for fresh frames"
    assert preview.frames[-1][0, 0].tolist() == [0, 0, 0]
    assert preview.closed


@pytest.mark.parametrize("window_error", [False, True])
async def test_preview_window_close_exits_without_waiting_for_another_frame(preview, window_error):
    preview.moments = [0.0, 1.0]
    preview.results = [None, None]
    if window_error:
        preview.cv2.getWindowProperty.side_effect = preview.cv2.error("window closed")
    else:
        preview.cv2.getWindowProperty.return_value = 0
    await preview.main()
    assert len(preview.frames) == 1 and preview.closed
    preview.cv2.destroyAllWindows.assert_called_once_with()


async def test_preview_cancellation_closes_window_and_connection(preview):
    preview.moments = [0.0]
    preview.results = [asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await preview.main()
    assert preview.closed
    preview.cv2.destroyAllWindows.assert_called_once_with()


def detection(**changes):
    from wrs_agent.nodes.vision.results import Objects2D

    data = dict(source="synthetic", result_id="detection", observation_id="a",
                source_stream_id="stream", captured_at_ns=1, width=96, height=64,
                model="fixture", weights_sha256="a" * 64,
                items=[{"instance_id": "cup.1", "label": "cup", "score": 0.9,
                        "box": [5.0, 40.0, 30.0, 60.0]}])
    return Objects2D(**(data | changes))


@pytest.mark.parametrize("observation", ["a", "previous"])
async def test_preview_draws_yolo_on_its_own_frame_even_when_live_rgb_has_advanced(
        preview, observation):
    preview.moments, preview.results = [0.0], ["a"]
    preview.yolo["result"] = detection(observation_id=observation)
    await preview.main()
    assert "cup  90%" in preview.labels
    assert f"Observation: {observation}" in preview.labels
    # Live RGB has no detection marks; yellow rectangle is on the retained inference RGB.
    assert preview.frames[0][40, 5].tolist() == [30, 20, 10]
    assert preview.frames[0][40, 96 + 5].tolist() == [0, 255, 255]
    assert preview.frames[0][60, 96 + 80].tolist() == [50, 60, 70]


@pytest.mark.parametrize("changes,moments,reason", [
    ({}, [2.1], "YOLO stale / waiting for new result"),
    ({"source_stream_id": "old-stream"}, [0.0], "YOLO belongs to another camera stream"),
])
async def test_preview_hides_stale_and_previous_camera_session_results(
        preview, changes, moments, reason):
    preview.moments, preview.results = moments, ["a"]
    preview.yolo["result"] = detection(**changes)
    await preview.main()
    assert reason in preview.labels and "cup  90%" not in preview.labels


async def test_preview_empty_detection_differs_from_missing_yolo(preview):
    preview.moments, preview.results = [0.0], ["a"]
    preview.yolo["result"] = detection(items=[])
    await preview.main()
    assert "No objects detected" in preview.labels


async def test_yolo_reader_queries_new_ids_only_and_clears_unavailable(monkeypatch):
    from wrs_agent.errors import AgentError

    reader = runpy.run_path("examples/camera/06_read_frame.py")["read_yolo"]
    result = detection()
    ready = SimpleNamespace(data=SimpleNamespace(
        health="ready", result_id=result.result_id, observation_id=result.observation_id,
    ))
    request = AsyncMock(return_value=result.model_dump())
    request_bytes = AsyncMock(return_value=frame_packet(result))
    system = SimpleNamespace(snapshot=AsyncMock(side_effect=[
        ready, ready, AgentError("node_unavailable"), ready,
    ]), registry=SimpleNamespace(transport=Mock(return_value=SimpleNamespace(
        request=request, request_bytes=request_bytes,
    ))))
    view, states = {}, []

    async def sleep(delay):
        states.append(dict(view))
        if len(states) == 4:
            raise asyncio.CancelledError

    monkeypatch.setitem(reader.__globals__, "asyncio", SimpleNamespace(sleep=sleep))
    with pytest.raises(asyncio.CancelledError):
        await reader(system, view)
    assert request.await_count == 1
    assert request_bytes.await_count == 1
    assert states[0]["result"] == result and states[1] == states[0]
    assert states[2]["result"] is None and "unavailable" in states[2]["reason"]
    # A reconnect with the same result must not reset freshness.
    assert states[3] == states[0]


@pytest.mark.parametrize("payload", [{"items": []}, detection(result_id="wrong").model_dump(),
                                    detection(observation_id="wrong").model_dump()])
async def test_yolo_reader_rejects_invalid_or_mismatched_results(monkeypatch, payload):
    reader = runpy.run_path("examples/camera/06_read_frame.py")["read_yolo"]
    system = SimpleNamespace(snapshot=AsyncMock(return_value=SimpleNamespace(data=SimpleNamespace(
        health="ready", result_id="detection", observation_id="a",
    ))), registry=SimpleNamespace(transport=Mock(return_value=SimpleNamespace(
        request=AsyncMock(return_value=payload),
    ))))
    monkeypatch.setitem(reader.__globals__, "asyncio", SimpleNamespace(
        sleep=AsyncMock(side_effect=asyncio.CancelledError),
    ))
    view = {}
    with pytest.raises(asyncio.CancelledError):
        await reader(system, view)
    assert view == {"result": None, "frame": None, "reason": "Invalid YOLO result or frame"}


def frame_packet(result, *, age=0.0):
    from wrs_agent.nodes.camera.frames import Observation, pack_frame

    info = Observation(observation_id=result.observation_id, stream_id=result.source_stream_id,
                       captured_at_ns=result.captured_at_ns, sequence=1, source=result.source,
                       frame_id="camera", width=result.width, height=result.height,
                       calibration=dict(fx=1.0, fy=1.0, cx=0.0, cy=0.0, distortion="none",
                                        coefficients=[0.0] * 5, depth_scale_m=0.001))
    frame = SimpleNamespace(rgb=np.full((result.height, result.width, 3), [10, 20, 30],
                                       dtype=np.uint8))
    return pack_frame(info, frame, ["rgb"], age)


@pytest.mark.parametrize("changes,age", [
    ({"observation_id": "wrong"}, 0.0), ({"source_stream_id": "wrong"}, 0.0),
    ({"width": 120}, 0.0), ({"source": "replay"}, 0.0), ({"captured_at_ns": 2}, 0.0),
    ({}, 2.1),
])
async def test_yolo_reader_rejects_wrong_frame_or_expired_capture(monkeypatch, changes, age):
    reader = runpy.run_path("examples/camera/06_read_frame.py")["read_yolo"]
    result = detection()
    bus = SimpleNamespace(request=AsyncMock(return_value=result.model_dump()),
                          request_bytes=AsyncMock(return_value=frame_packet(
                              detection(**changes), age=age)))
    system = SimpleNamespace(snapshot=AsyncMock(return_value=SimpleNamespace(data=SimpleNamespace(
        health="ready", result_id=result.result_id, observation_id=result.observation_id,
    ))), registry=SimpleNamespace(transport=Mock(return_value=bus)))
    monkeypatch.setitem(reader.__globals__, "asyncio", SimpleNamespace(
        sleep=AsyncMock(side_effect=asyncio.CancelledError),
    ))
    view = {}
    with pytest.raises(asyncio.CancelledError):
        await reader(system, view)
    assert view["result"] is None and view["frame"] is None
