import asyncio
import json
from pathlib import Path

import pytest

from wrs_agent.executor import SkillFailure
from wrs_agent.nodes.vision.announce import (
    UtteranceGate,
    asks_what_is_there,
    desktop_utterance,
    detection_labels,
)
from wrs_agent.nodes.vision.skills import DETECT, GRASPABILITY, detect, graspability
from wrs_agent.nodes.vision.speech import (
    best_match,
    grasp_choice,
    grasp_label,
    grasp_prompt,
    grasp_query,
    spoken_objects,
)
from wrs_agent.nodes.vision.state import VisionState
from wrs_agent.nodes.vision.worker import ModelWorker
from wrs_agent.schemas import NodeSnapshot, VisionData


def test_grasp_label_names_the_object():
    assert grasp_label("抓杯子") == "cup"
    assert grasp_label("请帮我抓一下苹果。") == "apple"
    assert grasp_label("拿起那个瓶子") == "bottle"
    assert grasp_label("grasp the cup") == "cup"
    assert grasp_label("不要抓杯子") is None
    assert grasp_label("停止") is None
    assert grasp_prompt("抓杯子") == "cup"
    assert grasp_prompt("抓住绿色盒子") == "绿色盒子"
    assert grasp_prompt("不要抓杯子") is None
    assert grasp_choice("mouse") == "mouse"
    assert grasp_choice("杯子") == "cup"
    assert grasp_choice("cell phone") == "cell phone"
    assert grasp_choice("none") is None
    assert grasp_choice("headphones") is None
    assert grasp_query("earphones") == "earphones"
    assert grasp_query("white mouse") == "white mouse"
    assert grasp_query("杯子") == "cup"
    assert grasp_query("none") is None
    assert grasp_query("red cup, left") == "red cup left"
    cup = {"label": "cup", "score": 0.4}
    mug = {"label": "cup", "score": 0.9}
    assert best_match((cup, {"label": "book", "score": 0.99}, mug), "cup") is mug
    assert best_match(({"label": "book", "score": 0.9},), "cup") is None


def test_spoken_objects_names_the_desktop():
    items = [
        {"label": "potted plant", "score": 0.7},
        {"label": "potted plant", "score": 0.4},
        {"label": "chair", "score": 0.3},
    ]
    assert spoken_objects(items) == "桌面上有两个盆栽和椅子。"
    assert spoken_objects(()) == "没有识别到桌面上的物体。"


def test_desktop_utterance_names_every_coco_class():
    assert desktop_utterance([{"label": "cat"}, {"label": "dog"}, {"label": "cat"}]) == (
        "桌面上有猫和狗。"
    )
    assert desktop_utterance([{"label": "traffic light"}]) == "桌面上有红绿灯。"
    assert desktop_utterance(()) == "桌面上没有识别到物体。"


def test_classify_desk_command_splits_question_and_motion():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "examples/voice/commands.py"
    spec = importlib.util.spec_from_file_location("desk_commands", path)
    commands = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(commands)
    assert commands.classify_desk_command("桌上有什么") == "ask"
    assert commands.classify_desk_command("向上") == "ignore"
    assert commands.classify_desk_command("向上5厘米") == "move"
    assert commands.classify_desk_command("停止") == "control"
    assert commands.classify_desk_command("状态") == "control"
    assert commands.classify_desk_command("随便说说") == "ignore"
    payload, speech = commands.fafu_payload("向上5厘米")
    assert payload == {"cmd": "nudge", "dx": 0.0, "dy": 0.0, "dz": 0.05}
    assert speech == "正在向上移动5厘米。"
    spoken = commands.fafu_payload("向左十二厘米")
    assert spoken[0]["dy"] == 0.12
    assert abs(commands.fafu_payload("向下移动3.5厘米")[0]["dz"] + 0.035) < 1e-9
    assert commands.fafu_payload("打开夹爪")[0] == {"cmd": "open"}


