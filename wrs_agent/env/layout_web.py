"""Session-only kitchen controls alongside the existing WRS viewer hub."""

import asyncio
import hashlib
import json
import secrets
import threading
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, TypeAdapter, ValidationError

from wrs_agent.layout_editor import editor_state, save_pose
from wrs_agent.layout_session import DEFAULT_SCENE, SESSION_DIRECTORY, current_session
from wrs_agent.schemas import Boundary, Name, Vector3

MESSAGE_LIMIT = 4096
ASSETS = Path(__file__).resolve().parents[1] / "assets/kitchen_editor"
Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Session = Annotated[str, Field(min_length=1, max_length=128)]


class _Get(Boundary):
    type: Literal["get"]


class _Reset(Boundary):
    type: Literal["reset"]
    id: Name
    revision: Revision
    session: Session


class _Set(Boundary):
    type: Literal["set"]
    id: Name
    pos: Vector3
    rpy_deg: Vector3
    revision: Revision
    session: Session


COMMAND = TypeAdapter(Annotated[_Get | _Reset | _Set, Field(discriminator="type")])


class LayoutWebEditor:
    """One viewer's immutable 05 session binding and serialized file operations."""

    def __init__(self, scene, *, session_directory=SESSION_DIRECTORY, default_scene=DEFAULT_SCENE):
        self.scene = Path(scene).resolve()
        self.directory = Path(session_directory).resolve()
        self.session = secrets.token_urlsafe(24)
        self._lock = threading.RLock()
        self._check_session()
        _, self.path, _ = editor_state(self.scene)
        _, _, self.defaults = editor_state(default_scene)

    def _check_session(self):
        if current_session(self.directory) != self.scene:
            raise ValueError("05 已重新启动，请重新启动 07_viewer.py 并刷新页面。")

    def _state(self):
        self._check_session()
        # CLI edits may replace the layout between either of editor_state's reads.
        for _ in range(3):
            before = self.path.read_bytes()
            objects, _, placements = editor_state(self.scene)
            if before == self.path.read_bytes():
                self._check_session()
                return {
                    "type": "state", "session": self.session,
                    "revision": hashlib.sha256(before).hexdigest(),
                    "objects": [
                        {"id": name, "label": obj.label or name,
                         "parent": placements[name].parent,
                         "pos": placements[name].pos, "rpy_deg": placements[name].rpy_deg,
                         "world_pos": obj.pos}
                        for name, obj in objects.items()
                    ],
                }
        raise ValueError("配置正被其他窗口修改，请稍后重试。")

    def state(self):
        with self._lock:
            return self._state()

    def handle(self, message):
        """Exactly one response; no request may switch to another 05 run."""
        with self._lock:
            try:
                if not isinstance(message, str) or len(message.encode("utf-8")) > MESSAGE_LIMIT:
                    raise ValueError("请求必须是 4KB 以内的 JSON 文本。")
                command = COMMAND.validate_json(message)
                state = self._state()
                if isinstance(command, _Get):
                    return state
                if command.session != self.session:
                    raise ValueError("编辑会话已变更，请刷新页面。")
                if command.revision != state["revision"]:
                    raise ValueError("配置刚被其他窗口修改，本次未覆盖；请确认最新位置后重试。")
                if command.id not in {obj["id"] for obj in state["objects"]}:
                    raise ValueError("没有找到这个厨具，请重新选择。")
                if isinstance(command, _Reset):
                    default = self.defaults.get(command.id)
                    if default is None:
                        raise ValueError("默认配置中没有这个厨具，无法恢复。")
                    pos, angles = default.pos, default.rpy_deg
                else:
                    pos, angles = command.pos, command.rpy_deg
                save_pose(self.scene, command.id, pos, angles,
                          session_directory=self.directory, expected_revision=command.revision)
                return self._state()
            except (OSError, ValueError) as error:
                response = {"type": "error", "message": (
                    "输入无效，请使用有限的三个数字，且不要添加未知字段。"
                    if isinstance(error, ValidationError) else str(error))}
                try:
                    response["state"] = self._state()
                except (OSError, ValueError):
                    pass
                return response


def same_origin(headers, port):
    """Only this loopback page may access the mutating editor websocket."""
    from websockets.datastructures import MultipleValuesError

    try:
        return (headers.get("Origin") in {
            f"http://127.0.0.1:{port}", f"http://localhost:{port}",
        } and headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"})
    except (KeyError, ValueError, MultipleValuesError):
        return False


def _http_response(path, static_response):
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    assets = {"/kitchen-editor/editor.js": ("editor.js", "text/javascript; charset=utf-8"),
              "/kitchen-editor/editor.css": ("editor.css", "text/css; charset=utf-8")}
    if path in assets:
        filename, content_type = assets[path]
        try:
            body = (ASSETS / filename).read_bytes()
        except OSError:
            return Response(404, "Not Found", Headers({"Content-Length": "0"}), b"")
        return Response(200, "OK", Headers({"Content-Type": content_type,
                        "Content-Length": str(len(body)), "Cache-Control": "no-store"}), body)
    response = static_response(path)
    if path in {"/", "/index.html", ""} and response.status_code == 200:
        additions = (b'<link rel="stylesheet" href="/kitchen-editor/editor.css">'
                     b'<script type="module" src="/kitchen-editor/editor.js"></script>')
        response.body = response.body.replace(b"</head>", additions + b"</head>", 1)
        del response.headers["Content-Length"]
        response.headers["Content-Length"] = str(len(response.body))
    return response


async def serve_layout_hub(port, scene):
    """Serve the normal WRS page and an editor bound to this exact runtime scene."""
    import websockets
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    from wrs_agent.env.wrs import load_wrs

    load_wrs()
    from wrs.viewer.server import Hub, _static_response

    editor = LayoutWebEditor(scene)
    hub = Hub(idle_timeout=0, url=f"http://127.0.0.1:{port}/", auto_open=False)

    async def router(websocket):
        path = urlsplit(websocket.request.path).path
        if path == "/kitchen-layout":
            try:
                await websocket.send(json.dumps(await asyncio.to_thread(editor.state)))
                async for message in websocket:
                    reply = await asyncio.to_thread(editor.handle, message)
                    await websocket.send(json.dumps(reply, allow_nan=False))
            except (OSError, ValueError) as error:
                await websocket.send(json.dumps({"type": "error", "message": str(error)}))
                await websocket.close(1008, "kitchen session ended")
            except websockets.ConnectionClosed:
                pass
        elif path.rstrip("/") == "/publish":
            await hub.on_publish(websocket)
        else:
            await hub.on_view(websocket)

    def process_request(connection, request):
        path = urlsplit(request.path).path
        if request.headers.get("Upgrade", "").lower() == "websocket":
            if path == "/kitchen-layout":
                if not same_origin(request.headers, port):
                    return Response(403, "Forbidden", Headers({"Content-Length": "0"}), b"")
                # Scene meshes need unlimited frames; editor commands are independently bounded.
                connection.protocol.max_size = MESSAGE_LIMIT
            return None
        return _http_response(path, _static_response)

    async with websockets.serve(router, "127.0.0.1", port, max_size=None,
                                process_request=process_request):
        await hub.stop.wait()
