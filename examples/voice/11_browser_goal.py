"""在打开页面的那台电脑上按住说话，识别和 UR7e 仍在运行 07 的机器上。

先运行 examples/voice/07_start_llm_voice.py 和 examples/voice/09_viewer.py。
用 qwen-asr 环境启动本文件。自己的电脑打开打印出的 https://<地址>:8443 ，
首次要信任 .local/remote-mic 里的证书。浏览器只在已信任的 HTTPS 页面里提供麦克风。
"""

import asyncio
import json
import socket
import ssl
import subprocess
import time
from pathlib import Path

import numpy as np

from examples._session import use_local_token
from examples.voice.commands import normalize
from wrs_agent import System
from wrs_agent.policy import text_intent
from wrs_agent.schemas import TERMINAL, TextInput
from wrs_agent.nodes.asr.qwen import QwenASR

CONFIG = Path(__file__).with_name("wrs_bindings.toml")
CERT_DIR = Path(__file__).resolve().parents[2] / ".local/remote-mic"
VIEWER = ("127.0.0.1", 8001)
HTTPS_PORT = 8443
RATE = 16000
MAX_SECONDS = 12.0
MIN_SECONDS = 0.2
CANCEL_SECONDS = 5.0
VOCABULARY = ["停止", "停止播报", "状态", "向前", "向后", "向左", "向右", "向上", "向下", "移动", "夹爪"]

PANEL = """
<style>
  .wrs-ui-panel .ui-hold.own-held,
  .wrs-ui-panel .ui-hold.own-held:hover {
    background: #276555; border-color: #1e5144; color: #f2fbf6;
  }
  #wrs-own-mic { margin: 4px 16px 8px; color: #263d39; font: 13px/1.45 sans-serif; }
  #wrs-own-mic p { margin: 0 0 6px; }
  #wrs-own-status { font-weight: 600; }
</style>
<script>
const RATE = 16000;
const MAX_SAMPLES = RATE * 12;
let audioCtx, source, processor, stream, chunks, button;
let held = false, arming = false, stopRequested = false;
function statusEl() { return document.getElementById("wrs-own-status"); }
function heardEl() { return document.getElementById("wrs-own-heard"); }
function setStatus(text) {
  const node = statusEl();
  if (node) node.textContent = text;
}
function voicePanel() {
  for (const panel of document.querySelectorAll(".wrs-ui-panel")) {
    const title = panel.querySelector(".ui-title");
    if (title && title.textContent === "语音指令") return panel;
  }
  return null;
}
function talkButton(panel) {
  for (const item of panel.querySelectorAll("button.ui-hold")) {
    if (item.textContent.startsWith("按住说话")) return item;
  }
  return null;
}
function ensureHost(panel) {
  if (panel.querySelector("#wrs-own-mic")) return;
  const host = document.createElement("div");
  host.id = "wrs-own-mic";
  host.innerHTML = "<p>按住上方“按住说话”，用的是打开这个页面的电脑。</p>"
    + "<p id=\\"wrs-own-status\\">待命</p><p id=\\"wrs-own-heard\\">最近识别：（无）</p>";
  (panel.querySelector(".ui-body") || panel).appendChild(host);
}
function markHeld(on) {
  if (!button) return;
  button.classList.toggle("own-held", on);
  button.dataset.held = on ? "true" : "false";
  button.setAttribute("aria-pressed", on ? "true" : "false");
}
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
function fail(error) {
  held = false;
  arming = false;
  stopRequested = true;
  markHeld(false);
  if (stream) stream.getTracks().forEach((track) => track.stop());
  setStatus("无法使用这台电脑的麦克风：" + (error && error.message ? error.message : error));
}
async function begin(event) {
  if (held || arming || !button) return;
  arming = true;
  stopRequested = false;
  markHeld(true);
  chunks = [];
  setStatus("正在听这台电脑…");
  if (event.pointerId !== undefined) {
    try { button.setPointerCapture(event.pointerId); } catch (error) { /* already released */ }
  }
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true }, video: false,
  });
  if (stopRequested) {
    arming = false;
    stream.getTracks().forEach((track) => track.stop());
    markHeld(false);
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
  arming = false;
  if (stopRequested) finish();
}
async function finish() {
  stopRequested = true;
  if (!held) return;
  held = false;
  markHeld(false);
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
    setStatus("没有完整短句，没有发送。请按住并说完一句。");
    return;
  }
  setStatus("正在识别…");
  const response = await fetch("/utterance", {
    method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: audio.buffer,
  });
  const body = await response.json();
  const heard = heardEl();
  if (heard) heard.textContent = "最近识别：" + (body.text || "（无）");
  setStatus(body.message || "没有结果");
}
function takeOver(event) {
  event.stopImmediatePropagation();
}
function bind(item) {
  if (item.dataset.ownMic === "1") return;
  item.dataset.ownMic = "1";
  button = item;
  item.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !event.isPrimary) return;
    takeOver(event);
    begin(event).catch(fail);
  }, true);
  item.addEventListener("pointerup", (event) => {
    takeOver(event);
    finish().catch((error) => setStatus(error.message));
  }, true);
  item.addEventListener("pointercancel", (event) => {
    takeOver(event);
    finish();
  }, true);
  item.addEventListener("keydown", (event) => {
    if (event.key !== " " && event.key !== "Enter") return;
    takeOver(event);
    event.preventDefault();
    if (!event.repeat) begin(event).catch(fail);
  }, true);
  item.addEventListener("keyup", (event) => {
    if (event.key !== " " && event.key !== "Enter") return;
    takeOver(event);
    event.preventDefault();
    finish().catch((error) => setStatus(error.message));
  }, true);
}
function attach() {
  const panel = voicePanel();
  if (!panel) return;
  ensureHost(panel);
  const item = talkButton(panel);
  if (item) bind(item);
}
new MutationObserver(attach).observe(document.documentElement, { childList: true, subtree: true });
attach();
</script>
"""


