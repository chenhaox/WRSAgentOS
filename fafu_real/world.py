"""世界状态：kitchen_scene.py 里的物体当前在哪、手里拿着什么、灶开没开。

没有视觉，状态全靠技能执行后更新：抓起来的物体跟着 TCP 走，放进容器的东西跟着容器走，
倒出去的东西记到目标里。真机上东西被碰歪了这里不会知道，重新摆好后重启程序。
"""

from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, field

import fafu_config as C
import kitchen_scene
import numpy as np
from kinematics import homo, rot_z


class SceneError(ValueError):
    pass


# 各物体的大致外形，只用于避碰和算“放到哪 / 倒在哪”，粗略即可，不用现场量。
# 位置不看这里，看 kitchen_scene.py 的 at_deg；placeholder 只在没记 at_deg 时给离线 check 用。
# 以后接视觉，尺寸由识别结果给出，这张表就不用了。
_SHAPES = {
    "table": {"size": (0.80, 0.90, 0.04), "placeholder": (0.30, 0.0, -0.02)},
    "cooktop": {
        "size": (0.24, 0.24, 0.01),
        "control": {"offset": (0.0, 0.11, 0.005), "press_m": 0.008, "state": "off"},
        "placeholder": (0.27, -0.13, 0.005),
    },
    "pot": {"size": (0.15, 0.15, 0.08), "seat_depth_m": 0.04, "placeholder": (0.27, -0.14, 0.05)},
    "strainer": {
        "size": (0.08, 0.08, 0.05),
        "grasp_offset": (-0.035, 0.0, 0.015),
        "placeholder": (0.20, 0.12, 0.025),
    },
    "noodles": {"size": (0.03, 0.04, 0.03), "placeholder": (0.15, 0.03, 0.015)},
    "plate": {"size": (0.12, 0.12, 0.016), "placeholder": (0.325, 0.12, 0.008)},
    "spoon": {
        "size": (0.02, 0.02, 0.10),
        "grasp_offset": (0.0, 0.0, 0.03),
        "close_axis_deg": 90.0,
        "placeholder": (0.14, 0.20, 0.05),
    },
}
_DEFAULT_SIZE = {
    "table": (0.80, 0.90, 0.04),
    "surface": (0.12, 0.12, 0.02),
    "container": (0.12, 0.12, 0.06),
    "food": (0.03, 0.04, 0.03),
    "tool": (0.02, 0.02, 0.10),
    "appliance": (0.24, 0.24, 0.01),
}


@dataclass
class Item:
    name: str
    label: str
    kind: str
    size: np.ndarray
    pose: np.ndarray  # 4x4，长方体中心
    grasp: dict | None = None
    at_deg: tuple | None = None  # TCP 对准这个物体时的 6 个关节角（度）
    container: str | None = None
    seat_depth_m: float | None = None
    control: dict | None = None
    state: str | None = None

    @property
    def half(self) -> np.ndarray:
        return self.size / 2.0

    @property
    def upright(self) -> bool:
        return bool(self.pose[2, 2] > 0.9)

    @property
    def upside_down(self) -> bool:
        return bool(self.pose[2, 2] < -0.9)

    @property
    def yaw(self) -> float:
        return math.atan2(self.pose[1, 0], self.pose[0, 0])

    def top(self) -> float:
        return float(self.corners()[:, 2].max())

    def bottom(self) -> float:
        return float(self.corners()[:, 2].min())

    def corners(self, inflate: float = 0.0) -> np.ndarray:
        h = self.half + inflate
        local = np.array(
            [
                [sx * h[0], sy * h[1], sz * h[2]]
                for sx in (-1, 1)
                for sy in (-1, 1)
                for sz in (-1, 1)
            ]
        )
        return local @ self.pose[:3, :3].T + self.pose[:3, 3]

    def contains_points(self, pts: np.ndarray, inflate: float) -> bool:
        """世界系点是否落进（膨胀后的）长方体。"""
        local = (np.asarray(pts, dtype=float) - self.pose[:3, 3]) @ self.pose[:3, :3]
        return bool(np.any(np.all(np.abs(local) <= self.half + inflate, axis=1)))


