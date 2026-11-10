"""说一句「抓杯子」：找到该物体，分割这块深度，再用 graspability 给抓取。

先运行 examples/nodes/07_start_vision.py。本文件打开 RealSense 彩色和对齐深度。
YOLO 持续看画面，直到出现这句话里的物体，graspability 再处理那个框。
这句话交给模型理解。画面用 SAM3 按这个说法查找，不限于固定类别。

返回的平移、旋转和夹爪张开都在深度相机坐标系里。这里还没有手眼标定，
所以不会驱动机械臂。识别结束后窗口保持打开，上面有检测框，按 q 才关闭。

  python -X utf8 -S scripts/run.py examples/nodes/11_voice_grasp.py
  python -X utf8 -S scripts/run.py examples/nodes/11_voice_grasp.py --text 抓杯子

画面上按 q 或关掉窗口即停止。
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

LIVE = Path("/tmp/wrs-grasp")
FRAME = LIVE / "frame.jpg"
DEPTH = LIVE / "depth.npy"
CAMERA = LIVE / "camera.json"
OVERLAY = LIVE / "overlay.json"
CAMERA_PYTHON = Path.home() / "miniconda3/envs/yolov8/bin/python"
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
VISION = ("tcp/127.0.0.1:7448", "node-demo")


def _token():
    os.environ.pop("WRS_AGENT_TOKEN", None)
    from examples._session import use_local_token

    use_local_token("nodes")


def _phrase():
    if "--text" in sys.argv:
        index = sys.argv.index("--text")
        if index + 1 >= len(sys.argv):
            raise SystemExit("请在 --text 后面写要抓的物体，例如：--text 抓杯子")
        return sys.argv[index + 1]
    from wrs_agent.nodes.asr.capture import record_command
    from wrs_agent.nodes.asr.qwen import QwenASR

    print("请说要抓的物体，例如：把鼠标拿起来。", flush=True)
    audio = record_command()
    if audio is None:
        raise SystemExit("没有听清。")
    heard = QwenASR().transcribe(audio)
    print("听到：", heard.text, flush=True)
    return heard.text


def _write_overlay(sentence, items, chosen=None):
    payload = {
        "sentence": sentence,
        "chosen": list(chosen) if chosen else None,
        "items": [
            {
                "label": item.label,
                "score": item.score,
                "xyxy": list(item.xyxy or []),
            }
            for item in items
        ],
    }
    temporary = OVERLAY.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, OVERLAY)


def _read_overlay():
    try:
        data = json.loads(OVERLAY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", [], None
    if not isinstance(data, dict):
        return "", [], None
    items = data.get("items")
    sentence = data.get("sentence")
    return (
        sentence if isinstance(sentence, str) else "",
        items if isinstance(items, list) else [],
        data.get("chosen") if isinstance(data.get("chosen"), list) else None,
    )


def _paint(image, sentence, items, chosen):
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    canvas = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(FONT, 22)
    except OSError:
        font = ImageFont.load_default()
    for item in items:
        box = item.get("xyxy") if isinstance(item, dict) else None
        if not box or len(box) != 4:
            continue
        x1, y1, x2, y2 = box
        same = chosen is not None and [round(value, 2) for value in box] == [
            round(float(value), 2) for value in chosen
        ]
        color = (80, 220, 80) if same or chosen is None else (180, 180, 180)
        draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
        label = f"{item.get('label', '')} {float(item.get('score', 0)):.2f}"
        draw.text((x1 + 4, max(0, y1 - 26)), label, fill=color, font=font)
    if sentence:
        draw.rectangle([0, 0, canvas.width, 34], fill=(0, 0, 0))
        draw.text((8, 4), sentence, fill=(255, 255, 255), font=font)
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


def _open_camera():
    import pyrealsense2 as rs

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        found = rs.context().query_devices()
        if len(found):
            break
        time.sleep(0.3)
    else:
        raise SystemExit(
            "USB 上没有 RealSense。"
            "请把相机拔掉，等三秒再插上，然后重新运行。"
        )
    device = found[0]
    usb = ""
    if device.supports(rs.camera_info.usb_type_descriptor):
        usb = device.get_info(rs.camera_info.usb_type_descriptor)
    fps = 30 if usb.startswith("3") else 15
    print(f"打开 RealSense，USB {usb or '未知'}，{fps} 帧。", flush=True)
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, fps)
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, fps)
    profile = pipeline.start(config)
    scale = profile.get_device().first_depth_sensor().get_depth_scale()
    return pipeline, scale


def camera_main():
    import signal

    import cv2
    import numpy as np
    import pyrealsense2 as rs

    quitting = False

    def _quit(_signum, _frame):
        nonlocal quitting
        quitting = True

    signal.signal(signal.SIGTERM, _quit)
    signal.signal(signal.SIGINT, _quit)
    LIVE.mkdir(parents=True, exist_ok=True)
    window = "RealSense"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, 960, 720)
    cv2.moveWindow(window, 40, 40)
    cv2.setWindowProperty(window, cv2.WND_PROP_TOPMOST, 1)
    blank = np.zeros((480, 640, 3), np.uint8)
    cv2.imshow(window, _paint(blank, "正在打开相机", [], None))
    cv2.waitKey(1)
    pipeline = None
    try:
        pipeline, scale = _open_camera()
        align = rs.align(rs.stream.color)
        reset_left = 1
        waiting_since = time.monotonic()
        while not quitting:
            try:
                frames = align.process(pipeline.wait_for_frames(1000))
            except RuntimeError:
                cv2.imshow(window, _paint(blank, "正在等相机画面", [], None))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                if time.monotonic() - waiting_since < 5 or reset_left == 0:
                    if reset_left == 0 and time.monotonic() - waiting_since > 8:
                        print(
                            "相机仍无画面。请拔掉再插上，优先插蓝色 USB3 口。",
                            flush=True,
                        )
                        break
                    continue
                print("5 秒没有画面，正在复位相机。", flush=True)
                reset_left = 0
                device = pipeline.get_active_profile().get_device()
                device.hardware_reset()
                pipeline = None
                time.sleep(4)
                if quitting:
                    break
                pipeline, scale = _open_camera()
                waiting_since = time.monotonic()
                continue
            color = frames.get_color_frame()
            depth = frames.get_depth_frame()
            if not color or not depth:
                continue
            waiting_since = time.monotonic()
            image = np.asanyarray(color.get_data())
            meters = np.asanyarray(depth.get_data()).astype(np.float32) * scale
            temporary = FRAME.with_suffix(".tmp.jpg")
            ok, encoded = cv2.imencode(".jpg", image)
            if not ok:
                continue
            temporary.write_bytes(encoded.tobytes())
            os.replace(temporary, FRAME)
            depth_tmp = DEPTH.with_name("depth.tmp.npy")
            np.save(depth_tmp, meters)
            os.replace(depth_tmp, DEPTH)
            intrinsics = color.profile.as_video_stream_profile().intrinsics
            camera = {
                "fx": float(intrinsics.fx),
                "fy": float(intrinsics.fy),
                "cx": float(intrinsics.ppx),
                "cy": float(intrinsics.ppy),
            }
            camera_tmp = CAMERA.with_suffix(".tmp.json")
            camera_tmp.write_text(json.dumps(camera), encoding="utf-8")
            os.replace(camera_tmp, CAMERA)
            cv2.imshow(window, _paint(image, *_read_overlay()))
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        if pipeline is not None:
            try:
                pipeline.stop()
            except RuntimeError:
                pass
        cv2.destroyAllWindows()


def _ready(camera):
    deadline = time.monotonic() + 25
    ready = FRAME.is_file() and DEPTH.is_file() and CAMERA.is_file()
    while camera.poll() is None and not ready:
        if time.monotonic() >= deadline:
            raise SystemExit("相机没有写出彩色图和深度图。")
        time.sleep(0.05)
        ready = FRAME.is_file() and DEPTH.is_file() and CAMERA.is_file()
    if not ready:
        raise SystemExit("相机没有写出彩色图和深度图。")


def _ask_model(phrase):
    import httpx

    from wrs_agent.nodes.vision.speech import grasp_instruction
    from wrs_agent.planner.providers.llm import USER_AGENT, LLMConfig, LLMError
    from wrs_agent.planner.providers.openai_chat import headers
    from wrs_agent.planner.providers.wire import with_extra

    try:
        config = LLMConfig.from_env()
    except LLMError as exc:
        raise RuntimeError(str(exc)) from None
    if config.protocol != "openai_chat":
        raise RuntimeError("类别判断使用 openai_chat。")
    key = os.environ.get("LLM_API_KEY", "")
    if (not key and not config.local) or (
        key and (len(key) > 512 or not key.isascii() or not key.isprintable() or " " in key)
    ):
        raise RuntimeError("模型配置错误：llm_api_key_missing_or_invalid")
    body = {
        "model": config.model,
        "stream": False,
        "max_tokens": min(config.max_tokens, 64),
        "messages": [
            {"role": "system", "content": grasp_instruction()},
            {"role": "user", "content": phrase},
        ],
    }
    if config.reasoning_effort is not None:
        body["reasoning_effort"] = config.reasoning_effort
    with httpx.Client(
        base_url=config.base_url + "/",
        headers={**config.extra_headers, **headers(key), "User-Agent": USER_AGENT},
        timeout=config.timeout_s,
        follow_redirects=False,
        trust_env=False,
        proxy=config.proxy,
    ) as client:
        response = client.post("chat/completions", json=with_extra(body, config.extra_body))
    if response.status_code != 200:
        raise RuntimeError(f"llm_http_{response.status_code}")
    message = response.json()["choices"][0]["message"]
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    text = str(content).strip()
    if not text:
        raise RuntimeError("模型没有返回类别")
    return text


def _target(phrase):
    from wrs_agent.nodes.vision.speech import grasp_declined, grasp_label, grasp_query

    reply = None
    try:
        reply = _ask_model(phrase)
    except Exception as exc:
        print(f"模型暂时不可用：{exc}", flush=True)
    if reply is not None:
        query = grasp_query(reply)
        if query is not None:
            print(f"画面里找：{query}", flush=True)
            return query
        if grasp_declined(reply):
            raise SystemExit(f"这句没有要抓的物体：{phrase}")
        print(f"模型回答无法用于检测：{reply}", flush=True)
    label = grasp_label(phrase)
    if label is None:
        raise SystemExit(f"没有听成抓取指令：{phrase}")
    print(f"按原话找：{label}", flush=True)
    return label


def _pick(items):
    chosen = None
    score = -1.0
    for item in items:
        box = getattr(item, "xyxy", None)
        if not box or len(box) != 4:
            continue
        value = float(getattr(item, "score", 0) or 0)
        if chosen is None or value > score:
            chosen = item
            score = value
    return chosen


def _grasp(phrase, camera_proc):
    from wrs_agent import connect

    query = _target(phrase)
    camera = json.loads(CAMERA.read_text(encoding="utf-8"))
    endpoint, env_id = VISION
    print(f"持续在画面里找「{query}」。第一次会加载 SAM3。按 q 结束。", flush=True)
    reported = None
    with connect(endpoint, env_id=env_id) as vision:
        while camera_proc.poll() is None:
            status = vision.action("segment", image=str(FRAME), text=query).wait(timeout=180)
            if camera_proc.poll() is not None:
                return
            if status.state != "SUCCEEDED":
                raise SystemExit(f"检测失败：{status.reason or status.state}")
            items = vision.snapshot("sam3").data.items
            chosen = _pick(items)
            if chosen is not None:
                break
            note = f"正在找「{query}」。画面里还没有。"
            _write_overlay(note, items)
            if note != reported:
                print(note, flush=True)
                reported = note
            time.sleep(0.3)
        else:
            return
        x1, y1, x2, y2 = chosen.xyxy
        print(
            f"目标：{query}  框：{x1:.0f},{y1:.0f} → {x2:.0f},{y2:.0f}",
            flush=True,
        )
        _write_overlay(f"目标：{query}", items, chosen.xyxy)
        status = vision.action(
            "graspability",
            depth=str(DEPTH),
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            fx=camera["fx"],
            fy=camera["fy"],
            cx=camera["cx"],
            cy=camera["cy"],
            top=3,
        ).wait(timeout=60)
        if status.state != "SUCCEEDED":
            raise SystemExit(f"graspability 失败：{status.reason or status.state}")
        items = vision.snapshot("graspability").data.items
    if not items:
        raise SystemExit("这块深度里没有可用的抓取。")
    grasp = items[0]
    x, y, z = grasp.translation
    print(f"抓取（相机系，米）：x={x:.3f} y={y:.3f} z={z:.3f}", flush=True)
    print(f"夹爪张开：{grasp.width:.3f} 米，评分 {grasp.score:.2f}", flush=True)
    print("graspability SUCCEEDED", flush=True)


def main():
    if not CAMERA_PYTHON.is_file():
        raise SystemExit(f"找不到相机解释器：{CAMERA_PYTHON}")
    LIVE.mkdir(parents=True, exist_ok=True)
    for path in (FRAME, DEPTH, CAMERA, OVERLAY):
        path.unlink(missing_ok=True)
    _token()
    environment = os.environ.copy()
    environment.setdefault("DISPLAY", ":0")
    camera = subprocess.Popen(
        [str(CAMERA_PYTHON), str(Path(__file__).resolve()), "--camera"],
        env=environment,
    )
    try:
        _ready(camera)
        from wrs_agent import AgentError

        try:
            _grasp(_phrase(), camera)
        except SystemExit as stop:
            if isinstance(stop.code, str) and stop.code:
                print(stop.code, flush=True)
        except AgentError as exc:
            print(exc, flush=True)
        if camera.poll() is None:
            print("按 q 关闭相机窗口。", flush=True)
            camera.wait()
    finally:
        if camera.poll() is None:
            camera.terminate()
            camera.wait(timeout=5)


if __name__ == "__main__":
    if "--camera" in sys.argv:
        camera_main()
    else:
        main()
