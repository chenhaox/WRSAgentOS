"""Example configuration preserves explicit device selection and local-only model loading."""

import hashlib
import runpy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize("backend", ["synthetic", "realsense"])
async def test_camera_backend_is_the_explicit_hardware_choice(monkeypatch, backend):
    entry = runpy.run_path("examples/camera/01_start_camera.py")
    main = entry["main"]
    serve = AsyncMock()
    for name, value in {"BACKEND": backend, "SERIAL": "",
                        "use_local_token": Mock(), "serve_node": serve}.items():
        monkeypatch.setitem(main.__globals__, name, value)
    await main()
    assert serve.await_args.kwargs["options"] == {
        "backend": backend, "serial": "", "allow_hardware": backend == "realsense",
    }


async def test_yolo_fingerprints_selected_local_file_without_manual_checksum(tmp_path, monkeypatch):
    entry = runpy.run_path("examples/camera/03_start_yolo.py")
    main = entry["main"]
    weights = tmp_path / "detector.pt"
    weights.write_bytes(b"fixture")
    serve = AsyncMock()
    for name, value in {"WEIGHTS": weights,
                        "use_local_token": Mock(), "serve_node": serve}.items():
        monkeypatch.setitem(main.__globals__, name, value)
    await main()
    options = serve.await_args.kwargs["options"]
    assert options["weights"] == weights
    assert options["weights_sha256"] == hashlib.sha256(b"fixture").hexdigest()


@pytest.mark.parametrize("filename,exists", [("missing.pt", False), ("wrong.txt", True)])
async def test_yolo_requires_local_checkpoint_before_connecting(tmp_path, monkeypatch,
                                                             filename, exists):
    entry = runpy.run_path("examples/camera/03_start_yolo.py")
    main = entry["main"]
    weights = tmp_path / filename
    if exists:
        weights.write_bytes(b"fixture")
    credential = Mock()
    serve = AsyncMock()
    for name, value in {"WEIGHTS": weights,
                        "use_local_token": credential, "serve_node": serve}.items():
        monkeypatch.setitem(main.__globals__, name, value)
    with pytest.raises(SystemExit, match="WEIGHTS"):
        await main()
    credential.assert_not_called()
    serve.assert_not_awaited()


@pytest.fixture
def speech_example(monkeypatch):
    main = runpy.run_path("examples/camera/06_speak_objects.py")["main"]
    completed = SimpleNamespace(state="SUCCEEDED", reason="")
    task = SimpleNamespace(wait=AsyncMock(return_value=completed))
    system = SimpleNamespace(
        registry=SimpleNamespace(wait_for=AsyncMock()),
        start=AsyncMock(return_value=task), goal=AsyncMock(),
        snapshot=AsyncMock(return_value=SimpleNamespace(data=SimpleNamespace(last_text="本次文本"))),
    )
    closed = Mock()

    @asynccontextmanager
    async def connect(*args, **kwargs):
        try:
            yield system
        finally:
            closed()

    monkeypatch.setitem(main.__globals__, "System", SimpleNamespace(connect=connect))
    monkeypatch.setitem(main.__globals__, "use_local_token", Mock())
    return main, system, completed, closed


@pytest.mark.parametrize("missing,script", [
    ("agent", "05_start_agent.py"), ("yolo", "03_start_yolo.py"), ("tts", "04_start_tts.py"),
])
async def test_speech_identifies_missing_service_before_submitting_task(speech_example, missing,
                                                                      script):
    main, system, _, closed = speech_example

    async def wait_for(node, **kwargs):
        if node == missing:
            raise TimeoutError

    system.registry.wait_for.side_effect = wait_for
    with pytest.raises(SystemExit, match=script):
        await main()
    system.start.assert_not_awaited()
    system.goal.assert_not_awaited()
    system.snapshot.assert_not_awaited()
    closed.assert_called_once()


async def test_speech_binds_this_description_to_tts_after_agent_ready(speech_example, capsys):
    main, system, _, closed = speech_example
    await main()
    assert [call.args[0] for call in system.registry.wait_for.await_args_list] == [
        "agent", "yolo", "tts",
    ]
    see, say = system.start.await_args.args
    assert see.skill == "describe_objects" and say.skill == "speak"
    assert say.depends_on == [see.step_id]
    assert say.inputs["text"].step_id == see.step_id
    assert say.inputs["text"].field == "text"
    system.goal.assert_not_awaited()
    assert "播报文本： 本次文本" in capsys.readouterr().out
    closed.assert_called_once()


async def test_failed_speech_does_not_display_text_from_previous_task(speech_example):
    main, system, completed, closed = speech_example
    completed.state, completed.reason = "FAILED", "camera_or_detection_unavailable"
    with pytest.raises(SystemExit, match="camera_or_detection_unavailable"):
        await main()
    system.snapshot.assert_not_awaited()
    closed.assert_called_once()
