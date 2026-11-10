import asyncio
import hashlib
import json
import runpy
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import action, control, eventually

from wrs_agent.executor import ExecutionUnknown
from wrs_agent.nodes import model_assets as assets
from wrs_agent.nodes.tts.backend import SpeechBackend, make_speech_executor
from wrs_agent.nodes.tts.qwen import STOP_CHECK_STEPS, QwenTTS
from wrs_agent.schemas import ActionState


async def test_cancel_during_synthesis_does_not_play_late_audio(tmp_path):
    entered, release = threading.Event(), threading.Event()
    played = []

    def render(text, stopped):
        entered.set()
        assert release.wait(3)
        return [1.0], 24000  # A provider may complete after cancellation.

    backend = SpeechBackend(render, lambda *args: played.append(args))
    env = make_speech_executor(tmp_path / "tts.db", backend)
    request = action(env, "speak", {"text": "正在上移。"})
    try:
        assert (await env.submit(request)).accepted
        await eventually(entered.is_set, bool)
        receipt = await asyncio.wait_for(env.cancel(control(env)), 0.2)
        assert receipt.accepted and receipt.phase == "STOPPING"
        assert env.status(request.action_id).state == ActionState.CANCELLING
        assert not env.stop_confirmed
        release.set()
        await env.runner
        assert env.status(request.action_id).state == ActionState.CANCELLED
        assert env.stop_confirmed and not played and env.world.completed == 0
    finally:
        release.set()
        await env.close()


async def test_cancel_playback_waits_for_output_confirmation(tmp_path):
    entered, abort = threading.Event(), threading.Event()

    def play(audio, rate, stopped):
        entered.set()
        assert stopped.wait(3)
        assert abort.wait(3)
        return False

    env = make_speech_executor(tmp_path / "tts.db", SpeechBackend(lambda *a: ([1], 24000), play))
    request = action(env, "speak", {"text": "你好"})
    try:
        await env.submit(request)
        await eventually(entered.is_set, bool)
        stop = control(env)
        first = await env.cancel(stop)
        assert await env.cancel(stop) == first
        assert not env.stop_confirmed
        abort.set()
        await env.runner
        assert env.stop_confirmed and env.world.completed == 0
        assert env.status(request.action_id).state == ActionState.CANCELLED
    finally:
        abort.set()
        await env.close()


async def test_unknown_output_keeps_admission_closed(tmp_path):
    def play(*args):
        raise ExecutionUnknown("device_disappeared")

    env = make_speech_executor(tmp_path / "tts.db", SpeechBackend(lambda *a: ([1], 24000), play))
    try:
        request = action(env, "speak", {"text": "你好"})
        await env.submit(request)
        await env.runner
        assert env.status(request.action_id).state == ActionState.UNKNOWN
        assert env.admission == "UNKNOWN" and not env.stop_confirmed
        assert not (await env.submit(action(env, "speak", {"text": "下一句"}))).accepted
    finally:
        await env.close()


async def test_prepared_speech_skips_inference_and_preserves_idempotence(tmp_path):
    rendered, played = [], []

    def render(text, stopped):
        rendered.append(text)
        return [0.5], 24000

    def play(*args):
        played.append(args)
        return True

    backend = SpeechBackend(render, play)
    await asyncio.to_thread(backend.prepare, ["正在上移。"])
    env = make_speech_executor(tmp_path / "tts.db", backend)
    try:
        request = action(env, "speak", {"text": "正在上移。"})
        assert (await env.submit(request)).accepted
        await env.runner
        assert (await env.submit(request)).accepted
        assert len(rendered) == len(played) == env.world.completed == 1
        assert env.status(request.action_id).state == ActionState.SUCCEEDED
    finally:
        await env.close()


