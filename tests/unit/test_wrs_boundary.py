import asyncio
import threading

import pytest
from conftest import action, control, eventually

from wrs_agent.env import wrs as adapter


async def test_slow_fk_ack_is_not_stop_confirmation(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    owner_threads = set()

    class Model:
        def __init__(self):
            owner_threads.add(threading.get_ident())

        def read(self):
            owner_threads.add(threading.get_ident())
            return {
                "joints": [0.0] * 6,
                "tip_position": [0.0] * 3,
                "observed_at_ns": 1,
                "valid": True,
            }

        def begin(self, pose, count):
            owner_threads.add(threading.get_ident())

        def step(self, index):
            owner_threads.add(threading.get_ident())
            entered.set()
            assert release.wait(3)
            return self.read()

    monkeypatch.setattr(adapter, "VirtualModel", Model)
    env = await adapter.make_wrs_environment(tmp_path / "wrs.sqlite3", duration=0.1)
    try:
        request = action(env, "move_named_pose", {"pose": "B"})
        assert (await env.submit(request)).status.state == "ACCEPTED"
        await eventually(entered.is_set, bool)
        hold = control(env)
        async with asyncio.timeout(0.3):
            receipt = await env.hold(hold)
        assert receipt.phase == "STOPPING"
        assert (await env.hold(hold)) == receipt
        assert env.snapshot().stop_confirmed is False
        assert env.status(request.action_id).state == "CANCELLING"
        assert not (await env.resume(control(env, world_version=env.world.version))).accepted
        release.set()
        await env.runner
        assert env.status(request.action_id).state == "CANCELLED"
        assert env.snapshot().stop_confirmed
        assert len(owner_threads) == 1 and threading.get_ident() not in owner_threads
    finally:
        release.set()
        await env.close()


async def test_wrs_backend_error_is_unknown(tmp_path, monkeypatch):
    class Model:
        def read(self):
            return {
                "joints": [0.0] * 6,
                "tip_position": [0.0] * 3,
                "observed_at_ns": 1,
                "valid": True,
            }

        def begin(self, pose, count):
            raise RuntimeError("unconfirmed update")

    monkeypatch.setattr(adapter, "VirtualModel", Model)
    env = await adapter.make_wrs_environment(tmp_path / "wrs.sqlite3")
    try:
        request = action(env, "move_named_pose", {"pose": "B"})
        await env.submit(request)
        await env.runner
        assert env.status(request.action_id).state == "UNKNOWN"
        assert env.snapshot().data.kinematics.valid is False
        assert not env.stop_confirmed and env.admission == "UNKNOWN"
        assert not (await env.resume(control(env, world_version=env.world.version))).accepted
    finally:
        await env.close()


async def test_hardware_refused_before_wrs_import(tmp_path):
    with pytest.raises(ValueError, match="hardware_unsupported"):
        await adapter.make_wrs_environment(tmp_path / "unused", allow_hardware=True)
