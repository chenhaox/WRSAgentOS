"""Fafu arm over fafu_arm_sdk. The node starts disconnected.

Switching to real opens the Hightorque debug-board serial port and enables the
arm. Named poses other than home stay on the UR7e model and are rejected here.
"""

import asyncio
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from wrs_agent.env.fixed_camera import load_calibration, validate_observation
from wrs_agent.env.wrs import ROBOT_SKILLS, UNSUPPORTED, VirtualState
from wrs_agent.errors import error_info
from wrs_agent.executor import ActionExecutor, ExecutionUnknown, SkillFailure
from wrs_agent.schemas import ModeReceipt, ModeRequest
from wrs_agent.skills.robot import SKILLS
from wrs_agent.skills.vision.grasping import APPLY_HAND_EYE, HandEyeOutput

JOINT_MODELS = ("M5036_02", "M6036_02", "M6036_02", "M5036_02", "M4438_30", "M4438_30")
MOVE_SPEED = 15
_IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class FafuState(VirtualState):
    def snapshot(self):
        data = super().snapshot()
        facts = dict(data.facts)
        facts.update(calibration="fafu-hightorque-6dof", collision_checked=False)
        return data.model_copy(update={"facts": facts})


def _kinematics(qs, pos=(0.0, 0.0, 0.0), rot=_IDENTITY, *, valid):
    if len(qs) != 6:
        raise SkillFailure("fafu_joint_count")
    return {
        "qs": [float(value) for value in qs],
        "tcp_name": "fafu_flange",
        "tcp_pos": [float(value) for value in pos],
        "tcp_rotmat": [[float(value) for value in row] for row in rot],
        "observed_at_ns": time.time_ns(),
        "valid": valid,
        "source": "fafu_feedback",
    }


def fafu_tool_pose(candidate, calibration):
    """GraspNet camera pose to the Fafu tool_link pose in the robot base.

    tool_link sits 0.165 m along link6 +X with identity rotation, so tool +X is the
    wrist-to-tip approach axis. GraspNet columns are approach, closing, then binormal,
    and those map onto tool +X, +Y, +Z. This is not the DH50 axis swap.
    """
    camera = calibration.matrix()
    grasp_rotation = np.asarray(candidate.rotation, dtype=float).reshape(3, 3)
    pose = np.eye(4)
    pose[:3, :3] = camera[:3, :3] @ grasp_rotation
    pose[:3, 3] = camera[:3, :3] @ np.asarray(candidate.translation, dtype=float) + camera[:3, 3]
    return pose


def open_fafu(sdk_root, cfg_path, port):
    """Open the debug board without enabling motors. The caller enables next."""
    root = Path(sdk_root).resolve()
    package = root / "fafu_robot_python"
    if not (package / "fafu_robot_controller.py").is_file():
        raise FileNotFoundError("fafu_sdk_not_found")
    sys.path.insert(0, str(package))
    from fafu_robot_controller import FafuRobotController

    cfg = Path(cfg_path) if cfg_path else package / "robot.cfg"
    arm = FafuRobotController(
        str(cfg), port=port, has_gripper=True, gripper_motor_id=7, auto_enable=False,
    )
    try:
        arm.setup_dynamics(motor_models=list(JOINT_MODELS))
    except Exception:
        arm.fafu_cartesian = False
    else:
        arm.fafu_cartesian = True
    return arm


