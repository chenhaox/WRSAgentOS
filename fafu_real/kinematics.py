"""Panthera-HT 运动学：比赛仓库 WRS 的 FK / TracIK，解出来的关节角按真机 robot.cfg 软限位检查。

仿真模型的关节范围比真机放宽过（见 panthera_ht.py 里的 SIM-ONLY 注释），所以这里每个解
都再对一遍真机限位，超出就当无解，不交给 RealArm 去裁剪。
"""

from __future__ import annotations

import math
import threading

import _paths  # noqa: F401
import fafu_config as C
import numpy as np
from scipy.spatial.transform import Rotation, Slerp

FLIP_Z = np.diag([-1.0, -1.0, 1.0])  # 绕夹爪 Z 轴转 180°：两指夹爪是同一个抓法


def homo(rot, pos) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = np.asarray(rot, dtype=float)
    T[:3, 3] = np.asarray(pos, dtype=float).reshape(3)
    return T


def rot_z(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def rot_axis(axis, angle: float) -> np.ndarray:
    return Rotation.from_rotvec(
        np.asarray(axis, dtype=float) / np.linalg.norm(axis) * angle
    ).as_matrix()


def down_rot(close_axis_xy, tilt: float = 0.0) -> np.ndarray:
    """夹爪朝下（TCP Z = 世界 -Z），两指沿 ``close_axis_xy`` 合拢（TCP Y）。

    再绕 TCP Y 倾斜 ``tilt``（弧度）。
    """
    z = np.array([0.0, 0.0, -1.0])
    y = np.array([close_axis_xy[0], close_axis_xy[1], 0.0], dtype=float)
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    rot = np.column_stack([x, y, z])
    if abs(tilt) > 1e-9:
        rot = rot @ rot_axis((0.0, 1.0, 0.0), tilt)
    return rot


def interpolate(T0: np.ndarray, T1: np.ndarray) -> list[np.ndarray]:
    """T0 → T1 的笛卡尔插值帧（不含 T0），平移按 CART_STEP_M、旋转按 ANGLE_STEP_DEG 切分。"""
    dist = float(np.linalg.norm(T1[:3, 3] - T0[:3, 3]))
    r0, r1 = Rotation.from_matrix(T0[:3, :3]), Rotation.from_matrix(T1[:3, :3])
    angle = float((r0.inv() * r1).magnitude())
    n = max(1, math.ceil(dist / C.CART_STEP_M), math.ceil(math.degrees(angle) / C.ANGLE_STEP_DEG))
    slerp = Slerp([0.0, 1.0], Rotation.concatenate([r0, r1]))
    frames = []
    for i in range(1, n + 1):
        t = i / n
        frames.append(homo(slerp(t).as_matrix(), T0[:3, 3] + (T1[:3, 3] - T0[:3, 3]) * t))
    return frames


def max_jump(a, b) -> float:
    return float(np.max(np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float))))


def lerp_joints(q0, q1, step_rad: float = math.radians(2.0)) -> list[list[float]]:
    a, b = np.asarray(q0, dtype=float), np.asarray(q1, dtype=float)
    n = max(1, math.ceil(max_jump(a, b) / step_rad))
    return [(a + (b - a) * i / n).tolist() for i in range(1, n + 1)]


class Kinematics:
    def __init__(self, joint_limits):
        from wrs.robot_sim.robots.panthera_ht.panthera_ht_single_arm import PantheraHTSglArm

        self.robot = PantheraHTSglArm(enable_cc=False)
        self.robot.end_effector.jaw_range = np.array([0.0, C.JAW_OPEN_M])
        self.limits = np.asarray(joint_limits, dtype=float)
        self.tol = math.radians(C.JOINT_LIMIT_TOL_DEG)
        self.jump = math.radians(C.MAX_JOINT_JUMP_DEG)
        self.rng = np.random.default_rng(0)
        self.lock = threading.Lock()

    # -- FK ----------------------------------------------------------------
    def tcp(self, q) -> np.ndarray:
        with self.lock:
            self.robot.fk(np.asarray(q, dtype=float), update=True)
            return homo(self.robot.gl_tcp_rotmat, self.robot.gl_tcp_pos)

    def flange(self, q) -> np.ndarray:
        with self.lock:
            self.robot.fk(np.asarray(q, dtype=float), update=True)
            m = self.robot.manipulator
            return homo(m.gl_flange_rotmat, m.gl_flange_pos)

    def within_limits(self, q) -> bool:
        q = np.asarray(q, dtype=float)
        return bool(
            np.all(q >= self.limits[:, 0] - self.tol) and np.all(q <= self.limits[:, 1] + self.tol)
        )

    # -- IK ----------------------------------------------------------------
    def _candidates(self, q, fold: bool):
        """同一姿态的等价解：J6 差 2π；``fold`` 时两指对称，J6 差 π 也算。"""
        step = math.pi if fold else 2.0 * math.pi
        out = []
        for k in range(-4, 5):
            c = np.asarray(q, dtype=float).copy()
            c[5] += k * step
            if self.within_limits(c):
                out.append(np.clip(c, self.limits[:, 0], self.limits[:, 1]))
        return out

    def ik(self, T, seed, *, fold: bool = False, explore: bool = True):
        """TCP 位姿 → 关节角，选离 ``seed`` 最近、且在真机限位内的解；无解返回 None。"""
        seed = np.asarray(seed, dtype=float)
        rots = [T[:3, :3], T[:3, :3] @ FLIP_Z] if fold else [T[:3, :3]]
        seeds = [seed]
        if explore:
            seeds += [
                self.rng.uniform(self.limits[:, 0], self.limits[:, 1])
                for _ in range(C.IK_RANDOM_SEEDS)
            ]
        best, best_gap = None, math.inf
        with self.lock:
            for rot in rots:
                for s in seeds:
                    q = self.robot.ik(tgt_pos=T[:3, 3], tgt_rotmat=rot, seed_jnt_values=s)
                    if q is None:
                        continue
                    for c in self._candidates(q, fold):
                        gap = max_jump(c, seed)
                        if gap < best_gap:
                            best, best_gap = c, gap
                    if best is not None and best_gap <= self.jump:
                        return best.tolist()
        return None if best is None else best.tolist()

    def follow(self, frames, seed):
        """沿笛卡尔帧逐帧求解，每帧以上一帧为种子；跳变过大或无解返回 None。"""
        path, q = [], np.asarray(seed, dtype=float)
        for T in frames:
            nxt = self.ik(T, q, explore=False)
            if nxt is None or max_jump(nxt, q) > self.jump:
                nxt = self.ik(T, q, explore=True)
            if nxt is None or max_jump(nxt, q) > self.jump:
                return None
            path.append(nxt)
            q = np.asarray(nxt, dtype=float)
        return path
