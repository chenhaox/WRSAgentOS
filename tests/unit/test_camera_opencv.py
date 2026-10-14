"""Real pixel conversion and drawing, with offline frame input and no GUI windows."""

import runpy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest


@pytest.fixture
def preview(monkeypatch):
    main = runpy.run_path("examples/camera/02_read_frame.py")["main"]
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
        info = SimpleNamespace(observation_id=result, source="synthetic")
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
        "asyncio": SimpleNamespace(sleep=AsyncMock()),
        "System": SimpleNamespace(connect=connect),
        "CameraClient": lambda _: SimpleNamespace(read=read),
        "use_local_token": Mock(),
    }
    for name, value in values.items():
        monkeypatch.setitem(main.__globals__, name, value)
    state.main, state.cv2 = main, cv2
    return state


async def test_preview_counts_unique_frames_converts_rgb_and_clears_stale_image(preview):
    preview.moments = [0.0, 0.1, 0.2, 1.0, 1.1, 2.3]
    preview.results = ["a", "a", "b", "b", None, None]
    await preview.main()
    assert preview.frames[0][0, 0].tolist() == [30, 20, 10]
    assert preview.labels[3] == "FPS: 2.0 | synthetic"  # Four reads, only two unique frames.
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
    import asyncio

    preview.moments = [0.0]
    preview.results = [asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await preview.main()
    assert preview.closed
    preview.cv2.destroyAllWindows.assert_called_once_with()
