"""FAFU arm and gripper through fafu_arm_sdk. execute=False never opens the serial port."""

import math
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
SDK_DIR = ROOT / "fafu_arm_sdk" / "fafu_robot_python"
CONFIG = SDK_DIR / "robot.cfg"
_LIMITS = re.compile(r"^limits\.(\d+)\s*=\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)", re.M)
# Firmware brakes when joint targets stop arriving, and the arm drops. Idle time resends.
HOLD_HZ = 20.0


class Stopped(RuntimeError):
    """A stop request arrived between two path chunks."""


def read_limits(path=CONFIG):
    """robot.cfg limits in degrees -> radians: six joints and the gripper motor."""
    text = Path(path).read_text(encoding="utf-8")
    found = {
        int(m.group(1)): (math.radians(float(m.group(2))), math.radians(float(m.group(3))))
        for m in _LIMITS.finditer(text)
    }
    missing = [i for i in range(1, 8) if i not in found]
    if missing:
        raise RuntimeError(f"{path} 缺少 limits.{missing}")
    return np.array([found[i] for i in range(1, 7)], dtype=float), found[7]


def densify(start, path, step):
    """Joint points no more than ``step`` radians apart, starting after ``start``."""
    out, previous = [], np.asarray(start, dtype=float)
    for q in path:
        q = np.asarray(q, dtype=float)
        count = max(1, math.ceil(float(np.max(np.abs(q - previous))) / step))
        out.extend((previous + (q - previous) * i / count).tolist() for i in range(1, count + 1))
        previous = q
    return out


