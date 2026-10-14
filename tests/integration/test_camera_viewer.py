"""WRS stays in the viewer process, including in integration tests."""

import subprocess

import pytest

from wrs_agent.processes import NO_WINDOW, ROOT, python_command

pytestmark = [pytest.mark.zenoh, pytest.mark.wrs]


def test_existing_wrs_page_camera_absent_ready_disconnected_reconnected():
    result = subprocess.run(python_command(ROOT / "tests/fixtures/camera_viewer.py"),
                            capture_output=True, text=True, encoding="utf-8", timeout=30,
                            creationflags=NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "absent/ready/disconnected/reconnected PASS" in result.stdout


def test_viewer_empty_probe_keeps_real_errors_and_http_websocket_working():
    result = subprocess.run(python_command(ROOT / "tests/fixtures/viewer_handshake.py"),
                            capture_output=True, text=True, encoding="utf-8", timeout=25,
                            creationflags=NO_WINDOW)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "malformed logged; HTTP/WebSocket PASS" in result.stdout