def test_qwen_stop_lands_between_chunks_and_always_closes_the_stream():
    """CUDA graph replay skips Python forward hooks, so chunk boundaries are the only
    place synthesis can be interrupted."""
    produced, closed = [], []

    def stream(**kwargs):
        assert kwargs["chunk_size"] == STOP_CHECK_STEPS
        try:
            for index in range(4):
                produced.append(index)
                yield [0.1 * index], 24000, {"chunk_index": index}
        finally:
            closed.append(True)

    renderer = QwenTTS.__new__(QwenTTS)
    renderer.speaker = "Vivian"
    renderer.model = SimpleNamespace(generate_custom_voice_streaming=stream)

    stop = threading.Event()
    stop.set()
    assert renderer.render("你好", stop) is None
    # Stopped at the first boundary instead of draining the whole utterance.
    assert produced == [0] and closed == [True]

    stop.clear()
    chunks, rate = renderer.render("你好", stop)
    assert rate == 24000 and chunks == [[0.0], [0.1], [0.2], [0.30000000000000004]]
    assert produced == [0, 0, 1, 2, 3] and closed == [True, True]


def test_qwen_render_propagates_synthesis_failure_and_closes_the_stream():
    closed = []

    def stream(**kwargs):
        try:
            yield [0.5], 24000, {"chunk_index": 0}
            raise ValueError("inference failed")
        finally:
            closed.append(True)

    renderer = QwenTTS.__new__(QwenTTS)
    renderer.speaker = "Vivian"
    renderer.model = SimpleNamespace(generate_custom_voice_streaming=stream)
    with pytest.raises(ValueError, match="inference failed"):
        renderer.render("你好", threading.Event())
    assert closed == [True]


@pytest.mark.parametrize("kind", ["sha256", "git_sha1"])
def test_download_verifies_bytes_not_only_file_size(tmp_path, kind):
    payload = b"abc"
    digest = (hashlib.sha256(payload).hexdigest() if kind == "sha256"
              else hashlib.sha1(b"blob 3\0" + payload).hexdigest())
    entry = {"path": "config.json", "size": 3, kind: digest}
    path = tmp_path / "config.json"
    path.write_bytes(payload)
    assets.verify_file(path, entry)
    path.write_bytes(b"xyz")
    with pytest.raises(ValueError, match="hash_mismatch"):
        assets.verify_file(path, entry)


def test_model_loading_requires_verified_local_files(tmp_path, monkeypatch):
    entry = {"repo": "Qwen/test", "revision": "abc", "files": [{"path": "model", "size": 3}]}
    monkeypatch.setattr(assets, "manifest", lambda: {"tts": entry})
    with pytest.raises(FileNotFoundError, match="download_speech_models"):
        assets.model_directory("tts", tmp_path)
    directory = tmp_path / "test"
    directory.mkdir()
    model = directory / "model"
    model.write_bytes(b"abc")
    receipt = {"revision": "abc", "files": {"model": [3, model.stat().st_mtime_ns]}}
    (directory / ".verified.json").write_text(json.dumps(receipt))
    assert assets.model_directory("tts", tmp_path) == directory
    model.write_bytes(b"changed")
    with pytest.raises(ValueError, match="speech_model_changed"):
        assets.model_directory("tts", tmp_path)


def test_asr_loader_is_local_chinese_command_profile(monkeypatch, tmp_path):
    from wrs_agent.nodes.asr import qwen as asr

    called = []
    model = SimpleNamespace(from_pretrained=lambda *a, **kw: called.append((a, kw)))
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(Qwen3ASRModel=model))
    monkeypatch.setattr(asr, "find_spec", lambda name: object())
    monkeypatch.setattr(asr, "model_directory", lambda kind: tmp_path)
    monkeypatch.setattr(asr, "offline_cuda", lambda: SimpleNamespace(bfloat16="bf16"))
    asr.QwenASR()
    assert called[0][0] == (str(tmp_path),)
    assert called[0][1]["local_files_only"] is True
    assert called[0][1]["max_inference_batch_size"] == 1
    assert called[0][1]["max_new_tokens"] == 256


