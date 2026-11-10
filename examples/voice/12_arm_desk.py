"""一个页面里同时看机械臂和相机，并用同一句语音操作。

先运行 examples/voice/05_start_wrs_voice.py 和 examples/nodes/07_start_vision.py。
07 还需要 examples/nodes/00_start_router.py。不要同时再开 07_viewer、06 或 10。

打开 http://127.0.0.1:8010 。按住「按住说话」：
向上、向下、向左、向右、向前、向后要带厘米数，例如「向上5厘米」，
回到初始位置把六个关节收到 0 弧度，打开夹爪、关闭夹爪操作夹爪。
问「有什么」则播报相机里当前识别到的物体。停止按钮对真机急停。
左侧画面仍是仿真 UR7E，不会跟着真机动。

Ctrl+C 关闭本页，不取消已经发出的动作。
"""

import json
import os
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
LIVE = Path("/tmp/wrs-live")
FRAME = LIVE / "frame.jpg"
OVERLAY = LIVE / "overlay.json"
PREVIEW = LIVE / "preview.jpg"
CAMERA_PYTHON = Path.home() / "miniconda3/envs/yolov8/bin/python"
ASR_PYTHON = ROOT / ".local/venvs/qwen-asr/bin/python"
FAFU_PYTHON = Path.home() / "miniconda3/envs/panthera/bin/python"
ARM = ("tcp/127.0.0.1:7449", "wrs-demo")
VISION = ("tcp/127.0.0.1:7448", "node-demo")
PAGE_PORT = 8010
VIEW_PORT = 8000

