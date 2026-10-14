import asyncio
import contextlib
import json
import os
import socket
import time

import httpx

from wrs_agent.env.wrs import serve_viewer_hub
from wrs_agent.nodes.camera.preview import CameraPreview
from wrs_agent.processes import ROOT, LocalStack, python_command
from wrs_agent.transport import Transport


async def eventually(call, predicate, timeout=4):  # noqa: ASYNC109 - bounded test polling
    deadline = time.monotonic() + timeout
    while True:
        value = await call()
        if predicate(value):
            return value
        if time.monotonic() > deadline:
            raise TimeoutError("camera_viewer_condition")
        await asyncio.sleep(0.02)


async def main():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    async with LocalStack(bindings="configs/robot.toml") as stack:
        os.environ["WRS_AGENT_TOKEN"] = stack.token
        preview = CameraPreview(endpoint=stack.endpoint, env_id=stack.env_id)
        await preview.start()
        hub = asyncio.create_task(serve_viewer_hub(port, http_handler=preview.http_response))
        bus = Transport(stack.endpoint, stack.site, stack.env_id + "-camera", stack.token, "test")
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
                async def read():
                    try:
                        return (await http.get("/camera/preview")).json()
                    except httpx.ConnectError:
                        return {}

                offline = await eventually(read, lambda s: s.get("state") == "offline", timeout=8)
                target = {"endpoint": stack.endpoint, "site": stack.site,
                          "env_id": stack.env_id, "node_id": "camera"}
                assert offline["connection"] == target
                assert stack.token not in json.dumps(offline)
                page = await http.get("/")
                assert page.status_code == 200 and "/camera/panel.js" in page.text
                assert "drawPoints" in (await http.get("/camera/panel.js")).text
                child = stack._spawn("camera", python_command(
                    ROOT / "tests/fixtures/camera_node.py", stack.endpoint, stack.env_id,
                ))
                first = await eventually(read, lambda s: s.get("state") == "ready", timeout=8)
                assert first["source"] == "synthetic" and len(first["points"]) <= 2048
                assert first["connection"] == target
                assert stack.token not in json.dumps(first)
                await bus.request("request/node/camera/shutdown", {}, control=True)
                assert await asyncio.to_thread(child.wait, 5) == 0
                disconnected = await eventually(read, lambda s: s.get("state") != "ready")
                assert "rgb" not in disconnected and "points" not in disconnected
                stack._spawn("camera-restarted", python_command(
                    ROOT / "tests/fixtures/camera_node.py", stack.endpoint, stack.env_id,
                ))
                second = await eventually(read, lambda s: s.get("state") == "ready", timeout=8)
                old_stream = first["observation_id"].split(".")[0]
                assert old_stream != second["observation_id"].split(".")[0]
        finally:
            hub.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hub
            await preview.close()
            await bus.close()


if __name__ == "__main__":
    asyncio.run(main())
    print("Camera viewer: absent/ready/disconnected/reconnected PASS")