def example_dispatch():
    return runpy.run_path(str(Path("examples/voice/06_push_to_talk.py")))["dispatch"]


async def test_voice_example_stop_bypasses_motion_planning():
    system = SimpleNamespace(send_text=AsyncMock(return_value=SimpleNamespace(
        disposition="stop", accepted=True, phase="STOPPING", overview=None)))
    await example_dispatch()(system, "停止。", None)
    system.send_text.assert_awaited_once_with("停止")


@pytest.mark.parametrize("text", ["不要向上", "向上然后向左", "", "正在上移。", "想上"])
async def test_voice_example_does_not_turn_unrecognized_or_echo_text_into_goals(text):
    await example_dispatch()(SimpleNamespace(), text, None)


async def test_voice_example_rejects_late_recognition_after_control_change():
    current = SimpleNamespace(boot_id="robot", control_epoch=2)
    captured = SimpleNamespace(boot_id="robot", control_epoch=1)
    system = SimpleNamespace(snapshot=AsyncMock(return_value=current))
    await example_dispatch()(system, "向上", captured)


async def test_voice_example_starts_motion_and_speech_without_waiting():
    current = SimpleNamespace(
        boot_id="robot", control_epoch=2, admission="OPEN", stop_confirmed=True
    )
    system = SimpleNamespace(
        snapshot=AsyncMock(return_value=current),
        status=AsyncMock(return_value={"task_id": None, "state": "IDLE"}),
        start=AsyncMock(return_value=SimpleNamespace(id="new-task")),
    )
    await example_dispatch()(system, "向上。", current)
    motion, speech = system.start.call_args.args
    assert motion.skill == "move_relative" and motion.args == {"dz": 0.02}
    assert speech.skill == "speak" and not speech.depends_on


def test_speech_node_uses_own_interpreter_without_core_dependency_overlay(tmp_path):
    from wrs_agent.processes import LocalStack

    interpreter = tmp_path / "python.exe"
    interpreter.touch()
    stack = LocalStack(tts_backend="qwen", tts_python=interpreter,
                       tts_prepared_texts=["正在上移。"])
    stack.endpoint = "tcp/127.0.0.1:9999"
    command = stack.node_command("tts")
    assert command[0] == str(interpreter)
    assert "-S" not in command and "scripts/run.py" not in command
    options = json.loads(command[command.index("--options") + 1])
    assert options["backend"] == "qwen" and options["prepared_texts"] == ["正在上移。"]


def test_capture_node_uses_own_interpreter_and_carries_its_vocabulary(tmp_path):
    from wrs_agent.processes import LocalStack

    interpreter = tmp_path / "python.exe"
    interpreter.touch()
    stack = LocalStack(bindings="tests/fixtures/asr.toml", asr_backend="qwen",
                       asr_python=interpreter, asr_vocabulary=["机器人", "停止"])
    stack.endpoint = "tcp/127.0.0.1:9999"
    command = stack.node_command("asr")
    assert command[0] == str(interpreter)
    assert "-S" not in command and "scripts/run.py" not in command
    options = json.loads(command[command.index("--options") + 1])
    assert options["backend"] == "qwen" and options["vocabulary"] == ["机器人", "停止"]
    # Capture options stay on the capture node; Voice keeps its own command.
    voice = stack.node_command("voice")
    assert json.loads(voice[voice.index("--options") + 1]) == {}


def test_speech_nodes_do_not_inherit_python_path(tmp_path, monkeypatch):
    from wrs_agent.processes import LocalStack

    interpreter = tmp_path / "python.exe"
    interpreter.touch()
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "other-venv-site-packages"))
    stack = LocalStack(bindings="tests/fixtures/asr.toml", tts_backend="qwen",
                       tts_python=interpreter, asr_backend="qwen", asr_python=interpreter)
    stack.endpoint = "tcp/127.0.0.1:9999"
    for role in ("tts", "asr"):
        env = stack.node_env(stack.node_command(role))
        assert "PYTHONPATH" not in env and env["WRS_AGENT_TOKEN"] == stack.token
    assert "PYTHONPATH" in stack.node_env(stack.node_command("agent"))


