"""识别一张桌面照片里的物体，并用已启动的 TTS 节点读出来。

先运行 07_start_vision.py。播报再运行 examples/tts/01_start_node.py。
MODE 用 yolo 或 sam3。sam3 还要填写 TEXT 提示词。
"""

import os
from pathlib import Path

from examples._session import use_local_token
from wrs_agent import connect
from wrs_agent.nodes.vision.speech import spoken_objects

IMAGE = "/tmp/vision-test.jpg"
MODE = "yolo"  # yolo 或 sam3
TEXT = "plant"
VISION = "tcp/127.0.0.1:7448"
TTS = "tcp/127.0.0.1:7450"
TTS_BINDINGS = Path(__file__).resolve().parents[2] / "configs/tts.toml"


def _connect(group, endpoint, env_id, **kwargs):
    os.environ.pop("WRS_AGENT_TOKEN", None)
    use_local_token(group)
    return connect(endpoint, env_id=env_id, **kwargs)


def recognize():
    with _connect("nodes", VISION, "node-demo") as system:
        if MODE == "sam3":
            status = system.action("segment", image=IMAGE, text=TEXT).wait(timeout=180)
            shot = system.snapshot("sam3")
        else:
            status = system.action("detect", image=IMAGE).wait(timeout=180)
            shot = system.snapshot("yolo")
    if status.state != "SUCCEEDED":
        raise RuntimeError(status.reason or status.state)
    return spoken_objects(shot.data.items)


def say(text):
    with _connect("tts", TTS, "tts-demo", bindings=TTS_BINDINGS) as system:
        result = system.action("speak", text=text).wait(timeout=60)
    if result.state != "SUCCEEDED":
        raise RuntimeError(result.reason or result.state)


if __name__ == "__main__":
    sentence = recognize()
    print(sentence, flush=True)
    say(sentence)
    print("播报结果： SUCCEEDED", flush=True)
