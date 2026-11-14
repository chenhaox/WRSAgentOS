"""Loopback HTTP front end; handler threads hand every operation to the node's event loop."""

import asyncio
import json
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wrs_agent.nodes.ui.control import describe

STATIC = Path(__file__).resolve().parent / "static"
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
MAX_BODY = 16 * 1024


class _Server(ThreadingHTTPServer):
    # Windows SO_REUSEADDR would let a second dashboard silently share the port.
    allow_reuse_address = False
    daemon_threads = True


class DashboardServer:
    def __init__(self, dashboard, loop, *, host, port):
        self.dashboard, self.loop = dashboard, loop
        self.httpd = _Server((host, port), self._handler())
        self.port = self.httpd.server_address[1]
        self.url = f"http://{host}:{self.port}/"
        self._thread = None

    def start(self):
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, name="wrs-ui-http", daemon=True,
        )
        self._thread.start()

    async def close(self):
        self.dashboard.hub.close()
        if self._thread is not None:
            await asyncio.to_thread(self.httpd.shutdown)
        self.httpd.server_close()

    def _call(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return 200, future.result(timeout=15)
        except FutureTimeout:
            future.cancel()
            return 504, {"ok": False, "message": "节点没有及时响应。"}
        except Exception as exc:
            return 500, {"ok": False, "message": describe(exc)}

    def _handler(self):
        server = self
        board = self.dashboard
        posts = {
            "/api/open": lambda data: board.open(data.get("mode") or None),
            "/api/text": lambda data: board.send_text(
                str(data.get("text") or ""),
                kind="voice" if data.get("kind") == "voice" else "text",
            ),
            "/api/planner": lambda data: board.configure_planner(data),
            "/api/listen/begin": lambda data: board.listen_begin(),
            "/api/listen/end": lambda data: board.listen_end(),
            "/api/speak": lambda data: board.speak(str(data.get("text") or "")),
            "/api/skill": lambda data: board.run_skill(
                str(data.get("skill") or ""),
                data["args"] if isinstance(data.get("args"), dict) else {},
            ),
            "/api/stop": lambda data: board.stop(),
            "/api/allow": lambda data: board.allow(),
            "/api/mode": lambda data: board.set_mode(str(data.get("mode") or "")),
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _send(self, code, body, content_type):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code, payload):
                body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                self._send(code, body, "application/json; charset=utf-8")

            def _trusted(self):
                # Reject DNS-rebinding hosts; the page itself only ever uses loopback names.
                hosts = {f"{name}:{server.port}" for name in ("127.0.0.1", "localhost")}
                if self.headers.get("Host") not in hosts:
                    self._send(403, b"forbidden host", "text/plain")
                    return False
                return True

            def do_GET(self):
                if not self._trusted():
                    return
                parsed = urlparse(self.path)
                path = parsed.path
                if path in {"/", "/index.html"}:
                    path = "/static/index.html"
                if path.startswith("/static/"):
                    target = STATIC / Path(path).name
                    if target.suffix not in MIME or not target.is_file():
                        self._send(404, b"missing", "text/plain")
                        return
                    self._send(200, target.read_bytes(), MIME[target.suffix])
                elif path == "/api/state":
                    self._json(200, board.state)
                elif path == "/api/events":
                    try:
                        after = int(parse_qs(parsed.query).get("after", ["0"])[0] or 0)
                    except ValueError:
                        after = 0
                    events, last = board.hub.wait(after)
                    self._json(200, {"events": events, "last": last})
                else:
                    self._send(404, b"missing", "text/plain")

            def do_POST(self):
                if not self._trusted():
                    return
                operation = posts.get(urlparse(self.path).path)
                if operation is None:
                    self._send(404, b"missing", "text/plain")
                    return
                # A JSON body forces a CORS preflight, which this server never grants.
                if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                    self._send(415, b"application/json required", "text/plain")
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._send(413, b"too large", "text/plain")
                    return
                try:
                    data = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    self._json(400, {"ok": False, "message": "请求体应为 JSON 对象。"})
                    return
                code, result = server._call(operation(data))
                self._json(code, result)

        return Handler
