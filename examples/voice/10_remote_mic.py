"""远程按住说话：麦克风在打开页面的设备上，动作发给 HTDW-4530-02-DNE。

先运行 05_start_wrs_voice.py。用 qwen-asr 环境启动本文件。
本机或 SSH 端口转发打开 http://127.0.0.1:8010 （浏览器把这当成本机，可以直接用麦克风）。
同一局域网的其他设备打开 https://<本机地址>:8443 ，首次需信任 .local/remote-mic 里的证书。
Zenoh 仍只在 127.0.0.1。页面只接收一段 16 kHz 单声道浮点音频，不把音频送进控制总线。
"""

import asyncio
import json
import os
import queue
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from examples._session import use_local_token
from examples.voice.commands import COMMANDS, fafu_payload, normalize, parse_centimeter_move
from wrs_agent import System
from wrs_agent.nodes.vision.announce import asks_what_is_there, desktop_utterance
from wrs_agent.nodes.vision.speech import grasp_prompt, object_name
from wrs_agent.policy import text_intent
from wrs_agent.schemas import TextInput
from wrs_agent.nodes.asr.qwen import QwenASR

CONFIG = Path(__file__).with_name("wrs_bindings.toml")
ROOT = Path(__file__).resolve().parents[2]
CERT_DIR = ROOT / ".local/remote-mic"
FRAME = Path("/tmp/wrs-live/frame.jpg")
DEPTH = Path("/tmp/wrs-live/depth.npy")
CAMERA = Path("/tmp/wrs-live/camera.json")
CLOUD = Path("/tmp/wrs-live/grasp-cloud.npy")
PREVIEW = Path("/tmp/wrs-live/preview.jpg")
OVERLAY = Path("/tmp/wrs-live/overlay.json")
ASKS = ("有什么", "有哪些", "有啥")
FAFU_PYTHON = Path.home() / "miniconda3/envs/panthera/bin/python"
FAFU_SCRIPT = ROOT / "examples/voice/fafu_voice_arm.py"
HTTP_PORT = 8010
HTTPS_PORT = 8443
RATE = 16000
MAX_SECONDS = 4.0
MIN_SECONDS = 0.2

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>远程语音控制</title>
<style>
  body { font-family: sans-serif; margin: 24px; background: #f2f2f0; color: #222; }
  button { font-size: 28px; padding: 28px 18px; width: 100%; max-width: 420px;
           border: 0; border-radius: 12px; background: #167464; color: white; }
  button.held { background: #b45309; }
  p { max-width: 42rem; line-height: 1.5; }
  #status { font-weight: 600; }
</style>
</head>
<body>
<h1>远程语音控制</h1>
<p>按住按钮说话，松开后识别。麦克风和播报都用打开这个页面的电脑。说「向上5厘米」按厘米数移动。说「抓住杯子」会在这块深度里计算抓取点。问「有什么」会读出画面里的物体。</p>
<img id="camera" alt="相机" src="/preview.jpg" style="max-width:640px;width:100%;background:#ddd" />
<button id="talk" type="button">按住说话</button>
<p id="status">待命</p>
<p id="heard">最近识别：（无）</p>
<p id="words"></p>
<script>
const button = document.getElementById("talk");
const statusEl = document.getElementById("status");
const heardEl = document.getElementById("heard");
const words = document.getElementById("words");
const RATE = 16000;
const MAX_SAMPLES = RATE * 4;
let audioCtx, source, processor, stream, chunks, held = false, stopRequested = false;

function setStatus(text) { statusEl.textContent = text; }

function resample(input, fromRate) {
  const count = Math.round(input.length * RATE / fromRate);
  const out = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const x = i * fromRate / RATE;
    const j = Math.floor(x);
    const t = x - j;
    const a = input[Math.min(j, input.length - 1)];
    const b = input[Math.min(j + 1, input.length - 1)];
    out[i] = a + (b - a) * t;
  }
  return out;
}

function prepareSpeech() {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.resume();
  const warm = new SpeechSynthesisUtterance("");
  warm.volume = 0;
  window.speechSynthesis.speak(warm);
}

function say(text) {
  if (!window.speechSynthesis || !text) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = "zh-CN";
  const voice = window.speechSynthesis.getVoices().find((item) => /^zh/i.test(item.lang || ""));
  if (voice) utter.voice = voice;
  window.speechSynthesis.speak(utter);
}

async function begin(event) {
  event.preventDefault();
  if (held) return;
  prepareSpeech();
  stopRequested = false;
  button.classList.add("held");
  chunks = [];
  setStatus("正在听…");
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true }, video: false,
  });
  if (stopRequested) {
    stream.getTracks().forEach((track) => track.stop());
    button.classList.remove("held");
    setStatus("没有完整短句，没有发送。");
    return;
  }
  audioCtx = new AudioContext();
  source = audioCtx.createMediaStreamSource(stream);
  processor = audioCtx.createScriptProcessor(4096, 1, 1);
  processor.onaudioprocess = (item) => {
    if (held) chunks.push(new Float32Array(item.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  processor.connect(audioCtx.destination);
  held = true;
  button.setPointerCapture(event.pointerId);
  if (stopRequested) finish();
}

async function finish() {
  stopRequested = true;
  if (!held) return;
  held = false;
  button.classList.remove("held");
  processor.disconnect();
  source.disconnect();
  stream.getTracks().forEach((track) => track.stop());
  const rate = audioCtx.sampleRate;
  await audioCtx.close();
  const length = chunks.reduce((sum, item) => sum + item.length, 0);
  const joined = new Float32Array(length);
  let offset = 0;
  chunks.forEach((item) => { joined.set(item, offset); offset += item.length; });
  const audio = resample(joined, rate);
  if (audio.length > MAX_SAMPLES || audio.length < RATE * 0.2) {
    setStatus("没有完整短句，没有发送。请按住并说完一个指令。");
    return;
  }
  setStatus("正在识别…");
  const response = await fetch("/utterance", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: audio.buffer,
  });
  const body = await response.json();
  heardEl.textContent = "最近识别：" + (body.text || "（无）");
  setStatus(body.message || "没有结果");
  say(body.speech || "");
}

button.addEventListener("pointerdown", (event) => {
  begin(event).catch((error) => {
    held = false;
    button.classList.remove("held");
    setStatus("无法使用麦克风：" + error.message);
  });
});
button.addEventListener("pointerup", () => finish().catch((error) => setStatus(error.message)));
button.addEventListener("pointercancel", () => finish());
fetch("/commands").then((response) => response.json()).then((names) => {
  words.textContent = "指令：" + names.join("、");
});
setInterval(() => {
  const camera = document.getElementById("camera");
  if (camera) camera.src = "/preview.jpg?" + Date.now();
}, 500);
</script>
</body>
</html>
"""


def ensure_certificate(addresses):
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    certificate, key = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    names = ",".join(
        ["DNS:localhost", *[f"IP:{item}" for item in ("127.0.0.1", *addresses)]]
    )
    stamp = CERT_DIR / "names.txt"
    if certificate.is_file() and key.is_file() and stamp.is_file() and stamp.read_text() == names:
        return certificate, key
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(certificate), "-days", "365",
            "-subj", "/CN=wrs-remote-mic", "-addext", f"subjectAltName={names}",
        ],
        check=True,
        capture_output=True,
    )
    stamp.write_text(names)
    return certificate, key


def local_addresses():
    import socket

    found = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return sorted(found)


def _group_token(group):
    return (ROOT / ".local/example_tokens" / f"{group}.token").read_text(encoding="ascii").strip()


def _write_overlay(sentence, items):
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
    OVERLAY.parent.mkdir(parents=True, exist_ok=True)
    temporary = OVERLAY.with_suffix(".tmp.json")
    temporary.write_text(
        json.dumps({"sentence": sentence, "items": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, OVERLAY)


class RealArm:
    """One JSON line in, one JSON line out. The SDK process owns the serial port."""

    def __init__(self):
        self.proc = None
        self.replies = queue.Queue()

    def start(self):
        if not FAFU_PYTHON.is_file():
            raise RuntimeError(f"找不到真机解释器：{FAFU_PYTHON}")
        self.proc = subprocess.Popen(
            [str(FAFU_PYTHON), str(FAFU_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()
        try:
            ready = self.replies.get(timeout=40)
        except queue.Empty as exc:
            self.close()
            raise RuntimeError("真机连接超时。") from exc
        if not ready.get("ready"):
            self.close()
            raise RuntimeError(str(ready.get("error") or "真机没连上"))
        return ready

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def command(self, payload, timeout):
        if not self.alive():
            raise RuntimeError("真机进程不在。")
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        return self.replies.get(timeout=timeout)

    def close(self):
        if not self.alive():
            return
        try:
            self.proc.stdin.write('{"cmd":"shutdown"}\n')
            self.proc.stdin.flush()
            self.proc.wait(timeout=4)
        except Exception:
            self.proc.terminate()

    def _read(self):
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.replies.put(message)


class RemoteMic:
    def __init__(self, loop, system, vision, recognizer, arm):
        self.loop, self.system, self.vision = loop, system, vision
        self.recognizer, self.arm = recognizer, arm
        self.lock = asyncio.Lock()

    def serve(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                print(f"[remote-mic] {self.address_string()} {fmt % args}", flush=True)

            def _send(self, status, body, content_type):
                data = body if isinstance(body, bytes) else body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/commands":
                    names = [*COMMANDS, *ASKS, "停止", "停止播报", "状态"]
                    self._send(200, json.dumps(names, ensure_ascii=False), "application/json")
                    return
                if path == "/preview.jpg":
                    try:
                        payload = PREVIEW.read_bytes()
                    except OSError:
                        self._send(404, "no preview", "text/plain; charset=utf-8")
                        return
                    self._send(200, payload, "image/jpeg")
                    return
                if path not in {"/", "/index.html"}:
                    self._send(404, "not found", "text/plain; charset=utf-8")
                    return
                self._send(200, PAGE, "text/html; charset=utf-8")

            def do_POST(self):
                if self.path.split("?", 1)[0] != "/utterance":
                    self._send(404, "not found", "text/plain; charset=utf-8")
                    return
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if length else b""
                future = asyncio.run_coroutine_threadsafe(owner.accept(body), owner.loop)
                try:
                    result = future.result(timeout=60)
                    status = 200
                except Exception as exc:
                    result = {"ok": False, "text": "", "message": f"识别失败：{exc.__class__.__name__}"}
                    status = 500
                self._send(status, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")

        def launch(server):
            thread = threading.Thread(target=server.serve_forever, name="remote-mic", daemon=True)
            thread.start()
            return server

        http = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler)
        addresses = local_addresses()
        certificate, key = ensure_certificate(addresses)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        https = ThreadingHTTPServer(("0.0.0.0", HTTPS_PORT), Handler)
        https.socket = context.wrap_socket(https.socket, server_side=True)
        return launch(http), launch(https), addresses

    async def accept(self, body):
        if len(body) % 4 or not 0 < len(body) <= int(RATE * MAX_SECONDS) * 4:
            return {"ok": False, "text": "", "message": "音频长度无效，没有执行动作。"}
        audio = np.frombuffer(body, dtype="<f4")
        if not np.isfinite(audio).all() or audio.size < RATE * MIN_SECONDS:
            return {"ok": False, "text": "", "message": "没有完整短句，没有执行动作。"}
        async with self.lock:
            captured = await self.system.snapshot(node="wrs")
            transcript = await asyncio.to_thread(self.recognizer.transcribe, audio)
            message, speech = await apply_command(self, transcript.text, captured)
            print(f"识别：{transcript.text}；{message}", flush=True)
            return {"ok": True, "text": transcript.text, "message": message, "speech": speech}


def _cloud_from_box(camera, box):
    import numpy as np

    depth = np.load(DEPTH, allow_pickle=False)
    height, width = depth.shape[:2]
    x1, y1, x2, y2 = (int(value) for value in box)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    fx, fy = float(camera["fx"]), float(camera["fy"])
    cx, cy = float(camera["cx"]), float(camera["cy"])
    points = []
    for row in range(y1, y2, 2):
        for col in range(x1, x2, 2):
            z = float(depth[row, col])
            if 0.05 < z < 1.5:
                points.append(((col - cx) * z / fx, (row - cy) * z / fy, z))
    if len(points) < 16:
        return None
    np.save(CLOUD, np.asarray(points, dtype="float32"))
    return CLOUD


def _grasp_sentence(prompt, item, source):
    name = object_name(prompt)
    translation = getattr(item, "translation", None)
    if not translation or len(translation) != 3:
        return f"找到了{name}，但没有可用的抓取点。机械臂没有移动。"
    x, y, z = (float(value) for value in translation)
    width = getattr(item, "width", None)
    opening = f"，张开{float(width):.2f}米" if width is not None else ""
    return (
        f"{source}在相机坐标系给出{name}的抓取点，"
        f"x{x:.2f}米，y{y:.2f}米，z{z:.2f}米{opening}。"
        "还没有手眼标定，机械臂没有移动。"
    )


async def grasp_object(remote, prompt):
    """Find the named region, segment its depth, then score one grasp."""
    if not FRAME.is_file() or not DEPTH.is_file() or not CAMERA.is_file():
        return "相机还没有对齐的深度图。", ""
    segment = await remote.vision.action("segment", image=str(FRAME), text=prompt)
    found = await segment.wait(timeout=180)
    if found.state != "SUCCEEDED":
        return f"没有分割出「{prompt}」。", ""
    items = (await remote.vision.snapshot(node="sam3")).data.items
    chosen = None
    score = -1.0
    for item in items:
        box = getattr(item, "xyxy", None)
        value = float(getattr(item, "score", 0) or 0)
        if box and len(box) == 4 and (chosen is None or value > score):
            chosen, score = item, value
    if chosen is None:
        return f"画面里没有「{object_name(prompt)}」。机械臂没有移动。", ""
    box = [float(value) for value in chosen.xyxy]
    _write_overlay(f"目标：{object_name(prompt)}", [chosen])
    camera = json.loads(CAMERA.read_text(encoding="utf-8"))
    scored = await remote.vision.action(
        "graspability",
        depth=str(DEPTH),
        x1=box[0],
        y1=box[1],
        x2=box[2],
        y2=box[3],
        fx=float(camera["fx"]),
        fy=float(camera["fy"]),
        cx=float(camera["cx"]),
        cy=float(camera["cy"]),
        top=3,
    )
    result = await scored.wait(timeout=60)
    grasps = []
    source = "graspability"
    if result.state == "SUCCEEDED":
        grasps = list((await remote.vision.snapshot(node="graspability")).data.items)
    if not grasps:
        cloud = _cloud_from_box(camera, box)
        if cloud is None:
            return f"这块深度里没有可抓的表面。机械臂没有移动。", ""
        backup = await remote.vision.action("infer_grasps", cloud=str(cloud), top=3)
        backup_result = await backup.wait(timeout=180)
        if backup_result.state != "SUCCEEDED":
            return "这块深度里没有可用的抓取。机械臂没有移动。", ""
        grasps = list((await remote.vision.snapshot(node="graspnet")).data.items)
        source = "GraspNet"
    if not grasps:
        return "这块深度里没有可用的抓取。机械臂没有移动。", ""
    sentence = _grasp_sentence(prompt, grasps[0], source)
    return sentence, sentence


async def describe_scene(remote):
    """Detect the latest camera frame. The browser speaks the sentence."""
    if not FRAME.is_file():
        return "相机还没有画面。", ""
    action = await remote.vision.action("detect", image=str(FRAME))
    result = await action.wait(timeout=180)
    if result.state != "SUCCEEDED":
        return f"识别没有完成：{result.state}", ""
    items = (await remote.vision.snapshot(node="yolo")).data.items
    sentence = desktop_utterance(items)
    _write_overlay(sentence, items)
    try:
        spoken = await (await remote.system.action("speak", text=sentence)).wait(timeout=60)
        if spoken.state != "SUCCEEDED":
            print(f"机器人端播报{spoken.state}", flush=True)
    except Exception as exc:
        print(f"机器人端播报未发出：{exc.__class__.__name__}", flush=True)
    return sentence, sentence


async def stop_real_arm(remote):
    if remote.arm is None or not remote.arm.alive():
        return "真机没连上，急停没有发出。"
    try:
        result = await asyncio.to_thread(remote.arm.command, {"cmd": "stop"}, 8)
    except Exception as exc:
        return f"真机急停没发出：{exc}"
    return str(result.get("status") or "已急停。")


async def move_real_arm(remote, text):
    if remote.arm is None or not remote.arm.alive():
        return "真机没连上，没有执行动作。", ""
    payload, speech = fafu_payload(text)
    timeout = 90
    if payload.get("cmd") == "nudge":
        meters = max(abs(payload["dx"]), abs(payload["dy"]), abs(payload["dz"]))
        timeout = max(90, int(meters * 300))
    try:
        result = await asyncio.to_thread(remote.arm.command, payload, timeout)
    except Exception as exc:
        return f"真机没有执行：{exc}", ""
    status = str(result.get("status") or speech)
    if not result.get("ok"):
        return status, ""
    return status, speech


async def apply_command(remote, text, captured):
    """Same admission rules as 06_push_to_talk.dispatch; returns the sentence shown on the page."""
    system = remote.system
    del captured
    text = normalize(text)
    if not text:
        return "没有识别到文字，没有执行动作。", ""
    intent, _ = text_intent(TextInput(text=text))
    prompt = grasp_prompt(text)
    if prompt and intent not in {"stop", "cancel_tts", "query"}:
        return await grasp_object(remote, prompt)
    if asks_what_is_there(text) and intent not in {"stop", "cancel_tts", "query"}:
        return await describe_scene(remote)
    if intent == "stop":
        return await stop_real_arm(remote), ""
    if intent in {"cancel_tts", "query"}:
        receipt = await system.send_text(text)
        detail = f"控制结果：{receipt.disposition}，受理 {receipt.accepted}"
        if receipt.phase:
            detail += f"，阶段 {receipt.phase}"
        return detail, ""
    if parse_centimeter_move(text) is not None or (
        text in COMMANDS and COMMANDS[text][0] != "move_relative"
    ):
        return await move_real_arm(remote, text)
    if text in COMMANDS:
        return "请说移动多少厘米，例如：向上5厘米。", ""
    return "未匹配完整指令，没有执行动作。支持：" + "、".join([*COMMANDS, "停止", "状态"]), ""


async def main():
    print("连接 HTDW-4530-02-DNE ……", flush=True)
    arm = RealArm()
    try:
        ready = await asyncio.to_thread(arm.start)
        print(f"真机已连接：{ready.get('port')} {ready.get('state')}", flush=True)
    except Exception as exc:
        print(f"真机没连上：{exc}", flush=True)
        arm = None
    print("加载 Qwen3-ASR 0.6B 并预热，语言固定为中文……", flush=True)
    recognizer = await asyncio.to_thread(
        QwenASR, vocabulary=[
            *COMMANDS, *ASKS, "停止", "停止播报", "状态", "厘米", "移动",
            "抓住", "抓起", "拿起", "杯子", "瓶子", "苹果",
            "一", "二", "两", "三", "四", "五", "六", "七", "八", "九", "十",
        ],
    )
    await asyncio.to_thread(recognizer.warmup)
    try:
        async with System.connect(
            "tcp/127.0.0.1:7448", env_id="node-demo", _token=_group_token("nodes"),
        ) as vision:
            async with System.connect(
                "tcp/127.0.0.1:7449", env_id="wrs-demo", bindings=CONFIG,
                _token=_group_token("wrs"),
            ) as system:
                remote = RemoteMic(asyncio.get_running_loop(), system, vision, recognizer, arm)
                _http, _https, addresses = remote.serve()
                print(f"本机或 SSH 转发：http://127.0.0.1:{HTTP_PORT}", flush=True)
                for address in addresses:
                    print(f"局域网：https://{address}:{HTTPS_PORT}", flush=True)
                print("指令：", "、".join([*COMMANDS, *ASKS, "停止", "停止播报", "状态"]), flush=True)
                await asyncio.Event().wait()
    finally:
        if arm is not None:
            await asyncio.to_thread(arm.close)


if __name__ == "__main__":
    use_local_token("wrs")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("已停止远程收音；远端任务未自动取消。")