CONTEXT = {"board": None, "listener": None, "audio": None, "fafu": None}


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>机械臂和桌面</title>
<style>
  html, body { margin: 0; height: 100%; background: #f4f1ea; color: #24312c;
    font: 15px/1.45 "Noto Sans CJK SC", sans-serif; }
  main { display: grid; grid-template-columns: 1.3fr 0.7fr; height: calc(100% - 132px); }
  iframe, img { width: 100%; height: 100%; border: 0; background: #d9d3c7; object-fit: contain; }
  footer { height: 132px; padding: 10px 16px; box-sizing: border-box; }
  button { font: inherit; margin-right: 8px; padding: 8px 14px; }
  #talk.down { background: #276555; color: white; }
  p { margin: 6px 0; }
</style>
</head>
<body>
<main>
  <iframe src="http://127.0.0.1:8000/" title="机械臂"></iframe>
  <img id="camera" alt="相机" src="/preview.jpg" />
</main>
<footer>
  <button id="talk" type="button">按住说话</button>
  <button id="stop" type="button">停止</button>
  <p id="status">正在加载语音识别……</p>
  <p id="heard">最近识别：（无）</p>
  <p id="sentence">最近回答：（无）</p>
</footer>
<script>
const talk = document.getElementById("talk");
function post(path) { fetch(path, {method: "POST"}); }
talk.addEventListener("pointerdown", (event) => {
  talk.setPointerCapture(event.pointerId);
  talk.classList.add("down");
  post("/talk/begin");
});
function release() {
  talk.classList.remove("down");
  post("/talk/end");
}
talk.addEventListener("pointerup", release);
talk.addEventListener("pointercancel", release);
document.getElementById("stop").addEventListener("click", () => post("/stop"));
setInterval(() => {
  document.getElementById("camera").src = "/preview.jpg?" + Date.now();
  fetch("/status").then((reply) => reply.json()).then((data) => {
    document.getElementById("status").textContent = data.status;
    document.getElementById("heard").textContent = "最近识别：" + (data.heard || "（无）");
    document.getElementById("sentence").textContent = "最近回答：" + (data.sentence || "（无）");
  });
}, 400);
</script>
</body>
</html>
"""


def _token(group):
    os.environ.pop("WRS_AGENT_TOKEN", None)
    from examples._session import use_local_token

    use_local_token(group)


def _box_rows(items):
    rows = []
    for item in items:
        box = getattr(item, "xyxy", None)
        if not box or len(box) != 4:
            continue
        rows.append({
            "label": str(getattr(item, "label", "") or ""),
            "score": float(getattr(item, "score", 0) or 0),
            "xyxy": [float(value) for value in box],
        })
    return rows


def _write_overlay(sentence, items):
    OVERLAY.parent.mkdir(parents=True, exist_ok=True)
    temporary = OVERLAY.with_suffix(".tmp.json")
    temporary.write_text(
        json.dumps({"sentence": sentence, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, OVERLAY)


def listen_main():
    sys.path.insert(0, str(ROOT))
    real_out = sys.stdout
    sys.stdout = sys.stderr
    from examples.voice.commands import COMMANDS
    from wrs_agent.nodes.asr.capture import record_push_to_talk
    from wrs_agent.nodes.asr.qwen import QwenASR

    holding = threading.Event()

    def commands():
        for line in sys.stdin:
            command = line.strip()
            if command == "begin":
                holding.set()
            elif command == "end":
                holding.clear()

    threading.Thread(target=commands, daemon=True).start()
    recognizer = QwenASR(vocabulary=[
        *COMMANDS, "停止", "停止播报", "状态", "有什么", "有哪些", "有啥",
    ])
    sys.stdout = real_out
    print(json.dumps({"ready": True}, ensure_ascii=False), flush=True)
    while True:
        holding.wait()
        audio = record_push_to_talk(holding.is_set)
        holding.clear()
        text = "" if audio is None else recognizer.transcribe(audio).text
        print(json.dumps({"text": text}, ensure_ascii=False), flush=True)


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._bytes(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            return
        if path == "/preview.jpg":
            try:
                payload = PREVIEW.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self._bytes(200, "image/jpeg", payload)
            return
        if path == "/status":
            board = CONTEXT["board"]
            with board["lock"]:
                payload = {key: board[key] for key in ("status", "heard", "sentence")}
            self._bytes(200, "application/json", json.dumps(payload, ensure_ascii=False).encode())
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        listener = CONTEXT["listener"]
        board = CONTEXT["board"]
        if path == "/talk/begin":
            with board["lock"]:
                board["status"] = "正在录音，松开后识别。"
            self._signal(listener, "begin")
            return
        if path == "/talk/end":
            self._signal(listener, "end")
            return
        if path == "/stop":
            with board["lock"]:
                board["stop"] = True
                board["status"] = "停止中…"
            self._bytes(204, "text/plain", b"")
            return
        self.send_error(404)

    def _signal(self, listener, command):
        try:
            listener.stdin.write(command + "\n")
            listener.stdin.flush()
        except (AttributeError, BrokenPipeError, OSError):
            self.send_error(503)
            return
        self._bytes(204, "text/plain", b"")

    def _bytes(self, status, content_type, payload):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


def _read_audio(listener, audio, board):
    for line in listener.stdout:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("ready"):
            with board["lock"]:
                current = board["status"]
                if current.startswith("真机已连接") or current.startswith("真机没连上"):
                    continue
                board["status"] = "语音已就绪，正在连接真机。"
            continue
        audio.put(str(message.get("text") or ""))


def _send_fafu(payload):
    proc = CONTEXT.get("fafu")
    if proc is None or proc.poll() is not None:
        raise RuntimeError("真机进程不在。")
    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def _read_fafu(proc, board):
    for line in proc.stdout:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("ready") is True:
            text = f"真机已连接（{message.get('port', '')}）。按住说话即可。"
        elif message.get("ready") is False:
            text = "真机没连上：" + str(message.get("error") or "未知原因")
        else:
            text = str(message.get("status") or "")
        if not text:
            continue
        with board["lock"]:
            board["status"] = text
        print(text, flush=True)


def _apply(system, heard, captured, scene):
    from examples.voice.commands import classify_desk_command, fafu_payload, normalize
    from wrs_agent.policy import text_intent
    from wrs_agent.schemas import TextInput

    del captured
    kind = classify_desk_command(heard)
    if kind == "ask":
        sentence = scene or "还没有识别结果。"
        system.action("speak", text=sentence)
        return sentence, sentence
    if kind == "control":
        text = normalize(heard)
        if text_intent(TextInput(text=text))[0] == "stop":
            _send_fafu({"cmd": "stop"})
        receipt = system.send_text(text)
        return f"控制：{receipt.phase}", None
    if kind == "move":
        payload, speech = fafu_payload(heard)
        system.action("speak", text=speech)
        _send_fafu(payload)
        return speech, None
    return "这句话没有对应动作。", None


def _refresh_scene(vision, board):
    from wrs_agent.nodes.vision.announce import desktop_utterance

    if not FRAME.is_file():
        return
    try:
        status = vision.action("detect", image=str(FRAME)).wait(timeout=2)
    except Exception as exc:
        with board["lock"]:
            board["detect_error"] = str(exc)
        return
    if status.state != "SUCCEEDED":
        return
    items = vision.snapshot("yolo").data.items
    sentence = desktop_utterance(items)
    _write_overlay(sentence, _box_rows(items))
    with board["lock"]:
        board["scene"] = sentence


def _drain(system, board, audio):
    while True:
        try:
            heard = audio.get_nowait()
        except queue.Empty:
            return
        with board["lock"]:
            captured = board["captured"]
            scene = board["scene"]
            board["captured"] = None
            board["heard"] = heard or "（没有听清）"
        if not heard:
            with board["lock"]:
                board["status"] = "没有听清，请按住再说一遍。"
            continue
        try:
            status, sentence = _apply(system, heard, captured, scene)
        except Exception as exc:
            status, sentence = f"没有执行：{exc}", None
        with board["lock"]:
            board["status"] = status
            if sentence is not None:
                board["sentence"] = sentence
        print(heard, status, flush=True)


def main():
    sys.path.insert(0, str(ROOT))
    if not CAMERA_PYTHON.is_file():
        raise SystemExit(f"找不到相机解释器：{CAMERA_PYTHON}")
    if not ASR_PYTHON.is_file():
        raise SystemExit(f"找不到语音识别解释器：{ASR_PYTHON}")
    if not FAFU_PYTHON.is_file():
        raise SystemExit(f"找不到真机解释器：{FAFU_PYTHON}")
    from wrs_agent import connect
    from wrs_agent.env.viewer import viewer_hub
    from wrs_agent.env.wrs import load_wrs, sync_scene_objects

    LIVE.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.setdefault("DISPLAY", ":0")
    camera = listener = fafu = server = world = None
    try:
        camera_script = ROOT / "examples/nodes/10_live_announce.py"
        camera = subprocess.Popen(
            [str(CAMERA_PYTHON), str(camera_script), "--camera-headless"],
            env=environment,
        )
        listener = subprocess.Popen(
            [str(ASR_PYTHON), str(Path(__file__).resolve()), "--listen"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        fafu = subprocess.Popen(
            [str(FAFU_PYTHON), str(ROOT / "examples/voice/fafu_voice_arm.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        board = {
            "lock": threading.Lock(),
            "status": "正在加载语音识别……",
            "heard": "",
            "sentence": "",
            "scene": "",
            "captured": None,
            "stop": False,
        }
        audio = queue.Queue()
        CONTEXT["board"] = board
        CONTEXT["listener"] = listener
        CONTEXT["audio"] = audio
        CONTEXT["fafu"] = fafu
        server = ThreadingHTTPServer(("127.0.0.1", PAGE_PORT), PageHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=_read_audio, args=(listener, audio, board), daemon=True).start()
        threading.Thread(target=_read_fafu, args=(fafu, board), daemon=True).start()
        wrs = load_wrs()
        world = wrs.wvw.World(
            cam_pos=(1.8, 1.4, 1.2),
            cam_lookat_pos=(0.3, 0, 0.35),
            port=VIEW_PORT,
            hz=10,
            auto_start_hub=False,
        )
        world.set_caption("WRS UR7E — 机械臂和桌面")
        robot, gripper = wrs.ur_ur7e.ur7e_with_gripper()
        robot.add_to_scene(world.scene)
        wrs.wssop.frame().add_to_scene(world.scene)
        _token("wrs")
        arm_endpoint, arm_env = ARM
        with connect(
            arm_endpoint, env_id=arm_env, bindings=ROOT / "examples/voice/wrs_bindings.toml",
        ) as arm:
            initial = arm.snapshot(node="wrs")
            if initial.data.robot.kinematics is None or not initial.data.robot.kinematics.valid:
                raise RuntimeError("WRS 节点没有有效的关节状态。")
            robot.fk(initial.data.robot.kinematics.qs)
            gripper.set_opening(initial.data.robot.kinematics.gripper_width)
            displayed = {}
            sync_scene_objects(wrs, world.scene, initial.data.objects, displayed)
            _token("nodes")
            vision_endpoint, vision_env = VISION
            with connect(vision_endpoint, env_id=vision_env) as vision:
                def update(dt):
                    snapshot = arm.snapshot(node="wrs")
                    if snapshot.boot_id != initial.boot_id:
                        raise RuntimeError("WRS 节点已重启，请重新打开页面。")
                    state = snapshot.data.robot.kinematics
                    if state is None or not state.valid:
                        raise RuntimeError("WRS 关节状态无法确认，停止显示。")
                    robot.fk(state.qs)
                    gripper.set_opening(state.gripper_width)
                    sync_scene_objects(wrs, world.scene, snapshot.data.objects, displayed)
                    with board["lock"]:
                        if board["status"].startswith("正在录音") and board["captured"] is None:
                            board["captured"] = snapshot
                        stopping = board["stop"]
                        board["stop"] = False
                    if stopping:
                        try:
                            _send_fafu({"cmd": "stop"})
                            arm_note = "已向真机发急停"
                        except Exception as exc:
                            arm_note = f"真机急停没发出：{exc}"
                        receipt = arm.send_text("停止")
                        with board["lock"]:
                            board["status"] = f"{arm_note}；播报：{receipt.phase}"
                    _refresh_scene(vision, board)
                    _drain(arm, board, audio)

                world.schedule_interval(update, 0.1)
                print(f"打开 http://127.0.0.1:{PAGE_PORT} ，Ctrl+C 退出。", flush=True)
                with viewer_hub(VIEW_PORT):
                    world.run()
    finally:
        if server is not None:
            server.shutdown()
        if world is not None:
            world.close()
        if camera is not None and camera.poll() is None:
            camera.terminate()
            camera.wait(timeout=5)
        if listener is not None and listener.poll() is None:
            listener.terminate()
            listener.wait(timeout=5)
        if fafu is not None and fafu.poll() is None:
            try:
                fafu.stdin.write('{"cmd":"shutdown"}\n')
                fafu.stdin.flush()
                fafu.wait(timeout=4)
            except Exception:
                fafu.terminate()
                fafu.wait(timeout=3)


if __name__ == "__main__":
    if "--listen" in sys.argv:
        listen_main()
    else:
        try:
            main()
        except KeyboardInterrupt:
            print("页面已关闭；远端动作未自动取消。")