def test_playback_continues_after_output_underflow(monkeypatch):
    from wrs_agent.nodes.tts.backend import play_audio

    class Samples:
        def reshape(self, *args):
            return self

        def __len__(self):
            return 960

        def __getitem__(self, key):
            return self

    events = []

    class Stream:
        def __init__(self, **kwargs):
            events.append(kwargs.get("latency"))

        def start(self):
            events.append("start")

        def write(self, samples):
            events.append("write")
            return 1

        def abort(self):
            events.append("abort")

        def stop(self):
            events.append("drain")

        def close(self):
            events.append("close")

    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(
        asarray=lambda *a, **k: Samples(),
        concatenate=lambda parts: Samples(),
        isfinite=lambda a: SimpleNamespace(all=lambda: True),
    ))
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=Stream))
    assert play_audio([0], 24000, threading.Event()) is True
    assert events[0] == "high"
    assert "abort" not in events and events[-2:] == ["drain", "close"]


@pytest.mark.parametrize("failure", ["write", "abort", "close"])
def test_audio_device_errors_never_look_like_confirmed_success(monkeypatch, failure):
    from wrs_agent.nodes.tts.backend import play_audio

    class Samples:
        def reshape(self, *args):
            return self

        def __len__(self):
            return 960

        def __getitem__(self, key):
            return self

    stop = threading.Event()
    events = []

    class Stream:
        def __init__(self, **kwargs):
            pass

        def start(self):
            events.append("start")

        def write(self, samples):
            events.append("write")
            if failure == "write":
                raise OSError("device_disappeared")
            if failure == "abort":
                stop.set()
            return False

        def abort(self):
            events.append("abort")
            if failure == "abort":
                raise OSError("abort_failed")

        def stop(self):
            events.append("drain")

        def close(self):
            events.append("close")
            if failure == "close":
                raise OSError("close_failed")

    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(
        asarray=lambda *a, **k: Samples(),
        concatenate=lambda parts: Samples(),
        isfinite=lambda a: SimpleNamespace(all=lambda: True),
    ))
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=Stream))
    with pytest.raises(ExecutionUnknown):
        play_audio([0], 24000, stop)
    assert events[-1] == "close"


@pytest.mark.parametrize("words", ["向上", [""], [1], ["向上"] * 65])
def test_invalid_vocabulary_rejected_before_model_load(words):
    from wrs_agent.nodes.asr.qwen import QwenASR

    with pytest.raises(ValueError, match="vocabulary"):
        QwenASR(vocabulary=words)


def test_prepared_speech_cache_is_bounded_across_calls():
    backend = SpeechBackend(lambda *args: ([0], 16000))
    backend.prepare([str(i) for i in range(32)])
    with pytest.raises(ValueError, match="too_many"):
        backend.prepare(["more"])