def test_asks_what_is_there_matches_the_question_only():
    assert asks_what_is_there("有什么")
    assert asks_what_is_there("桌上有什么东西")
    assert asks_what_is_there("有哪些")
    assert asks_what_is_there("有啥")
    assert asks_what_is_there("没有什么") is False
    assert asks_what_is_there("没什么") is False
    assert asks_what_is_there("抓杯子") is False
    assert asks_what_is_there("") is False


def test_utterance_gate_waits_for_a_repeated_label_set():
    gate = UtteranceGate()
    cup = detection_labels([{"label": "cup"}, {"label": "Cup"}])
    assert cup == ("cup",)
    assert gate.needs_sentence(cup) is False
    assert gate.needs_sentence(cup) is True
    gate.remember(cup)
    assert gate.needs_sentence(cup) is False
    book = ("book",)
    assert gate.needs_sentence(book) is False
    assert gate.needs_sentence(book) is True


def test_vision_snapshot_roundtrip():
    data = VisionData(task="detect", summary="1 detect: cup", items=[])
    snapshot = NodeSnapshot(
        node_id="yolo",
        boot_id="boot",
        captured_at_ns=1,
        control_epoch=0,
        state_version=0,
        admission="OPEN",
        active_action=None,
        stop_confirmed=True,
        data=data,
    )
    restored = NodeSnapshot.model_validate_json(snapshot.model_dump_json())
    assert isinstance(restored.data, VisionData)
    assert restored.data.task == "detect"


def test_detect_rejects_a_missing_image(tmp_path):
    state = VisionState(worker=None, result_dir=tmp_path)

    async def call():
        await detect(
            state,
            DETECT.arguments(image="missing.png"),
            asyncio.Event(),
            lambda _value: None,
        )

    with pytest.raises(SkillFailure, match="input_missing"):
        asyncio.run(call())


def test_worker_records_a_detection(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'ready': True}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    sys.stdout.write(json.dumps({'ok': True, 'count': 1, 'items': ["
        "{'label': 'cup', 'score': 0.9, 'xyxy': [1, 2, 30, 40]}]})\n"
        " + '\\n')\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8",
    )
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG\r\n")
    worker = ModelWorker(script.name, "yolo")
    worker.script = script
    worker.start()
    try:
        state = VisionState(worker, tmp_path / "results")
        state.result_dir.mkdir()

        async def call():
            options = DETECT.arguments(image=str(image))
            return await detect(state, options, asyncio.Event(), lambda _value: None)

        assert asyncio.run(call()) is True
    finally:
        worker.close()
    assert state.completed == 1
    assert state.items[0].label == "cup"
    saved = json.loads(Path(state.result_path).read_text(encoding="utf-8"))
    assert saved["count"] == 1


def test_graspability_records_a_camera_frame_grasp(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'ready': True}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    assert request['depth'].endswith('.npy')\n"
        "    assert request['x2'] > request['x1']\n"
        "    sys.stdout.write(json.dumps({'ok': True, 'count': 1, 'items': [{"
        "'label': 'grasp', 'score': 0.8, "
        "'translation': [0.01, 0.02, 0.4], "
        "'rotation': [1, 0, 0, 0, 1, 0, 0, 0, 1], "
        "'width': 0.04}]})\n"
        " + '\\n')\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8",
    )
    depth = tmp_path / "depth.npy"
    depth.write_bytes(b"\x93NUMPY")
    worker = ModelWorker(script.name, "graspability")
    worker.script = script
    worker.start()
    try:
        state = VisionState(worker, tmp_path / "results")
        state.result_dir.mkdir()

        async def call():
            options = GRASPABILITY.arguments(
                depth=str(depth), x1=10.0, y1=20.0, x2=80.0, y2=90.0,
                fx=600.0, fy=600.0, cx=320.0, cy=240.0,
            )
            return await graspability(state, options, asyncio.Event(), lambda _value: None)

        assert asyncio.run(call()) is True
    finally:
        worker.close()
    assert state.task == "graspability"
    assert state.items[0].translation[2] == 0.4
    assert state.items[0].width == 0.04