async def make_fafu_environment(
    journal_path, *, sdk_root, cfg_path=None, port="auto", allow_hardware=False, connect=None,
    calibration_path=None, read_grasps=None,
):
    """Return (executor, switch_mode). Nothing opens the serial port here."""
    calibration = None
    if calibration_path is not None:
        calibration = await asyncio.to_thread(load_calibration, calibration_path)
        if calibration.source != "measured":
            raise ValueError("fafu_hand_eye_requires_measured_calibration")
    if connect is None:
        if sdk_root is None:
            raise ValueError("fafu_sdk_not_found")
        connect = lambda: open_fafu(sdk_root, cfg_path, port)  # noqa: E731
    state = FafuState(_kinematics((0.0,) * 6, valid=False))
    state.facts.update(execution="virtual", robot_status="disconnected", arm="fafu")
    if calibration is not None:
        state.facts["camera_calibration"] = calibration.calibration_id
    link = {"arm": None, "cartesian": False, "busy": False}
    stop = None
    switches = {}

    async def device(fn, *args):
        return await asyncio.to_thread(fn, *args)

    def sample(arm):
        qs = arm.get_joint_values()
        if getattr(arm, "fafu_cartesian", link["cartesian"]):
            pos, rot = arm.get_pose()
            return _kinematics(qs, pos, rot, valid=True)
        return _kinematics(qs, valid=True)

    async def refresh(arm):
        state.kinematics = await device(sample, arm)
        state.version += 1

    async def move(arm, fn):
        task = asyncio.create_task(device(fn))
        stopped = asyncio.create_task(stop.wait())
        done, _pending = await asyncio.wait({task, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if stopped in done and not task.done():
            threading.Thread(target=arm.emergency_stop, daemon=True).start()
            try:
                await task
            except Exception:
                return False
            return False
        stopped.cancel()
        await task
        return not stop.is_set()

    async def advance(skill, state, args, stop_signal, progress):
        nonlocal stop
        stop = stop_signal
        arm = link["arm"]
        if skill == "observe" and arm is None:
            state.kinematics = _kinematics((0.0,) * 6, valid=False)
            state.version += 1
            return True
        if arm is None:
            raise SkillFailure("fafu_not_connected")
        try:
            if skill == "observe":
                await refresh(arm)
                return state.kinematics["valid"] is True
            if skill == "move_named_pose":
                if args.pose != "home":
                    raise SkillFailure("fafu_named_pose_unsupported")
                moved = await move(arm, lambda: arm.go_home(speed=MOVE_SPEED))
            elif skill == "move_relative":
                if not link["cartesian"]:
                    raise SkillFailure("fafu_kinematics_unavailable")
                pos, rot = await device(arm.get_pose)
                target = [float(pos[0]) + args.dx, float(pos[1]) + args.dy, float(pos[2]) + args.dz]
                moved = await move(arm, lambda: arm.move_p(target, rot, speed=MOVE_SPEED))
            else:
                command = arm.open_gripper if args.command == "open" else arm.close_gripper
                moved = await move(arm, command)
            await refresh(arm)
            if moved and skill == "move_named_pose":
                state.pose = "home"
            progress(1.0)
            return moved
        except SkillFailure:
            raise
        except Exception:
            state.kinematics = {**state.kinematics, "valid": False}
            state.facts["robot_status"] = "unknown"
            raise ExecutionUnknown("fafu_device_state_unknown") from None

    skills = []
    for name in ROBOT_SKILLS:
        skill = SKILLS[name].bind(lambda *args, name=name: advance(name, *args))
        if name != "observe":
            skill = replace(skill, timeout=30.0)
        skills.append(skill)

    async def apply_hand_eye(state, args, stop, progress):
        request = executor.active_request()
        grasp, age = await read_grasps(args.candidate_set_id, request.task_id)
        if stop.is_set():
            return False
        try:
            validate_observation(
                calibration, grasp.info.segment.observation, age, hardware=True,
            )
        except ValueError as exc:
            raise SkillFailure(str(exc)) from exc
        if grasp.info.owner.task_id != request.task_id:
            raise SkillFailure("grasp_task_mismatch")
        pose = fafu_tool_pose(grasp.info.candidates[0], calibration)
        x, y, z = (float(value) for value in pose[:3, 3])
        progress(1.0)
        return HandEyeOutput(
            observation_id=grasp.info.segment.observation.observation_id,
            text=(f"手眼标定把抓取点变到基座坐标 {x:.3f}、{y:.3f}、{z:.3f} 米。"
                  "这只是坐标绑定，机械臂没有移动。"),
        )

    if calibration is not None:
        skills.append(APPLY_HAND_EYE.bind(apply_hand_eye))
    executor = ActionExecutor(
        journal_path,
        state=state,
        skills=skills,
        backend="fafu",
        features_extra={
            "hardware": bool(allow_hardware),
            "verification": "fafu_joint_readback",
            "stop_scope": "fafu_emergency_stop",
            "controller_flush": False,
            "unsupported": {
                **UNSUPPORTED,
                "move_named_pose_B_C": "home is the only Fafu named pose; B and C belong to UR7e.",
            },
        },
    )

    def receipt(accepted, reason=""):
        mode = "real" if link["arm"] is not None else "virtual"
        return ModeReceipt(
            accepted=accepted,
            reason=reason,
            mode=mode,
            control_epoch=executor.epoch,
            error=None
            if accepted
            else error_info(reason, node_id=executor.node_id, stage="control"),
        )

    async def switch(request):
        if request.boot_id != executor.boot_id or request.control_epoch != executor.epoch:
            return receipt(False, "stale_control")
        if link["busy"]:
            return receipt(False, "mode_switch_busy")
        real = request.mode == "real"
        if real == (link["arm"] is not None):
            return receipt(True)
        if real and not allow_hardware:
            return receipt(False, "hardware_not_enabled")
        if (
            executor.active is not None
            or executor.admission == "UNKNOWN"
            or not executor.stop_confirmed
        ):
            return receipt(False, "mode_switch_not_ready")
        executor.epoch += 1
        executor.leases.clear()
        executor.admission = "HELD"
        executor.stop_confirmed = False
        link["busy"] = True
        arm = None
        try:
            if real:
                arm = await device(connect)
                await device(arm.enable)
                link["cartesian"] = bool(getattr(arm, "fafu_cartesian", False))
                link["arm"] = arm
                state.facts.update(execution="real", robot_status="enabled")
                await refresh(arm)
            else:
                arm, link["arm"] = link["arm"], None
                link["cartesian"] = False
                await device(arm.close_connection)
                state.facts.update(execution="virtual", robot_status="disconnected")
                state.kinematics = _kinematics((0.0,) * 6, valid=False)
                state.version += 1
            state.pose = None
            return receipt(True)
        except Exception:
            opened = link["arm"] or arm
            link["arm"] = None
            link["cartesian"] = False
            if opened is not None:
                try:
                    await device(opened.close_connection)
                except Exception:
                    pass
            return receipt(False, "fafu_connect_failed")
        finally:
            link["busy"] = False
            executor.stop_confirmed = executor.admission != "UNKNOWN"
            executor.on_event(
                "events/control",
                {
                    "boot_id": executor.boot_id,
                    "control_epoch": executor.epoch,
                    "stop_confirmed": executor.stop_confirmed,
                    "admission": executor.admission,
                    "execution": state.facts["execution"],
                },
            )

    async def switch_mode(payload):
        request = ModeRequest.model_validate(payload)
        if request.interrupt_id in switches:
            old, result = switches[request.interrupt_id]
            if old != request:
                return receipt(False, "interrupt_id_conflict").model_dump()
            return result.model_dump()
        result = await switch(request)
        if len(switches) < 4096:
            switches[request.interrupt_id] = (request, result)
        return result.model_dump()

    async def close():
        arm = link["arm"]
        link["arm"] = None
        if arm is not None:
            await device(arm.close_connection)

    executor.close_backend = close
    return executor, switch_mode
