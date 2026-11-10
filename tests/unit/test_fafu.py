import asyncio
import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from tests.fixtures.grasping.virtual_data import data
from wrs_agent.env.fafu import fafu_tool_pose, make_fafu_environment
from wrs_agent.env.fixed_camera import load_calibration
from wrs_agent.executor import SkillFailure
from wrs_agent.nodes.grasp.data import GraspData
from wrs_agent.nodes.wrs.node import WrsOptions
from wrs_agent.processes import ROOT
from wrs_agent.schemas import Empty, ModeRequest, new_id
from wrs_agent.skills.robot.definitions import GripperArgs, MoveArgs, RelativeMoveArgs
from wrs_agent.skills.vision.grasping import ExecuteGraspArgs


class Arm:
    def __init__(self, *, block=False):
        self.calls = []
        self.fafu_cartesian = True
        self.release = threading.Event()
        self.entered = threading.Event()
        if not block:
            self.release.set()

    def enable(self):
        self.calls.append("enable")

    def close_connection(self, **_kwargs):
        self.calls.append("close")

    def get_joint_values(self, prefer_cache=True):
        return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]

    def get_pose(self, prefer_cache=True):
        return [0.2, 0.0, 0.3], ((1, 0, 0), (0, 1, 0), (0, 0, 1))

    def go_home(self, speed=15):
        self.calls.append(("home", speed))
        self.entered.set()
        assert self.release.wait(2)

    def move_p(self, pos, rot, speed=15):
        self.calls.append(("move_p", [round(float(value), 5) for value in pos], speed))

    def open_gripper(self):
        self.calls.append("open")

    def close_gripper(self):
        self.calls.append("close")

    def emergency_stop(self):
        self.calls.append("estop")
        self.release.set()


class Dead:
    def __init__(self):
        self.closed = False

    def enable(self):
        raise RuntimeError("motors silent")

    def close_connection(self, **_kwargs):
        self.closed = True


def mode(executor, name):
    return ModeRequest(
        interrupt_id=new_id(), boot_id=executor.boot_id,
        control_epoch=executor.epoch, mode=name,
    )


async def call(executor, name, args):
    return await executor.skills[name].handler(executor.world, args, asyncio.Event(), lambda _v: None)


async def test_disconnected_fafu_reports_state_and_refuses_motion(tmp_path):
    arm = Arm()
    executor, switch = await make_fafu_environment(
        tmp_path / "fafu.sqlite3", sdk_root=None, allow_hardware=True, connect=lambda: arm,
    )
    assert executor.features().backend == "fafu"
    assert executor.features().hardware is True
    assert await call(executor, "observe", Empty()) is True
    assert executor.world.facts["robot_status"] == "disconnected"
    assert executor.snapshot().data.facts["calibration"] == "fafu-hightorque-6dof"
    assert executor.world.kinematics["source"] == "fafu_feedback"
    assert executor.world.kinematics["valid"] is False
    with pytest.raises(SkillFailure, match="fafu_not_connected"):
        await call(executor, "move_named_pose", MoveArgs(pose="home"))