@dataclass
class World:
    items: dict[str, Item]
    held: str | None = None
    grasp_tf: np.ndarray | None = None  # TCP → 手上物体
    qs: list[float] = field(default_factory=lambda: [0.0] * 6)
    tcp: np.ndarray = field(default_factory=lambda: np.eye(4))
    width: float = 0.0
    version: int = 0
    mode: str = "dry_run"
    pose: str | None = None  # 当前所在的命名位形
    home: dict[str, np.ndarray] = field(
        default_factory=dict
    )  # 开机时的物体位姿，place_on 桌面时放回这里

    @classmethod
    def from_config(cls, objects=None):
        items = {}
        for name, entry in (objects or kitchen_scene.OBJECTS).items():
            spec = {**_SHAPES.get(name, {}), **entry}
            kind = spec.get("kind")
            if kind not in _DEFAULT_SIZE:
                raise SceneError(f"{name}: kind 不认识：{kind!r}")
            size = np.asarray(spec.get("size", _DEFAULT_SIZE[kind]), dtype=float)
            if size.shape != (3,) or np.any(size <= 0):
                raise SceneError(f"{name}: size 要 3 个正数")
            if spec.get("at_deg") is None and "placeholder" not in spec:
                raise SceneError(f"{name}: 还没记 at_deg（用 read_joints.py 读关节角填进去）")
            pose = homo(rot_z(0.0), spec.get("placeholder", (0.0, 0.0, 0.0)))
            control = copy.deepcopy(spec.get("control"))
            if control is not None:
                control["point"] = control.pop("offset", (0.0, 0.0, 0.0))
            grasp = None
            if name in C.GRIP:
                grasp = {
                    **C.GRIP[name],
                    "point": tuple(spec.get("grasp_offset", (0.0, 0.0, 0.0))),
                    "close_axis_deg": float(spec.get("close_axis_deg", 0.0)),
                }
            spec["grasp"] = grasp
            at_deg = spec.get("at_deg")
            if at_deg is not None and len(tuple(at_deg)) != 6:
                raise SceneError(f"{name}.at_deg 要 6 个关节角（度）")
            items[name] = Item(
                name=name,
                label=spec.get("label", name),
                kind=kind,
                size=size,
                pose=pose,
                grasp=grasp,
                at_deg=None if at_deg is None else tuple(float(v) for v in at_deg),
                container=spec.get("container"),
                seat_depth_m=spec.get("seat_depth_m"),
                control=control,
                state=(control or {}).get("state"),
            )
        for item in items.values():
            if item.container is not None and items.get(item.container, None) is None:
                raise SceneError(f"{item.name}: container {item.container!r} 不在场景里")
        return cls(items, home={name: item.pose.copy() for name, item in items.items()})

    def item(self, name: str) -> Item:
        if name not in self.items:
            raise SceneError(f"{name}_not_in_scene")
        return self.items[name]

    def table_z(self) -> float:
        tables = [i for i in self.items.values() if i.kind == "table"]
        return max((t.top() for t in tables), default=0.0)

    def contents(self, name: str) -> list[str]:
        """直接或间接放在 ``name`` 里的物体。"""
        out = []
        for item in self.items.values():
            if item.container == name:
                out += [item.name, *self.contents(item.name)]
        return out

    def ancestors(self, name: str) -> list[str]:
        out, cur = [], self.items[name].container
        while cur is not None:
            out.append(cur)
            cur = self.items[cur].container
        return out

    def supports(self, name: str) -> list[str]:
        """``name`` 底下直接托着它的物体（顶面贴着它底面、而且有重叠）。"""
        item = self.items[name]
        out = []
        for other in self.items.values():
            if other.name == name or other.kind == "table":
                continue
            if abs(other.top() - item.bottom()) < 0.015 and other.contains_points(
                item.pose[:3, 3][None, :] * [1, 1, 0] + [0, 0, other.pose[2, 3]], 0.0
            ):
                out.append(other.name)
        return out

    def locate_from_joints(self, tcp_of, logger=print) -> None:
        """``at_deg`` 是 TCP 对准该物体时的关节角，正运动学换成世界坐标，技能再自己规划过去。

        能抓的东西：记录时两指合拢方向（TCP Y）就是以后抓它的方向，物体朝向跟着它定。
        """
        for item in self.items.values():
            if not item.at_deg:
                continue
            tcp = tcp_of([math.radians(float(v)) for v in item.at_deg])
            touched = tcp[:3, 3]
            pose = item.pose.copy()
            if item.grasp:
                close = tcp[:3, 1]
                if np.linalg.norm(close[:2]) > 0.3:
                    axis = math.radians(item.grasp["close_axis_deg"])
                    pose[:3, :3] = rot_z(math.atan2(close[1], close[0]) - axis)
                center = touched - pose[:3, :3] @ np.asarray(item.grasp["point"], dtype=float)
                where = "抓取点"
            elif item.control:
                center = touched - np.asarray(item.control["point"], dtype=float)
                where = "按键"
            else:
                center = touched.copy()
                center[2] -= float(item.half[2])
                where = "顶面中心"
            pose[:3, 3] = center
            self.move(item.name, pose)
            logger(
                f"[scene] {item.name} 由关节角得到{where} "
                f"[{touched[0]:+.3f}, {touched[1]:+.3f}, {touched[2]:+.3f}]"
            )
        self.home = {name: it.pose.copy() for name, it in self.items.items()}

    def move(self, name: str, pose: np.ndarray) -> None:
        """移动物体，里面装着的东西一起走。"""
        item = self.items[name]
        delta = pose @ np.linalg.inv(item.pose)
        for moved in [name, *self.contents(name)]:
            self.items[moved].pose = delta @ self.items[moved].pose

    def follow_tcp(self, tcp: np.ndarray) -> None:
        self.tcp = np.asarray(tcp, dtype=float)
        if self.held is not None and self.grasp_tf is not None:
            self.move(self.held, self.tcp @ self.grasp_tf)

    # -- WRSAgentOS 快照 ----------------------------------------------------
    def snapshot(self):
        from wrs_agent.schemas import KinematicState, ObjectData, RobotData, SceneData

        now = time.time_ns()
        objects = {
            name: ObjectData(
                label=item.label,
                pos=item.pose[:3, 3].tolist(),
                rotmat=item.pose[:3, :3].tolist(),
                geometry={"kind": "box", "xyz_lengths": item.size.tolist()},
                location=item.container,
                source="configured",
                observed_at_ns=now,
            )
            for name, item in self.items.items()
        }
        facts = {"execution": self.mode}
        for item in self.items.values():
            if item.state is not None:
                facts[f"{item.name}_state"] = item.state
        return SceneData(
            robot=RobotData(
                held_object=self.held,
                pose=self.pose,
                kinematics=KinematicState(
                    qs=list(self.qs),
                    tcp_name="grasp_center",
                    tcp_pos=self.tcp[:3, 3].tolist(),
                    tcp_rotmat=self.tcp[:3, :3].tolist(),
                    gripper_width=max(0.0, float(self.width)),
                    observed_at_ns=now,
                ),
            ),
            objects=objects,
            facts=facts,
        )

    def describe(self) -> str:
        lines = [f"手上：{self.held or '无'}"]
        for item in self.items.values():
            if item.kind == "table":
                continue
            p = item.pose[:3, 3]
            where = f"，在 {item.container} 里" if item.container else ""
            state = f"，状态 {item.state}" if item.state else ""
            turn = "" if item.upright else ("，倒扣" if item.upside_down else "，倾斜")
            lines.append(
                f"  {item.name}({item.label}) [{p[0]:+.3f}, {p[1]:+.3f}, {p[2]:+.3f}]"
                f"{where}{turn}{state}"
            )
        return "\n".join(lines)