class FafuArm:
    """Thread-safe. Joint values are radians in SDK order J1..J6."""

    def __init__(self, config, *, execute, start=None, logger=print):
        self.config = config
        self.execute = bool(execute)
        self.log = logger
        self.limits, self.gripper_limits = read_limits()
        self.hw = None
        self.width = float(config["gripper"]["open_m"])
        self._q = [0.0] * 6 if start is None else [float(v) for v in start]
        self._io = threading.Lock()
        self._hold_q = None
        self._hold_pause = threading.Event()
        self._hold_stop = threading.Event()
        self._hold_thread = None

    @property
    def speed(self):
        return max(1, min(100, int(self.config["arm"]["speed"])))

    def connect(self):
        if not self.execute:
            self.log("[arm] dry-run：不打开串口，关节角只在内存里更新")
            return self
        for path in (SDK_DIR / "fafu_robot_controller.py",
                     SDK_DIR / "fafu_motor.cp312-win_amd64.pyd"):
            if not path.is_file():
                raise RuntimeError(f"找不到 fafu_arm_sdk 文件：{path.name}")
        if str(SDK_DIR) not in sys.path:
            sys.path.insert(0, str(SDK_DIR))
        from fafu_robot_controller import FafuRobotController

        port = self.config["arm"].get("serial_port", "auto")
        self.hw = FafuRobotController(
            str(CONFIG), port=None if port == "auto" else port,
            has_gripper=True, gripper_motor_id=7, auto_enable=True,
        )
        self._q = [float(v) for v in self.hw.get_joint_values()]
        self._hold_q = list(self._q)
        self._start_hold()
        return self

    def _start_hold(self):
        """Resend the last commanded joints so the motors stay powered between motions."""
        if self.hw is None or (self._hold_thread is not None and self._hold_thread.is_alive()):
            return
        self._hold_stop.clear()
        self._hold_pause.clear()
        warned = False

        def loop():
            nonlocal warned
            period = 1.0 / HOLD_HZ
            while not self._hold_stop.is_set():
                if not self._hold_pause.is_set() and self._hold_q is not None:
                    try:
                        with self._io:
                            if not self._hold_pause.is_set() and self.hw is not None:
                                self.hw.move_j(list(self._hold_q), speed=self.speed, block=False)
                    except Exception as exc:
                        if not warned:
                            warned = True
                            self.log(f"[arm] 保持帧失败，仍会重试：{exc}")
                time.sleep(period)

        self._hold_thread = threading.Thread(target=loop, daemon=True, name="fafu-hold")
        self._hold_thread.start()

    def _stop_hold(self):
        self._hold_pause.set()
        self._hold_stop.set()
        if self._hold_thread is not None and self._hold_thread.is_alive():
            self._hold_thread.join(timeout=0.6)
        self._hold_thread = None

    def close(self):
        self._stop_hold()
        if self.hw is not None:
            mode = self.config["arm"].get("release_on_close", "hold")
            with self._io:
                self.hw.close_connection(joint_release=mode, gripper_release=mode)
            self.hw = None

    def joints(self):
        if self.hw is not None:
            with self._io:
                if self.hw is not None:
                    self._q = [float(v) for v in self.hw.get_joint_values(prefer_cache=False)]
        return list(self._q)

    def release(self):
        """Motors off so a person can guide the arm by hand. It falls if nobody holds it."""
        if self.hw is None:
            return
        self._hold_pause.set()
        time.sleep(0.08)
        with self._io:
            self.hw.disable()

    def capture(self):
        """Read where the hand left the arm, enable, and hold exactly there."""
        if self.hw is None:
            return self.joints()
        q = list(self._q)
        try:
            with self._io:
                q = [float(v) for v in self.hw.get_joint_values(prefer_cache=False)]
        finally:
            with self._io:
                self.hw.enable()
            self._q, self._hold_q = list(q), list(q)
            self._hold_pause.clear()
        return list(q)

    def move(self, path, *, stop=None):
        """Follow joint points. Hardware gets PATH chunks; a stop takes effect between them."""
        path = densify(self.joints(), path, math.radians(self.config["arm"]["path_step_deg"]))
        if not path:
            return self.joints()
        if self.hw is None:
            chunk = int(self.config["arm"]["chunk"])
            for start in range(0, len(path), chunk):
                if stop is not None and stop.is_set():
                    raise Stopped
                self._q = list(path[min(len(path), start + chunk) - 1])
                time.sleep(0.02)
            return list(self._q)
        chunk = int(self.config["arm"]["chunk"])
        self._hold_pause.set()
        try:
            with self._io:
                for start in range(0, len(path), chunk):
                    if stop is not None and stop.is_set():
                        raise Stopped
                    part = path[start:start + chunk]
                    if len(part) == 1:
                        self.hw.move_j(part[0], speed=self.speed, block=True)
                    else:
                        self.hw.move_jntspace_path(part, speed=self.speed, start_frame_id=0)
                    self._q, self._hold_q = list(part[-1]), list(part[-1])
        finally:
            self._hold_pause.clear()
        return list(self._q)

    def _angle(self, width):
        """Opening 0 is shut, open_m is fully open. The low gripper limit is shut."""
        t = min(1.0, max(0.0, float(width) / float(self.config["gripper"]["open_m"])))
        low, high = self.gripper_limits
        return low + (high - low) * t

    def open(self, width=None):
        width = float(self.config["gripper"]["open_m"] if width is None else width)
        if self.hw is not None:
            self._hold_pause.set()
            try:
                with self._io:
                    self.hw.gripper_control(self._angle(width), effort=80)
            finally:
                self._hold_pause.clear()
        self.width = width

    def grasp(self):
        """Close until the torque threshold. Returns whether something is between the jaws."""
        if self.hw is None:
            self.width = 0.0
            return True
        grip = self.config["gripper"]
        self._hold_pause.set()
        try:
            with self._io:
                result = self.hw.grasp(target_angle=self._angle(0.0),
                                       force_threshold=int(grip["force"]),
                                       effort=int(grip["effort"]))
        finally:
            self._hold_pause.clear()
        self.width = 0.0
        return bool(result.grasped)

    def estop(self):
        """Ctrl+C only. A planning failure must never come here: the arm would drop."""
        self._stop_hold()
        if self.hw is not None:
            with self._io:
                self.hw.emergency_stop()