def _headers(blob):
    text = blob.decode("latin1", errors="replace")
    lines = text.split("\r\n")
    fields = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        fields[name.strip().lower()] = value.strip()
    return lines[0], fields


def _rewrite(head, *, websocket):
    """Replace Host. A second Upgrade header makes the viewer reject the socket."""
    lines = head.decode("latin1").split("\r\n")
    kept = [lines[0]]
    for line in lines[1:]:
        if not line:
            continue
        name = line.split(":", 1)[0].strip().lower()
        if name == "host" or (not websocket and name == "connection"):
            continue
        kept.append(line)
    kept.append(f"Host: {VIEWER[0]}:{VIEWER[1]}")
    if not websocket:
        kept.append("Connection: close")
    return ("\r\n".join(kept) + "\r\n\r\n").encode("latin1")


def _inject(body):
    panel = PANEL.encode("utf-8")
    if b"</body>" in body:
        return body.replace(b"</body>", panel + b"</body>", 1)
    return body + panel


async def _read_headers(reader):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = await reader.read(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    head, marker, rest = data.partition(b"\r\n\r\n")
    return head, rest if marker else b""


async def _read_body(reader, headers, rest):
    length = int(headers.get("content-length", "0") or 0)
    while len(rest) < length:
        chunk = await reader.read(length - len(rest))
        if not chunk:
            break
        rest += chunk
    return rest[:length]


class BrowserMic:
    def __init__(self, system, recognizer):
        self.system, self.recognizer = system, recognizer
        self.lock = asyncio.Lock()

    async def accept(self, body):
        if len(body) % 4 or not 0 < len(body) <= int(RATE * MAX_SECONDS) * 4:
            return {"ok": False, "text": "", "message": "音频长度无效，没有执行动作。"}
        audio = np.frombuffer(body, dtype="<f4")
        if not np.isfinite(audio).all() or audio.size < RATE * MIN_SECONDS:
            return {"ok": False, "text": "", "message": "没有完整短句，没有执行动作。"}
        async with self.lock:
            transcript = await asyncio.to_thread(self.recognizer.transcribe, audio)
            message = await apply_goal(self.system, transcript.text)
            print(f"识别：{transcript.text}；{message}", flush=True)
            return {"ok": True, "text": transcript.text, "message": message}

    async def handle(self, reader, writer):
        try:
            head, rest = await _read_headers(reader)
            if not head:
                return
            request, headers = _headers(head)
            parts = request.split(" ")
            if len(parts) < 2:
                return
            method, path = parts[0], parts[1].split("?", 1)[0]
            if method == "POST" and path == "/utterance":
                body = await _read_body(reader, headers, rest)
                result = await self.accept(body)
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                header = (
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n"
                    f"Content-Length: {len(payload)}\r\nCache-Control: no-store\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                writer.write(header + payload)
                await writer.drain()
                return
            upgrade = headers.get("upgrade", "").lower() == "websocket"
            await self._proxy(reader, writer, head, rest, path, upgrade)
        except Exception as exc:
            print(f"[browser-mic] {exc.__class__.__name__}", flush=True)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _proxy(self, reader, writer, head, rest, path, upgrade):
        try:
            remote_reader, remote_writer = await asyncio.open_connection(*VIEWER)
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        remote_writer.write(_rewrite(head, websocket=upgrade) + rest)
        await remote_writer.drain()
        if upgrade:
            await asyncio.gather(
                _pipe(reader, remote_writer), _pipe(remote_reader, writer),
            )
            return
        response, pending = await _read_headers(remote_reader)
        status, fields = _headers(response)
        length = int(fields.get("content-length", "0") or 0)
        body = pending
        while len(body) < length:
            chunk = await remote_reader.read(min(65536, length - len(body)))
            if not chunk:
                break
            body += chunk
        if path in {"/", "/index.html"} and b"text/html" in fields.get("content-type", "").encode():
            body = _inject(body)
        header = _response_header(status, fields, len(body))
        writer.write(header + body)
        await writer.drain()
        remote_writer.close()


def _response_header(status, fields, length):
    lines = [status]
    for name, value in fields.items():
        if name in {"content-length", "transfer-encoding", "connection"}:
            continue
        lines.append(f"{name}: {value}")
    lines.append(f"Content-Length: {length}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin1")


async def _pipe(source, destination):
    try:
        while True:
            chunk = await source.read(65536)
            if not chunk:
                break
            destination.write(chunk)
            await destination.drain()
    except Exception:
        pass
    finally:
        destination.close()


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
    found = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return sorted(found)


def _busy(overview):
    return overview.get("planning") == "WAITING" or (
        overview.get("task_id") and overview.get("state") not in TERMINAL
    )


async def apply_goal(system, text):
    """Same admission as 09_viewer: a full sentence becomes a goal, stop stays a stop."""
    text = normalize(text)
    if not text:
        return "没有识别到文字，没有执行动作。"
    if text_intent(TextInput(text=text))[0] in {"stop", "cancel_tts", "query"}:
        receipt = await system.send_text(text)
        detail = f"{receipt.disposition}，受理 {receipt.accepted}"
        if receipt.phase:
            detail += f"，{receipt.phase}"
        return detail
    current = await system.snapshot(node="wrs")
    if current.admission == "UNKNOWN" or not current.stop_confirmed:
        return "机器人状态未确认，拒绝新目标。"
    overview = await system.status()
    if overview.get("state") == "UNKNOWN":
        return "上一次任务结果无法确认；请先说“停止”。"
    if _busy(overview):
        receipt = await system.send_text("停止")
        if not receipt.accepted:
            return f"未能停下上一条（{receipt.phase}），新指令未提交。"
        deadline = time.monotonic() + CANCEL_SECONDS
        while time.monotonic() < deadline and _busy(await system.status()):
            await asyncio.sleep(0.2)
        overview = await system.status()
        if overview.get("state") == "UNKNOWN" or _busy(overview):
            return "上一条没有确认停下，新指令已放弃。"
        current = await system.snapshot(node="wrs")
    if current.admission == "HELD":
        receipt = await system.allow_actions(node="wrs")
        if not receipt.accepted:
            return f"未能允许新任务：{receipt.reason}"
    receipt = await system.send_text(text)
    if receipt.accepted:
        return f"目标已受理：{receipt.request_id or ''}".strip()
    return f"目标未受理：{receipt.reason or receipt.disposition}"


async def main():
    print("加载 Qwen3-ASR，用来识别你电脑上传来的语音……", flush=True)
    recognizer = await asyncio.to_thread(QwenASR, vocabulary=VOCABULARY)
    await asyncio.to_thread(recognizer.warmup)
    async with System.connect(
        "tcp/127.0.0.1:7451", env_id="voice-goal", bindings=CONFIG,
    ) as system:
        page = BrowserMic(system, recognizer)
        addresses = local_addresses()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        certificate, key = ensure_certificate(addresses)
        context.load_cert_chain(certificate, key)
        server = await asyncio.start_server(page.handle, "0.0.0.0", HTTPS_PORT, ssl=context)
        print("在你自己的电脑打开下面的地址，按住右上角“按住说话”。", flush=True)
        for address in addresses:
            print(f"https://{address}:{HTTPS_PORT}", flush=True)
        print("浏览器提示证书不受信任时，选择继续访问，麦克风才会可用。", flush=True)
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    use_local_token("voice")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("已关闭本机麦克风页面；远端任务未自动取消。")
