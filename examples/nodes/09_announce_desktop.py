"""识别一张桌面照片里的物体，并用 TTS 读出来。

先启动视觉节点 examples/nodes/07_start_vision.py，
再启动 examples/tts/01_start_node.py。
默认用 YOLO。传入 sam3 时改用文字提示分割。

  python -X utf8 -S scripts/run.py examples/nodes/09_announce_desktop.py
  python -X utf8 -S scripts/run.py examples/nodes/09_announce_desktop.py sam3

图片默认 /tmp/vision-test.jpg，可用 WRS_ANNOUNCE_IMAGE 覆盖。
SAM3 提示默认 cup,bottle,plant,box,phone,book，可用 WRS_ANNOUNCE_TEXT 覆盖。
"""

import os
import sys

from examples._session import use_local_token
from wrs_agent import connect
from wrs_agent.nodes.vision.announce import desktop_utterance

IMAGE = os.environ.get("WRS_ANNOUNCE_IMAGE", "/tmp/vision-test.jpg")
SAM_TEXT = os.environ.get("WRS_ANNOUNCE_TEXT", "cup,bottle,plant,box,phone,book")
VISION = ("tcp/127.0.0.1:7448", "node-demo", "nodes")
SPEECH = ("tcp/127.0.0.1:7450", "tts-demo", "tts")


def _session(group, endpoint, env_id):
    os.environ.pop("WRS_AGENT_TOKEN", None)
    use_local_token(group)
    return connect(endpoint, env_id=env_id)


def recognize(source):
    if source == "sam3":
        skill, node, args = "segment", "sam3", {"image": IMAGE, "text": SAM_TEXT}
    elif source == "yolo":
        skill, node, args = "detect", "yolo", {"image": IMAGE}
    else:
        raise SystemExit("来源应为 yolo 或 sam3。")
    endpoint, env_id, group = VISION
    with _session(group, endpoint, env_id) as system:
        status = system.action(skill, **args).wait(timeout=180)
        shot = system.snapshot(node)
    if status.state != "SUCCEEDED":
        raise SystemExit(f"{skill} {status.state} {status.reason}")
    return desktop_utterance(shot.data.items)


def speak(text):
    endpoint, env_id, group = SPEECH
    with _session(group, endpoint, env_id) as system:
        status = system.action("speak", text=text).wait(timeout=60)
    if status.state != "SUCCEEDED":
        raise SystemExit(f"speak {status.state} {status.reason}")


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "yolo"
    sentence = recognize(source)
    print(sentence, flush=True)
    speak(sentence)


if __name__ == "__main__":
    main()
