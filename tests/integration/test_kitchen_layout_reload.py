"""Real terminal edit -> WRS node -> unchanged 07 polling -> page/reconnect stream."""

import asyncio
import json
import secrets
import socket
import struct
from contextlib import ExitStack

import httpx
import pytest
from conftest import eventually

from wrs_agent import System
from wrs_agent.layout_session import kitchen_session
from wrs_agent.processes import NO_WINDOW, ROOT, python_command

pytestmark = [pytest.mark.zenoh, pytest.mark.wrs]


async def page_pose(port, expected):
    # This is the same /view stream consumed on page load and browser refresh.
    from wrs_agent.env.wrs import load_wrs

    load_wrs()  # Viewer dependencies live in the WRS/Conda package paths under -S.
    import websockets

    async with websockets.connect(f"ws://127.0.0.1:{port}/view", max_size=None) as stream:
        async with asyncio.timeout(8):
            async for frame in stream:
                if not isinstance(frame, bytes):
                    continue
                length = struct.unpack_from("<I", frame)[0]
                header = json.loads(frame[4:4 + length])
                if header.get("type") != "scene_update":
                    continue
                offset = 4 + length + header["matrices"]["off"]
                for index, name in enumerate(header["ids"]):
                    matrix = struct.unpack_from("<16f", frame, offset + index * 64)
                    if all(abs(matrix[12 + i] - expected[i]) < 1e-5 for i in range(3)):
                        return name, matrix


@pytest.fixture
def session_files(tmp_path):
    kitchen = ROOT / "wrs_agent/assets/kitchen"
    originals = {}
    for name in ("scene.toml", "layout.toml"):
        originals[name] = (kitchen / name).read_bytes()
        (tmp_path / name).write_bytes(originals[name])
    with ExitStack() as sessions:
        yield tmp_path / "scene.toml", tmp_path / "session", sessions
    for name, content in originals.items():
        assert (tmp_path / name).read_bytes() == content
        assert (kitchen / name).read_bytes() == content


async def test_terminal_edit_reaches_page_and_resets_on_restart(
    tmp_path, monkeypatch, session_files,
):
    monkeypatch.setenv("WRS_AGENT_TOKEN", secrets.token_urlsafe(32))
    source, directory, sessions = session_files
    runtime = sessions.enter_context(kitchen_session(source, directory))
    bindings = tmp_path / "bindings.toml"
    bindings.write_bytes((ROOT / "examples/wrs/bindings.toml").read_bytes())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        view_port = probe.getsockname()[1]
    env_id = "layout-test-" + secrets.token_hex(6)
    viewer, editor = None, None
    log_path = tmp_path / "viewer.log"
    async with System.launch(backend="wrs", scene=runtime,
                             bindings=bindings, env_id=env_id) as system:
        initial = await system.snapshot()
        target = [0.8123, -0.3123, 0.0234]
        code = (ROOT / "examples/wrs/07_viewer.py").read_text(encoding="utf-8")
        code = code.replace("7449", system.endpoint.rsplit(":", 1)[1])
        code = code.replace("wrs-demo", env_id)
        code = code.replace("VIEWER_PORT = 8000", f"VIEWER_PORT = {view_port}")
        code = code.replace("world.schedule_interval(update, 0.1)",
                            "world.schedule_interval(update, 0.1)\n"
                            "        world.schedule_once(lambda dt: world.close(), 8.0)")
        entry = tmp_path / "07_viewer.py"
        entry.write_text(code, encoding="utf-8")
        with log_path.open("w", encoding="utf-8") as log:
            try:
                viewer = await asyncio.create_subprocess_exec(
                    *python_command(entry), cwd=tmp_path, stdout=log, stderr=log,
                    creationflags=NO_WINDOW,
                )
                async with httpx.AsyncClient(trust_env=False) as http:
                    async def page_ready():
                        if viewer.returncode is not None:
                            pytest.fail(log_path.read_text(encoding="utf-8"))
                        try:
                            response = await http.get(f"http://127.0.0.1:{view_port}")
                            return response.status_code == 200 and "canvas" in response.text
                        except httpx.TransportError:
                            return False

                    await eventually(page_ready, bool, timeout=20)
                # Establish a running scene before changing anything.
                await page_pose(view_port, initial.data.objects["pot_lid"].pos)
                editor = await asyncio.create_subprocess_exec(
                    *python_command(ROOT / "examples/wrs/10_edit_kitchen.py",
                                    "--session-dir", directory),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, creationflags=NO_WINDOW,
                )
                output, error = await asyncio.wait_for(editor.communicate(
                    b"pot_lid\n0.8123, -0.3123, 0.0234\n0, 0, 90\nq\n"), 5)
                assert editor.returncode == 0, (output + error).decode("utf-8")
                assert "已应用到本次场景" in output.decode("utf-8")

                async def changed():
                    snapshot = await system.snapshot()
                    return snapshot if snapshot.data.objects["pot_lid"].pos[0] > 0.81 else None

                updated = await eventually(changed, bool, timeout=5)
                assert updated.boot_id == initial.boot_id
                assert updated.state_version > initial.state_version
                assert updated.data.robot.kinematics.qs == initial.data.robot.kinematics.qs
                assert updated.data.objects["pot_lid"].pos == pytest.approx(target, abs=1e-6)
                identity, matrix = await page_pose(view_port, target)
                assert matrix[0] == pytest.approx(0, abs=1e-6)
                assert matrix[1] == pytest.approx(1, abs=1e-6)
                # A fresh connection models the browser's Refresh operation.
                refreshed_id, _ = await page_pose(view_port, target)
                assert refreshed_id == identity
                assert await asyncio.wait_for(viewer.wait(), 12) == 0, log_path.read_text("utf-8")
            finally:
                for process in (editor, viewer):
                    if process is not None and process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 5)
    sessions.close()
    with kitchen_session(source, directory) as restarted_scene:
        assert restarted_scene != runtime
        async with System.launch(backend="wrs", scene=restarted_scene,
                                 bindings=bindings, env_id=env_id) as restarted:
            reset = await restarted.snapshot()
            assert reset.boot_id != initial.boot_id
            assert reset.data.objects == initial.data.objects
            assert reset.data.objects["pot_lid"].pos != pytest.approx(target, abs=1e-6)
