"""把技能拆成可执行的步骤：笛卡尔直线、抬高转场、倾斜，每一帧都做碰撞检查。

碰撞检查只覆盖夹爪（一个长方体）和手上的物体，对比 kitchen_scene 里的其他长方体和桌面；
机械臂连杆不检查，容器的壁也不建模（锅、漏勺都按实心长方体，放进去时要显式忽略）。
所有逆解和检查在动之前做完，一帧不通过整个技能就不执行。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import fafu_config as C
import numpy as np
from kinematics import Kinematics, homo, interpolate, lerp_joints, max_jump, rot_axis
from world import World

_GB = C.GRIPPER_BOX_M
GRIPPER_CENTER = np.array([(lo + hi) / 2 for lo, hi in (_GB["x"], _GB["y"], _GB["z"])])
GRIPPER_HALF = np.array([(hi - lo) / 2 for lo, hi in (_GB["x"], _GB["y"], _GB["z"])])


class PlanError(ValueError):
    """规划失败：还没动机械臂。消息是给人看的原因码。"""


def obb_overlap(Ta, ha, Tb, hb) -> bool:
    """两个有向长方体（中心位姿 + 半边长）是否相交，分离轴定理。"""
    Ra, Rb = Ta[:3, :3], Tb[:3, :3]
    t = Tb[:3, 3] - Ta[:3, 3]
    axes = [Ra[:, i] for i in range(3)] + [Rb[:, i] for i in range(3)]
    axes += [np.cross(Ra[:, i], Rb[:, j]) for i in range(3) for j in range(3)]
    for axis in axes:
        n = np.linalg.norm(axis)
        if n < 1e-9:
            continue
        axis = axis / n
        ra = np.sum(np.abs(Ra.T @ axis) * ha)
        rb = np.sum(np.abs(Rb.T @ axis) * hb)
        if abs(float(t @ axis)) > ra + rb:
            return False
    return True


@dataclass
class Plan:
    """按顺序执行的步骤。

    ("move", 标签, 关节路径, 速度) / ("grasp", 标签, 宽度, 没抓到时的退回路径) /
    ("open", 标签, 宽度) / ("close", 标签) / ("wait", 标签, 秒) / ("effect", 标签, 回调)
    """

    steps: list = field(default_factory=list)

    def move(self, label, path, speed=None):
        if path:
            self.steps.append(
                ("move", label, [list(map(float, q)) for q in path], speed or C.MOVE_SPEED)
            )

    def add(self, *step):
        self.steps.append(step)

    def describe(self) -> str:
        parts = []
        for step in self.steps:
            if step[0] == "move":
                parts.append(f"{step[1]}({len(step[2])}帧)")
            elif step[0] != "effect":
                parts.append(step[1])
        return " → ".join(parts) or "（无动作）"


class Planner:
    """``held`` 参数统一是 ``(物体名, TCP→物体的 4x4)`` 或 None。"""

    def __init__(self, kin: Kinematics, world: World):
        self.kin = kin
        self.world = world

    # -- 碰撞 --------------------------------------------------------------
    def _blocked(self, q, ignore, held) -> str | None:
        tcp = self.kin.tcp(q)
        boxes = [("夹爪", tcp @ homo(np.eye(3), GRIPPER_CENTER), GRIPPER_HALF)]
        skip = set(ignore)
        if held is not None:
            name, grasp_tf = held
            item = self.world.items[name]
            pose = tcp @ grasp_tf
            boxes.append((name, pose, item.half))
            for inside in self.world.contents(name):
                rel = np.linalg.inv(item.pose) @ self.world.items[inside].pose
                boxes.append((inside, pose @ rel, self.world.items[inside].half))
            skip |= {name, *self.world.contents(name)}
        for other in self.world.items.values():
            if other.name in skip:
                continue
            inflate = 0.002 if other.kind == "table" else C.CLEARANCE_M
            for name, T, half in boxes:
                if obb_overlap(T, half, other.pose, other.half + inflate):
                    return f"{name}碰到{other.name}"
        return None

    def check(self, path, ignore=(), held=None, label: str = "") -> None:
        for i, q in enumerate(path):
            if i % 2 and i != len(path) - 1:
                continue
            hit = self._blocked(q, ignore, held)
            if hit:
                raise PlanError(f"collision:{label}:{hit}")

    # -- 路径 --------------------------------------------------------------
    def line(self, q0, T1, label, *, ignore=(), held=None):
        path = self.kin.follow(interpolate(self.kin.tcp(q0), T1), q0)
        if path is None:
            raise PlanError(f"unreachable:{label}")
        self.check(path, ignore, held, label)
        return path

    def frames(self, q0, frames, label, *, ignore=(), held=None):
        path = self.kin.follow(frames, q0)
        if path is None:
            raise PlanError(f"unreachable:{label}")
        self.check(path, ignore, held, label)
        return path

    def _segments(self, q0, targets, ignore, held):
        path, q = [], q0
        for target, name in targets:
            now = self.kin.tcp(q)
            if np.linalg.norm(now[:3, 3] - target[:3, 3]) < 1e-3 and np.allclose(
                now[:3, :3], target[:3, :3], atol=1e-3
            ):
                continue
            seg = self.line(q, target, name, ignore=ignore, held=held)
            path += seg
            q = seg[-1]
        return path

    def transit(self, q0, T1, label, *, ignore=(), held=None):
        """到 ``T1``，中途 TCP 不低于 CARRY_Z_M。依次尝试：

        1. 笛卡尔：竖直抬到转场高度 → 高处平移（同时转到目标姿态）→ 竖直下降；
        2. 关节插值到目标正上方 → 竖直下降（起点姿态和目标差很多时用这条）；
        3. 关节插值直接到目标。
        每条都做碰撞检查，第一条能用的就用。
        """
        T0 = self.kin.tcp(q0)
        height = max(C.CARRY_Z_M, T1[2, 3])
        over = T1.copy()
        over[2, 3] = height
        up = T0.copy()
        up[2, 3] = max(height, T0[2, 3])
        first = None
        try:
            return self._segments(
                q0,
                ((up, f"{label}抬高"), (over, f"{label}平移"), (T1, f"{label}下降")),
                ignore,
                held,
            )
        except PlanError as exc:
            first = exc
        for start_T in (over, T1):
            q1 = self.kin.ik(start_T, q0)
            if q1 is None:
                continue
            try:
                path = self.joints(q0, q1, f"{label}(关节插值)", ignore=ignore, held=held)
                return path + self._segments(q1, ((T1, f"{label}下降"),), ignore, held)
            except PlanError:
                continue
        raise first

    def joints(self, q0, q1, label, *, ignore=(), held=None):
        path = lerp_joints(q0, q1)
        self.check(path, ignore, held, label)
        return path

    def rotate_about(self, q0, axis_world, angle, pivot, label, *, ignore=(), held=None):
        """绕世界系 ``pivot`` 点、``axis_world`` 轴转 ``angle``，逐步求解。"""
        T0 = self.kin.tcp(q0)
        pivot = np.asarray(pivot, dtype=float)
        n = max(1, math.ceil(abs(math.degrees(angle)) / C.ANGLE_STEP_DEG))
        frames = []
        for i in range(1, n + 1):
            R = rot_axis(axis_world, angle * i / n)
            T = T0.copy()
            T[:3, :3] = R @ T0[:3, :3]
            T[:3, 3] = pivot + R @ (T0[:3, 3] - pivot)
            frames.append(T)
        return self.frames(q0, frames, label, ignore=ignore, held=held)

    def tilt(self, q0, angles_deg, pivot, label, *, ignore=(), held=None):
        """绕过 ``pivot`` 的水平轴倾斜，依次试角度、TCP X/Y 两个方向和正反；返回 (路径, 角度)。"""
        T0 = self.kin.tcp(q0)
        last = None
        for degrees in angles_deg:
            for axis in (T0[:3, 0], T0[:3, 1]):
                horizontal = np.array([axis[0], axis[1], 0.0])
                if np.linalg.norm(horizontal) < 1e-6:
                    continue
                for sign in (1.0, -1.0):
                    try:
                        path = self.rotate_about(
                            q0,
                            horizontal,
                            sign * math.radians(degrees),
                            pivot,
                            label,
                            ignore=ignore,
                            held=held,
                        )
                        return path, degrees
                    except PlanError as exc:
                        last = exc
        raise last or PlanError(f"unreachable:{label}")

    @staticmethod
    def back(path, q_start):
        """原路返回：去程倒序再回到起点。"""
        return [list(q) for q in reversed(path[:-1])] + [list(q_start)]

    def jump_ok(self, q0, q1) -> bool:
        return max_jump(q0, q1) <= math.radians(C.MAX_JOINT_JUMP_DEG)
