import asyncio

import numpy as np
import pytest
from conftest import eventually

from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.processes import ROOT, LocalStack, python_command
from wrs_agent.transport import RemoteError, Transport

pytestmark = pytest.mark.zenoh


async def test_busy_binary_worker_does_not_block_control_query():
    async with LocalStack(bindings="configs/robot.toml") as stack:
        address = stack.endpoint, stack.site, stack.env_id + "-data", stack.token
        server = Transport(*address, "server")
        client = Transport(*address, "client")
        entered, release = asyncio.Event(), asyncio.Event()

        async def data(_):
            entered.set()
            await release.wait()
            return b"frame"

        async def control(_):
            return {"responsive": True}

        server.register_handler("request/frame", data, binary=True)
        server.register_handler("request/control", control, control=True)
        pending = asyncio.create_task(client.request_bytes("request/frame", {}, timeout=4))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert await client.request("request/control", {}, control=True) == {"responsive": True}
            assert not pending.done()
            release.set()
            assert await pending == b"frame"
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await client.close()
            await server.close()


async def test_camera_process_binary_data_auth_expiry_and_control():
    async with LocalStack(bindings="configs/robot.toml") as stack:
        child = stack._spawn("camera", python_command(
            ROOT / "tests/fixtures/camera_node.py", stack.endpoint, stack.env_id,
        ))
        bus = Transport(stack.endpoint, stack.site, stack.env_id + "-camera", stack.token, "test")
        bad = Transport(stack.endpoint, stack.site, stack.env_id + "-camera", "x" * 32, "bad")
        try:
            await stack.system.registry.wait_for("camera")
            client = CameraClient(bus)
            await eventually(client.status, lambda state: state["state"] == "ready")
            events = bus.subscribe("stream/camera/observation")
            info, arrays, age = await client.read()
            assert 0 <= age < 1.0
            assert info.source == "synthetic"
            assert arrays["points"].nbytes > 65536  # Real binary path, not an enlarged JSON cap.
            np.testing.assert_allclose(arrays["points"][:, :, 2], arrays["depth"] * 0.001)
            assert (await client.status())["cache_bytes"] <= 64 * 1024 * 1024
            with pytest.raises(RemoteError, match="unauthorized"):
                await CameraClient(bad).read()
            await asyncio.sleep(0.3)
            assert events.try_recv() is not None
            with pytest.raises(RemoteError, match="observation_expired"):
                await client.read(observation_id=info.observation_id)
            # The camera has no action service and cannot receive movement commands.
            node = await bus.request("request/node/camera", {})
            assert not node["actions"] and node["skills"] == {}
            reply = await bus.request("request/node/camera/shutdown", {}, control=True)
            assert reply["stopping"]
            assert await asyncio.to_thread(child.wait, 5) == 0
        finally:
            await bad.close()
            await bus.close()
