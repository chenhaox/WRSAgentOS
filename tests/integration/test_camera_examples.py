import asyncio
import re
import runpy
import shutil
import time

import pytest
from conftest import eventually

from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.processes import ROOT, LocalStack, python_command

pytestmark = pytest.mark.zenoh


async def test_camera_and_announcement_examples_use_discovered_nodes(tmp_path, monkeypatch):
    # Start only the router; the example itself must supply its missing Agent.
    bindings = tmp_path / "router_only.toml"
    bindings.write_text('[nodes.agent]\ntype="agent"\nsuffix="-agent"\n'
                        'actions=false\nenabled=false\n', encoding="utf-8")
    async with LocalStack(bindings=bindings) as stack:
        monkeypatch.setenv("WRS_AGENT_TOKEN", stack.token)
        # Like test_developer_examples: edit constants in temporary teaching-script copies.
        # The real examples accept no CLI arguments; never use the user's 7449 or 7451.
        directory = tmp_path / "camera"
        shutil.copytree(ROOT / "examples/camera", directory,
                        ignore=shutil.ignore_patterns("__pycache__"))
        for path in directory.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            source = re.sub(r"^ENDPOINT =.*$", f'ENDPOINT = "{stack.endpoint}"',
                            source, flags=re.M)
            source = re.sub(r"^ENV_ID =.*$", f'ENV_ID = "{stack.env_id}"', source, flags=re.M)
            # A user may have configured hardware in the teaching files; never inherit it.
            if path.name == "01_start_camera.py":
                source = re.sub(r"^BACKEND =.*$", 'BACKEND = "synthetic"', source, flags=re.M)
            elif path.name == "03_start_tts.py":
                source = re.sub(r"^BACKEND =.*$", 'BACKEND = "mock"', source, flags=re.M)
            elif path.name == "04_start_agent.py":
                source = re.sub(r"^LIVE_MODEL =.*$", 'LIVE_MODEL = False', source, flags=re.M)
            path.write_text(source, encoding="utf-8")
        stack._spawn("camera", python_command(directory / "01_start_camera.py"))
        stack._spawn("tts", python_command(directory / "03_start_tts.py"))
        stack._spawn("agent", python_command(directory / "04_start_agent.py"))
        # The detector fixture validates the example's actual default frame dimensions.
        stack._spawn("yolo", python_command(
            ROOT / "tests/fixtures/vision_node.py", stack.endpoint, stack.env_id,
            "848", "480", "0.25",
        ))
        stack.started.extend(("camera", "tts", "agent", "yolo"))
        for name in ("camera", "tts", "agent", "yolo"):
            await stack.system.registry.wait_for(name, timeout=8)
        # Read real frame and YOLO data through 06, replacing only GUI calls.
        camera = CameraClient(stack.system.registry.transport("camera"))
        await eventually(camera.status, lambda state: state["state"] == "ready")
        info, arrays, _ = await camera.read()
        assert info.source == "synthetic" and arrays["points"].shape == (480, 848, 3)
        main = runpy.run_path(str(directory / "06_read_frame.py"))["main"]
        cv2, labels, images, boxes = main.__globals__["cv2"], set(), [], []
        original_put_text = cv2.putText
        original_rectangle = cv2.rectangle

        def rectangle(image, start, end, *args):
            boxes.append((start, end))
            return original_rectangle(image, start, end, *args)

        def put_text(image, label, *args):
            labels.add(label)
            return original_put_text(image, label, *args)

        began = time.monotonic()
        monkeypatch.setattr(cv2, "putText", put_text)
        monkeypatch.setattr(cv2, "rectangle", rectangle)
        monkeypatch.setattr(cv2, "namedWindow", lambda *args: None)
        monkeypatch.setattr(cv2, "imshow", lambda name, image: images.append(image.shape))
        monkeypatch.setattr(cv2, "getWindowProperty", lambda *args: 1)
        monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
        monkeypatch.setattr(cv2, "waitKey", lambda _: ord("q") if
                            any(label.startswith("cup  ") for label in labels)
                            or time.monotonic() - began > 8 else -1)
        await main()
        assert any(label.startswith("cup  ") for label in labels), labels
        assert any(label.startswith("Observation: ") for label in labels)
        assert any(label.endswith("| synthetic") for label in labels)
        assert images and images[-1] == (540, 1696, 3)
        assert boxes and all(start[0] >= 848 and end[0] >= 848 for start, end in boxes)
        child = stack._spawn("05_speak_objects.py",
                             python_command(directory / "05_speak_objects.py"))
        code = await asyncio.to_thread(child.wait, 20)
        log = (stack.directory / "05_speak_objects.py.log").read_text(encoding="utf-8")
        assert code == 0, log
        assert "任务结果： SUCCEEDED" in log