@pytest.mark.parametrize("kind", ["asr", "tts"])
def test_default_model_location_survives_ide_working_directory(tmp_path, monkeypatch, kind):
    entry = {"repo": "Qwen/test", "revision": "abc", "files": [{"path": "model", "size": 3}]}
    monkeypatch.setattr(assets, "manifest", lambda: {kind: entry})
    monkeypatch.setattr(assets, "DEFAULT_MODEL_ROOT", tmp_path / "project/models")
    monkeypatch.delenv("WRS_AGENT_MODELS", raising=False)
    directory = assets.DEFAULT_MODEL_ROOT / "test"
    directory.mkdir(parents=True)
    model = directory / "model"
    model.write_bytes(b"abc")
    receipt = {"revision": "abc", "files": {"model": [3, model.stat().st_mtime_ns]}}
    (directory / ".verified.json").write_text(json.dumps(receipt))
    outside = tmp_path / "ide-working-directory"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert assets.model_directory(kind) == directory
    model.write_bytes(b"xyz")
    # A stable path does not waive the verified resource check.
    receipt["files"]["model"][1] -= 1
    (directory / ".verified.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="speech_model_changed"):
        assets.model_directory(kind)


def test_model_root_preserves_explicit_and_environment_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WRS_AGENT_MODELS", "custom-models")
    assert assets.model_root() == tmp_path / "custom-models"
    assert assets.model_root("explicit-models") == tmp_path / "explicit-models"
    monkeypatch.setenv("WRS_AGENT_MODELS", "")
    assert assets.model_root() == assets.DEFAULT_MODEL_ROOT


def test_downloader_and_loader_share_default_outside_repository(tmp_path, monkeypatch):
    path = Path("scripts/download_speech_models.py").resolve()
    entry = runpy.run_path(str(path))
    default = tmp_path / "project/models"
    monkeypatch.setattr(assets, "DEFAULT_MODEL_ROOT", default)
    monkeypatch.delenv("WRS_AGENT_MODELS", raising=False)
    calls = []
    monkeypatch.setitem(entry["main"].__globals__, "download", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", [str(path), "--model", "asr"])
    monkeypatch.chdir(tmp_path)
    entry["main"]()
    assert calls == [("asr", assets.model_root())] == [("asr", default)]


def test_missing_models_report_the_absolute_searched_path(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "DEFAULT_MODEL_ROOT", tmp_path / "models")
    monkeypatch.delenv("WRS_AGENT_MODELS", raising=False)
    with pytest.raises(FileNotFoundError, match="missing or unverified") as exc:
        assets.model_directory("asr")
    assert str(tmp_path / "models") in str(exc.value)


def test_wrong_asr_interpreter_reports_the_prepared_environment_before_loading(monkeypatch):
    from wrs_agent.nodes.asr import qwen as asr

    monkeypatch.setattr(asr, "find_spec", lambda name: None)

    def unexpected_load(*args):
        raise AssertionError("Do not load assets or Torch with missing ASR dependencies")

    monkeypatch.setattr(asr, "model_directory", unexpected_load)
    monkeypatch.setattr(asr, "offline_cuda", unexpected_load)
    with pytest.raises(RuntimeError, match="qwen_asr is not installed") as exc:
        asr.QwenASR()
    assert sys.executable in str(exc.value)
    assert "qwen-asr" in str(exc.value) and "setup_speech.ps1" in str(exc.value)


async def test_cancelled_synthesis_cannot_play_before_stop_relay_runs(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    relay_gate = asyncio.Event()
    played = []

    def render(text, stopped):
        entered.set()
        assert release.wait(3)
        assert not stopped.is_set()  # The asynchronous relay is deliberately delayed.
        return [1.0], 24000

    env = make_speech_executor(
        tmp_path / "tts.db", SpeechBackend(render, lambda *args: played.append(args))
    )
    try:
        request = action(env, "speak", {"text": "迟到的合成"})
        assert (await env.submit(request)).accepted
        original_wait = env.stop_signal.wait

        async def delayed_wait():
            await relay_gate.wait()
            await original_wait()

        monkeypatch.setattr(env.stop_signal, "wait", delayed_wait)
        await eventually(entered.is_set, bool)
        receipt = await asyncio.wait_for(env.cancel(control(env)), 0.2)
        assert receipt.accepted and env.stop_signal.is_set()
        release.set()
        await asyncio.wait_for(env.runner, 2)
        assert env.status(request.action_id).state == ActionState.CANCELLED
        assert env.stop_confirmed and not played and env.world.completed == 0
    finally:
        release.set()
        relay_gate.set()
        await env.close()