async def test_real_mode_enables_reads_and_commands_only_home(tmp_path):
    arm = Arm()
    executor, switch = await make_fafu_environment(
        tmp_path / "fafu.sqlite3", sdk_root=None, allow_hardware=True, connect=lambda: arm,
    )
    opened = await switch(mode(executor, "real").model_dump())
    assert opened["accepted"] is True and opened["mode"] == "real"
    assert arm.calls[0] == "enable"
    assert await call(executor, "observe", Empty()) is True
    assert executor.world.kinematics["qs"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    assert await call(executor, "move_named_pose", MoveArgs(pose="home")) is True
    assert ("home", 15) in arm.calls
    with pytest.raises(SkillFailure, match="fafu_named_pose_unsupported"):
        await call(executor, "move_named_pose", MoveArgs(pose="B"))
    assert await call(executor, "move_relative", RelativeMoveArgs(dx=0.01)) is True
    assert ("move_p", [0.21, 0.0, 0.3], 15) in arm.calls
    assert await call(executor, "set_gripper", GripperArgs(command="open")) is True
    assert "open" in arm.calls
    closed = await switch(mode(executor, "virtual").model_dump())
    assert closed["accepted"] is True and closed["mode"] == "virtual"
    assert arm.calls[-1] == "close"


async def test_stop_during_home_stops_the_arm(tmp_path):
    arm = Arm(block=True)
    executor, _switch = await make_fafu_environment(
        tmp_path / "fafu.sqlite3", sdk_root=None, allow_hardware=True, connect=lambda: arm,
    )
    await _switch(mode(executor, "real").model_dump())
    stop = asyncio.Event()
    task = asyncio.create_task(executor.skills["move_named_pose"].handler(
        executor.world, MoveArgs(pose="home"), stop, lambda _v: None,
    ))
    await asyncio.to_thread(arm.entered.wait, 2)
    stop.set()
    assert await task is False
    assert "estop" in arm.calls


async def test_failed_enable_closes_the_port_and_stays_virtual(tmp_path):
    arm = Dead()
    executor, switch = await make_fafu_environment(
        tmp_path / "fafu.sqlite3", sdk_root=None, allow_hardware=True, connect=lambda: arm,
    )
    result = await switch(mode(executor, "real").model_dump())
    assert result["accepted"] is False
    assert result["reason"] == "fafu_connect_failed"
    assert result["mode"] == "virtual"
    assert arm.closed is True


async def test_hardware_switch_requires_permission_and_a_real_sdk(tmp_path):
    executor, switch = await make_fafu_environment(
        tmp_path / "fafu.sqlite3", sdk_root=None, connect=lambda: Arm(),
    )
    result = await switch(mode(executor, "real").model_dump())
    assert result["accepted"] is False and result["reason"] == "hardware_not_enabled"
    with pytest.raises(ValueError, match="fafu_sdk_not_found"):
        WrsOptions(backend="fafu", allow_hardware=True, sdk_root=tmp_path)
    with pytest.raises(ValueError, match="fafu_options_require_fafu_backend"):
        WrsOptions(backend="wrs", allow_hardware=True)


def measured_hand_eye(tmp_path):
    path = tmp_path / "hand_eye.json"
    path.write_text(json.dumps({
        "calibration_id": "fafu-test-measured",
        "camera_frame": "d405-color",
        "source": "measured",
        "world_from_camera": [
            1.0, 0.0, 0.0, 0.4, 0.0, -1.0, 0.0, 0.1, 0.0, 0.0, -1.0, 0.4, 0.0, 0.0, 0.0, 1.0,
        ],
        "max_observation_age_s": 5.0,
    }), encoding="utf-8")
    return path


def test_fafu_tool_pose_uses_tool_x_as_approach():
    sample = data()
    pose = fafu_tool_pose(sample.info.candidates[0], load_calibration(
        ROOT / "tests/fixtures/grasping/virtual_camera.json"))
    np.testing.assert_allclose(pose[:3, 3], [0.4, 0.1, 0.05], atol=1e-8)
    np.testing.assert_allclose(
        pose[:3, :3], [[0.0, 1.0, 0.0], [0.0, 0.0, -1.0], [-1.0, 0.0, 0.0]], atol=1e-8,
    )


def test_fafu_rejects_virtual_fixture_as_hand_eye():
    with pytest.raises(ValueError, match="fafu_hand_eye_requires_measured_calibration"):
        WrsOptions(
            backend="fafu",
            grasp_calibration=ROOT / "tests/fixtures/grasping/virtual_camera.json",
        )


async def test_apply_hand_eye_reports_base_pose_without_moving(tmp_path):
    arm = Arm()

    async def read_grasps(candidate_set_id, task_id):
        assert candidate_set_id == "candidate"
        sample = data(task_id)
        observation = sample.info.segment.observation.model_copy(
            update={"source": "realsense", "frame_id": "d405-color"},
        )
        info = sample.info.model_copy(update={
            "segment": sample.info.segment.model_copy(update={"observation": observation}),
        })
        return GraspData(info, sample.segment), 0.2

    executor, switch = await make_fafu_environment(
        tmp_path / "fafu.sqlite3", sdk_root=None, allow_hardware=True, connect=lambda: arm,
        calibration_path=measured_hand_eye(tmp_path), read_grasps=read_grasps,
    )
    await switch(mode(executor, "real").model_dump())
    assert executor.snapshot().data.facts["camera_calibration"] == "fafu-test-measured"
    executor.active_request = lambda: SimpleNamespace(task_id="task")
    result = await call(executor, "apply_hand_eye", ExecuteGraspArgs(candidate_set_id="candidate"))
    assert result.observation_id == "fixture.1"
    assert "0.400、0.100、0.050" in result.text
    assert "机械臂没有移动" in result.text
    assert not any(isinstance(item, tuple) and item[0] == "move_p" for item in arm.calls)
