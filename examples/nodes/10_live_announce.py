"""打开 RealSense 画面，用 YOLO 画框。问「有什么」之后再播报。

先运行 examples/nodes/07_start_vision.py 和 examples/tts/01_start_node.py。
相机窗口由 yolov8 解释器打开。听写由 .local/venvs/qwen-asr 完成。
YOLO 持续更新当前结果，只有听到「有什么」「有哪些」或「有啥」才把这句说出来。

  python -X utf8 -S scripts/run.py examples/nodes/10_live_announce.py

画面上按 q 或关掉窗口即停止。
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

LIVE = Path("/tmp/wrs-live")
FRAME = LIVE / "frame.jpg"
DEPTH = LIVE / "depth.npy"
CAMERA = LIVE / "camera.json"
PREVIEW = LIVE / "preview.jpg"
OVERLAY = LIVE / "overlay.json"
CAMERA_PYTHON = Path.home() / "miniconda3/envs/yolov8/bin/python"
ASR_PYTHON = Path(__file__).resolve().parents[2] / ".local/venvs/qwen-asr/bin/python"
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
VISION = ("tcp/127.0.0.1:7448", "node-demo", "nodes")
SPEECH = ("tcp/127.0.0.1:7450", "tts-demo", "tts")


def _token(group):
    os.environ.pop("WRS_AGENT_TOKEN", None)
    from examples._session import use_local_token

    use_local_token(group)


def _paint(image, items, sentence):
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    canvas = Image.fromarray(rgb)
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(FONT, 22)
    except OSError:
        font = ImageFont.load_default()
    for item in items:
        box = item.get("xyxy")
        if not box or len(box) != 4:
            continue
        x1, y1, x2, y2 = box
        draw.rectangle([x1, y1, x2, y2], outline=(80, 220, 80), width=3)
        label = f"{item.get('label', '')} {float(item.get('score', 0)):.2f}"
        draw.text((x1 + 4, max(0, y1 - 26)), label, fill=(80, 220, 80), font=font)
    if sentence:
        draw.rectangle([0, 0, canvas.width, 34], fill=(0, 0, 0))
        draw.text((8, 4), sentence, fill=(255, 255, 255), font=font)
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


def _read_overlay():
    try:
        data = json.loads(OVERLAY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], ""
    items = data.get("items") if isinstance(data, dict) else None
    sentence = data.get("sentence") if isinstance(data, dict) else ""
    if not isinstance(items, list):
        items = []
    return items, sentence if isinstance(sentence, str) else ""


def _write_jpeg(path, image):
    import cv2

    temporary = path.with_suffix(".tmp.jpg")
    ok, encoded = cv2.imencode(".jpg", image)
    if not ok:
        return
    temporary.write_bytes(encoded.tobytes())
    os.replace(temporary, path)


def camera_main():
    import cv2
    import numpy as np
    import pyrealsense2 as rs

    show = "--camera-headless" not in sys.argv
    LIVE.mkdir(parents=True, exist_ok=True)
    pipeline = rs.pipeline()
    config = rs.config()
    # This machine's D405 is on USB 2. 30 fps does not deliver frames.
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 15)
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 15)
    align = rs.align(rs.stream.color)
    scale = 0.0
    try:
        profile = pipeline.start(config)
        scale = float(profile.get_device().first_depth_sensor().get_depth_scale())
    except RuntimeError:
        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 15)
        pipeline.start(config)
    window = "RealSense"
    if show:
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window, 960, 720)
        cv2.moveWindow(window, 40, 40)
        cv2.setWindowProperty(window, cv2.WND_PROP_TOPMOST, 1)
    try:
        while True:
            try:
                frames = pipeline.wait_for_frames(1000)
            except RuntimeError:
                if show and cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                continue
            if scale:
                frames = align.process(frames)
            color = frames.get_color_frame()
            depth = frames.get_depth_frame() if scale else None
            if not color:
                continue
            image = np.asanyarray(color.get_data())
            _write_jpeg(FRAME, image)
            if depth is not None:
                meters = np.asanyarray(depth.get_data()).astype("float32") * scale
                temporary = DEPTH.with_name("depth.tmp.npy")
                np.save(temporary, meters)
                os.replace(temporary, DEPTH)
                intrinsics = color.profile.as_video_stream_profile().intrinsics
                camera_tmp = CAMERA.with_suffix(".tmp.json")
                camera_tmp.write_text(json.dumps({
                    "fx": float(intrinsics.fx),
                    "fy": float(intrinsics.fy),
                    "cx": float(intrinsics.ppx),
                    "cy": float(intrinsics.ppy),
                }), encoding="utf-8")
                os.replace(camera_tmp, CAMERA)
            view = _paint(image, *_read_overlay())
            if not show:
                _write_jpeg(PREVIEW, view)
                continue
            cv2.imshow(window, view)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        pipeline.stop()
        if show:
            cv2.destroyAllWindows()


def _write_overlay(sentence, items):
    payload = {"sentence": sentence, "items": items}
    temporary = OVERLAY.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, OVERLAY)


def _box_rows(items):
    rows = []
    for item in items:
        box = getattr(item, "xyxy", None)
        if not box or len(box) != 4:
            continue
        rows.append(
            {
                "label": str(getattr(item, "label", "") or ""),
                "score": float(getattr(item, "score", 0) or 0),
                "xyxy": [float(value) for value in box],
            }
        )
    return rows


def _box_loop(stop, seen):
    from wrs_agent import connect
    from wrs_agent.nodes.vision.announce import desktop_utterance

    try:
        _token("nodes")
        endpoint, env_id, _group = VISION
        sentence = ""
        with connect(endpoint, env_id=env_id) as vision:
            while not stop.is_set():
                if not FRAME.is_file():
                    stop.wait(0.1)
                    continue
                status = vision.action("detect", image=str(FRAME)).wait(timeout=30)
                if stop.is_set():
                    return
                if status.state != "SUCCEEDED":
                    print("检测：", status.state, status.reason, flush=True)
                    stop.wait(0.5)
                    continue
                items = vision.snapshot("yolo").data.items
                sentence = desktop_utterance(items)
                with seen["lock"]:
                    seen["text"] = sentence
                _write_overlay(sentence, _box_rows(items))
    except Exception as exc:
        print("检测停止：", exc, flush=True)


def _speak_loop(stop, pending):
    from wrs_agent import connect

    try:
        _token("tts")
        endpoint, env_id, _group = SPEECH
        with connect(endpoint, env_id=env_id) as speech:
            while not stop.is_set():
                with pending["lock"]:
                    seq, text, done = pending["seq"], pending["text"], pending["done"]
                if seq == done or not text:
                    stop.wait(0.05)
                    continue
                result = speech.action("speak", text=text).wait(timeout=60)
                print(text, result.state, flush=True)
                with pending["lock"]:
                    pending["done"] = seq
    except Exception as exc:
        print("播报停止：", exc, flush=True)
        stop.set()


def listen_main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    real_out = sys.stdout
    sys.stdout = sys.stderr
    from wrs_agent.nodes.asr.capture import record_command
    from wrs_agent.nodes.asr.qwen import QwenASR

    recognizer = QwenASR()
    sys.stdout = real_out
    print(json.dumps({"ready": True}, ensure_ascii=False), flush=True)
    for line in sys.stdin:
        if line.strip() != "listen":
            continue
        audio = record_command()
        text = "" if audio is None else recognizer.transcribe(audio).text
        print(json.dumps({"text": text}, ensure_ascii=False), flush=True)


def _ask_loop(stop, seen, pending, listener):
    from wrs_agent.nodes.vision.announce import asks_what_is_there

    try:
        ready = json.loads(listener.stdout.readline() or "")
        if not ready.get("ready"):
            raise RuntimeError("语音识别没有就绪")
        print("请说：有什么。", flush=True)
        while not stop.is_set():
            listener.stdin.write("listen\n")
            listener.stdin.flush()
            reply = json.loads(listener.stdout.readline() or "")
            heard = str(reply.get("text") or "")
            if not heard or stop.is_set():
                continue
            print("听到：", heard, flush=True)
            if not asks_what_is_there(heard):
                continue
            with seen["lock"]:
                text = seen["text"] or "还没有识别结果。"
            with pending["lock"]:
                pending["text"] = text
                pending["seq"] += 1
                seq = pending["seq"]
            print("播报句子：", text, flush=True)
            while not stop.is_set():
                with pending["lock"]:
                    finished = pending["done"] >= seq
                if finished:
                    break
                stop.wait(0.05)
    except Exception as exc:
        print("听写停止：", exc, flush=True)


def live_main():
    if not CAMERA_PYTHON.is_file():
        raise SystemExit(f"找不到相机解释器：{CAMERA_PYTHON}")
    if not ASR_PYTHON.is_file():
        raise SystemExit(f"找不到语音识别解释器：{ASR_PYTHON}")
    LIVE.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.setdefault("DISPLAY", ":0")
    camera = subprocess.Popen(
        [str(CAMERA_PYTHON), str(Path(__file__).resolve()), "--camera"],
        env=environment,
    )
    listener = subprocess.Popen(
        [str(ASR_PYTHON), str(Path(__file__).resolve()), "--listen"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stop = threading.Event()
    seen = {"text": "", "lock": threading.Lock()}
    pending = {"text": "", "seq": 0, "done": 0, "lock": threading.Lock()}
    speaker = threading.Thread(target=_speak_loop, args=(stop, pending), daemon=True)
    boxes = threading.Thread(target=_box_loop, args=(stop, seen), daemon=True)
    asking = threading.Thread(
        target=_ask_loop, args=(stop, seen, pending, listener), daemon=True,
    )
    try:
        print("相机窗口已打开。按 q 关闭。问「有什么」才播。", flush=True)
        print("正在加载语音识别……", flush=True)
        speaker.start()
        boxes.start()
        asking.start()
        deadline = time.monotonic() + 20
        while camera.poll() is None and not FRAME.is_file():
            if time.monotonic() >= deadline:
                raise SystemExit("相机没有写出画面。")
            time.sleep(0.05)
        if not FRAME.is_file():
            raise SystemExit("相机没有写出画面。")
        camera.wait()
    finally:
        stop.set()
        if camera.poll() is None:
            camera.terminate()
            camera.wait(timeout=5)
        if listener.poll() is None:
            listener.terminate()
            listener.wait(timeout=5)
        speaker.join(timeout=8)
        boxes.join(timeout=2)
        asking.join(timeout=2)


if __name__ == "__main__":
    if "--camera" in sys.argv or "--camera-headless" in sys.argv:
        camera_main()
    elif "--listen" in sys.argv:
        listen_main()
    else:
        live_main()
