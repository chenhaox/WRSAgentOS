"""对 demo/real/real_arm.RealArm 的薄封装：夹爪用米、路径分段走、段间能停。

EXECUTE_ON_REAL=False 时 RealArm 跑 dry-run：不连硬件，只更新虚拟关节角并打日志，
限位、夹爪映射、servo 插帧的逻辑和真机完全一样。
"""

from __future__ import annotations

import math

import _paths  # noqa: F401
import fafu_config as C


class Stopped(RuntimeError):
    """收到停止请求，在两段路径之间停下了。"""


class Arm:
    def __init__(self, *, execute: bool, logger=print):
        from real_arm import RealArm
        from real_config import GripperMap, load_real_config

        self.cfg = load_real_config()
        self.execute = bool(execute)
        self.log = logger
        self.real = RealArm(
            self.cfg,
            dry_run=not self.execute,
            speed=C.MOVE_SPEED,
            port=C.SERIAL_PORT,
            logger=logger,
            release_mode=C.RELEASE_MODE_ON_CLOSE,
            use_servo=C.USE_SERVO_J,
        )
        self.gripper = GripperMap.from_config(self.cfg)
        self.width = C.JAW_OPEN_M
        if not self.execute:
            self.real.set_virtual_q(C.named_pose_rad(C.DRY_RUN_START_POSE))

    @property
    def joint_limits(self):
        return self.cfg.joint_limits

    def connect(self) -> Arm:
        self.real.connect()
        return self

    def close(self) -> None:
        self.real.close()

    def joints(self) -> list[float]:
        return [float(v) for v in self.real.joint_values()]

    def move(self, path, *, label: str, speed=None, stop=None) -> list[float]:
        """走一条关节路径（弧度）。单点用 go_conf，多点分段 follow_path。"""
        path = [list(map(float, q)) for q in path]
        if not path:
            return self.joints()
        speed = int(speed or C.MOVE_SPEED)
        if len(path) == 1:
            self.real.go_conf(path[0], label=label, speed=speed)
            return path[0]
        for start in range(0, len(path), C.PATH_CHUNK):
            if stop is not None and stop.is_set():
                raise Stopped(label)
            chunk = path[start : start + C.PATH_CHUNK]
            self.real.follow_path(chunk, speed=speed, phase=label)
        return path[-1]

    def open(self, width: float | None = None, *, label: str = "") -> None:
        width = C.JAW_OPEN_M if width is None else float(width)
        self.real.gripper_move(self.gripper.width_to_angle(width), note=label)
        self.width = width

    def grasp(
        self, width: float, *, label: str = "", mode: str = "force", force=None, effort=None
    ) -> bool:
        """合爪。``force`` 碰到力矩阈值就停；``pinch`` 合到指定开口就停，力矩上限压低。

        dry-run 没有力矩反馈，按抓到算。
        """
        if label:
            self.log(f"  {label}")
        if mode == "pinch":
            return self.pinch(width, effort=int(effort if effort is not None else 70))
        result = self.real.gripper_grasp(
            self.gripper.width_to_angle(width),
            force=int(force if force is not None else 160),
            effort=int(effort if effort is not None else 220),
        )
        self.width = float(width)
        if result is None:
            return True
        return bool(result.grasped)

    def pinch(self, width: float, *, effort: int) -> bool:
        """合到指定开口（米），不走到限位。真机上把力矩上限压到 ``effort``。"""
        angle = self.gripper.width_to_angle(width)
        self.log(f"  夹爪合到 {width * 1000:.0f}mm 就停，力矩上限 {effort}")
        hw = getattr(self.real, "_arm", None)
        if hw is None:
            self.real.gripper_move(angle, note="(按开口合)")
        else:
            self.real._pause_hold_for_gripper()
            try:
                kwargs = {
                    "is_radians": True,
                    "block": True,
                    "effort": int(effort),
                    "on_tick": self.real._hold_joints_tick,
                }
                try:
                    hw.gripper_control(angle, **kwargs)
                except TypeError:
                    kwargs.pop("effort")
                    hw.gripper_control(angle, **kwargs)
            finally:
                self.real._hold_paused = False
        self.width = float(width)
        return True

    def close_jaw(self, *, label: str = "") -> None:
        self.real.gripper_move(self.cfg.gripper_limits[0], note=label)
        self.width = 0.0

    def jaw_width(self) -> float:
        return float(self.width)

    def estop(self) -> None:
        self.real.estop()

    def describe_limits(self) -> str:
        parts = [
            f"J{i + 1} {math.degrees(lo):.1f}~{math.degrees(hi):.1f}°"
            for i, (lo, hi) in enumerate(self.cfg.joint_limits)
        ]
        return "真机软限位：" + "，".join(parts)
