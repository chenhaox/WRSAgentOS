"""Actual WRS Lite6 FK, owned by one worker. No viewer or hardware connection."""

import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path

from wrs_agent.actions import ActionExecutor, ExecutionUnknown
from wrs_agent.schemas import RobotData
from wrs_agent.skills import SKILLS

ROOT = Path(__file__).resolve().parents[2]
POSES = {
    "home": [0.0] * 6,
    "B": [0.3, 0.2, 0.5, 0.0, 0.2, 0.0],
    "C": [-0.3, 0.1, 0.4, 0.0, 0.1, 0.0],
}


def load_wrs():
    # Only this adapter enables the nominated environment's scientific packages.
    scientific = Path(sys.executable).parents[1] / "Lib/site-packages"
    for package_path in (scientific, Path(sys.base_prefix) / "Lib/site-packages"):
        if str(package_path) not in sys.path:
            sys.path.append(str(package_path))
    source = ROOT / "third_party/wrs"
    sys.path.insert(0, str(source))
    import wrs

    if not Path(wrs.__file__).resolve().is_relative_to(source.resolve()):
        raise RuntimeError("unexpected_wrs_source")
    return wrs


class VirtualModel:
    """All methods, including construction, run on one owner thread."""

    def __init__(self):
        self.wrs = load_wrs()
        self.robot = self.wrs.xarm_lite6.Lite6()
        self.path = None
        self.goal = None

    def read(self):
        tf = self.robot.gl_lnk_tfarr
        return {
            "joints": self.robot.qs.tolist(),
            "tip_position": tf[-1, :3, 3].tolist(),
            "observed_at_ns": time.time_ns(),
            "valid": bool(self.wrs.np.isfinite(tf).all()),
        }

    def begin(self, pose, count):
        self.goal = self.wrs.np.asarray(POSES[pose], dtype=self.wrs.np.float32)
        self.path = self.wrs.wmij.interp_by_n(self.robot.qs.copy(), self.goal, count)

    def step(self, index):
        self.robot.fk(self.path[index])
        return self.read()

    def verify(self):
        # Read the model, not a timer or a commanded pose label.
        before = self.robot.gl_lnk_tfarr.copy()
        after = self.robot.fk(self.robot.qs.copy())
        return bool(
            self.wrs.np.allclose(self.robot.qs, self.goal, atol=1e-6)
            and self.wrs.np.allclose(before, after, atol=1e-6)
            and self.wrs.np.isfinite(after).all()
        )


class VirtualState:
    def __init__(self, kinematics):
        self.version = 0
        self.kinematics = kinematics
        self.pose = "home"

    def snapshot(self):
        return RobotData(
            pose=self.pose,
            kinematics=dict(self.kinematics),
            facts={"calibration": "wrs2-lite6-v1", "collision_checked": False},
        )


async def make_wrs_environment(journal_path, *, duration=0.4, allow_hardware=False):
    if allow_hardware:
        raise ValueError("hardware_unsupported")
    if not 0 < duration <= 30:
        raise ValueError("invalid_duration")
    owner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wrs-model")
    loop = asyncio.get_running_loop()

    async def call(fn, *args):
        # Never cancel this future to claim a physical/model stop.
        return await loop.run_in_executor(owner, fn, *args)

    try:
        model = await call(VirtualModel)
        state = VirtualState(await call(model.read))
    except BaseException:
        owner.shutdown(wait=False)
        raise

    async def advance(skill, state, args, stop, progress):
        try:
            if skill == "observe":
                state.kinematics = await call(model.read)
                state.version += 1
                return state.kinematics["valid"]
            count = max(2, round(duration / 0.02) + 1)
            await call(model.begin, args.pose, count)
            state.pose = None
            for index in range(1, count):
                if stop.is_set():
                    return False
                # One in-flight FK only. No model object is read by the control thread.
                state.kinematics = await call(model.step, index)
                state.version += 1
                if stop.is_set():
                    return False
                progress(index / (count - 1))
                try:
                    await asyncio.wait_for(stop.wait(), duration / (count - 1))
                except TimeoutError:
                    pass
            if stop.is_set():
                return False
            verified = await call(model.verify)
            if not stop.is_set() and verified:
                state.pose = args.pose
            return verified
        except Exception:
            state.kinematics = {**state.kinematics, "valid": False}
            # A model update may have happened; no blind resume or retry.
            raise ExecutionUnknown("wrs_model_state_unknown") from None

    async def close():
        await asyncio.to_thread(owner.shutdown, wait=True)

    executor = ActionExecutor(
        journal_path,
        state=state,
        close_backend=close,
        skills={
            name: replace(SKILLS[name], handler=partial(advance, name))
            for name in ("observe", "move_named_pose")
        },
        duration=0,  # This backend advances itself; no Mock delay before motion.
        backend="wrs_virtual",
        capabilities_extra={
            "verification": "wrs_fk",
            "stop_scope": "virtual_fk_boundary",
            "controller_flush": False,
            "unsupported": {
                "pick": "Bare Lite6 profile has no validated gripper/grasp scene.",
                "place": "No verified held-object/contact state.",
                "verify": "Object placement verifier requires a grasp scene.",
                "hardware": "No verified controller stop/flush/state feedback.",
                "collision_planning": "No collision-checked path in this FK-only profile.",
            },
        },
    )
    return executor


def probe_virtual():
    from importlib.metadata import version

    wrs = load_wrs()
    robot = wrs.xarm_lite6.Lite6()
    transforms = robot.fk()
    return {
        "import": "PASS",
        "dependencies": {
            name: version(name) for name in ("numpy", "scipy", "mujoco", "wgpu", "websockets")
        },
        "module": wrs.__file__,
        "robot_class": type(robot).__name__,
        "fk_shape": list(transforms.shape),
        "joints": robot.qs.tolist(),
        "hardware": False,
        "virtual_runtime_adapter": "wrs_virtual_fk",
    }
