"""15 个动作型技能：规划（全部逆解 + 碰撞检查）→ 执行（RealArm）→ 更新世界状态 → 校验。

技能合同（名字、参数、前置条件）用 WRSAgentOS 的 ``wrs_agent.skills.robot.SKILLS``，
这里只提供真机实现并 ``bind`` 上去，所以看板 / Agent 看到的技能和仿真节点一模一样，
只有 move_named_pose 的位形列表换成了 fafu_config 里的命名位形。

失败分两种：
- ``SkillFailure``：还没动或已经安全收回（规划不通、没抓到），原因码给 Agent 看。
- ``ExecutionUnknown``：动到一半出了硬件异常，状态说不清，执行器会锁住后续动作。
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import threading
import time
from typing import Literal

import _paths  # noqa: F401
import fafu_config as C
import numpy as np
from arm import Arm, Stopped
from kinematics import FLIP_Z, Kinematics, down_rot, homo, interpolate, rot_z
from motions import Plan, PlanError, Planner
from pydantic import create_model
from world import SceneError, World

from wrs_agent.executor import ActionExecutor, ExecutionUnknown, SkillFailure
from wrs_agent.schemas import Boundary
from wrs_agent.skills.robot import SKILLS

# 工具伸进 / 抽出容器时，尖端在容器口上方留的余量。
RIM_CLEAR_M = C.CLEARANCE_M + 0.01
# place_on / seat_in 放下时依次试的朝向（绕物体竖直中心线），第一个规划得通的就用。
PLACE_YAWS_DEG = (0.0, 90.0, -90.0, 180.0)

ACTION_SKILLS = (
    "observe",
    "move_named_pose",
    "move_relative",
    "set_gripper",
    "pick",
    "place",
    "verify",
    "reorient",
    "place_on",
    "seat_in",
    "set_control",
    "pour",
    "stir",
    "scoop",
    "shake_off",
)


def _fail(reason: str):
    return SkillFailure(str(reason)[:240])


def _all_failed(errors, default) -> PlanError:
    """所有候选都不通：把不同的原因去重后一起报（最多 240 字）。"""
    unique = list(dict.fromkeys(errors))
    return PlanError(("; ".join(unique) or default)[:240])


def _lifted(T, dz):
    out = T.copy()
    out[2, 3] += dz
    return out


def _shifted(T, d):
    out = T.copy()
    out[:3, 3] += np.asarray(d, dtype=float)
    return out


class Robot:
    """一台 fafu 臂 + 它的世界状态。所有技能串行执行（一把锁）。"""

    def __init__(self, *, execute: bool, confirm: bool = False, logger=print, kin=None):
        self.log = logger
        self.execute = bool(execute)
        self.confirm = bool(confirm and execute)
        self.arm = Arm(execute=self.execute, logger=logger)
        self.kin = kin or Kinematics(self.arm.joint_limits)
        self.world = World.from_config()
        self.world.mode = "real" if self.execute else "dry_run"
        self.world.locate_from_joints(self.kin.tcp, logger)
        self.plan = Planner(self.kin, self.world)
        self.lock = threading.Lock()
        self.poses = C.named_poses_deg()

    # -- 生命周期 ----------------------------------------------------------
    def connect(self) -> Robot:
        self.arm.connect()
        self.sync()
        return self

    def close(self) -> None:
        self.arm.close()

    def sync(self) -> list[float]:
        q = self.arm.joints()
        self.world.qs = q
        self.world.width = self.arm.jaw_width()
        self.world.follow_tcp(self.kin.tcp(q))
        return q

    def home_on_exit(self) -> None:
        if not C.HOME_ON_EXIT:
            return
        if self.world.held is not None:
            self.log(f"[exit] 手上还有 {self.world.held}，不回 {C.EXIT_POSE}，原地保持")
            return
        self.log(f"[exit] 回 {C.EXIT_POSE}")
        self.run_skill("move_named_pose", {"pose": C.EXIT_POSE})

    # -- 对外入口 ----------------------------------------------------------
    def run_skill(self, name: str, args: dict, stop=None, progress=None) -> bool:
        """命令行入口：按合同校验参数后执行。"""
        entry = SKILLS[name] if name != "move_named_pose" else _move_contract(self)
        parsed = entry.arguments.model_validate(args or {})
        return self.execute_skill(
            name, parsed, stop or threading.Event(), progress or (lambda v: None)
        )

    def execute_skill(self, name, args, stop, progress) -> bool:
        with self.lock:
            if stop.is_set():
                return False
            self.sync()
            try:
                plan = PLANNERS[name](self, args)
            except (PlanError, SceneError) as exc:
                raise _fail(exc) from None
            if isinstance(plan, bool):
                return plan
            self.log(f"[{name}] 计划：{plan.describe()}")
            moved = False
            try:
                moved = self.run(plan, stop, progress)
            except Stopped:
                self.sync()
                self.log(f"[{name}] 已在两段之间停下")
                return False
            except SkillFailure:
                self.sync()
                raise
            except Exception as exc:
                self.log(f"[{name}] 执行异常：{exc!r}")
                if not self.execute and not moved:
                    raise _fail(f"execution_failed:{exc}") from None
                raise ExecutionUnknown(f"{name}: {exc!r}", reason="fafu_state_unknown") from None
            self.sync()
            self.world.version += 1
            return bool(VERIFY.get(name, lambda r, a: True)(self, args))

    def _confirm(self, label: str) -> None:
        if not self.confirm:
            return
        answer = input(f"  下一步【{label}】回车执行，q 回车放弃：").strip().lower()
        if answer == "q":
            raise Stopped(label)

    def run(self, plan: Plan, stop, progress) -> bool:
        total = max(1, sum(1 for s in plan.steps if s[0] != "effect"))
        done, moved = 0, False
        for step in plan.steps:
            kind, label = step[0], step[1]
            if kind != "effect":
                if stop.is_set():
                    raise Stopped(label)
                self._confirm(label)
            if kind == "move":
                moved = True
                self.arm.move(step[2], label=label, speed=step[3], stop=stop)
                self.world.qs = list(step[2][-1])
                self.world.follow_tcp(self.kin.tcp(self.world.qs))
                self.world.pose = None
            elif kind == "open":
                moved = True
                self.arm.open(step[2], label=label)
            elif kind == "close":
                moved = True
                self.arm.close_jaw(label=label)
            elif kind == "grasp":
                moved = True
                spec = step[2]
                if not self.arm.grasp(
                    spec["width"],
                    label=label,
                    mode=spec["mode"],
                    force=spec.get("force"),
                    effort=spec.get("effort"),
                ):
                    self.log("  没检测到抓住，张开并退回")
                    self.arm.open(None, label="松开")
                    self.arm.move(step[3], label="退回", speed=C.CONTACT_SPEED)
                    raise _fail("grasp_not_detected")
            elif kind == "wait":
                time.sleep(float(step[2]))
            elif kind == "effect":
                step[2]()
                self.world.version += 1
            self.world.width = self.arm.jaw_width()
            if kind != "effect":
                done += 1
                progress(min(done / total, 0.99))
        return moved

    # -- 公共几何 ----------------------------------------------------------
    @property
    def q(self):
        return list(self.world.qs)

    def held_as(self, name: str):
        if self.world.held != name:
            raise PlanError(
                "object_not_held" if self.world.held is None else f"holding_{self.world.held}"
            )
        return name, self.world.grasp_tf

    def need_empty(self):
        if self.world.held is not None:
            raise PlanError(f"gripper_not_empty:{self.world.held}")

    def tables(self):
        return [n for n, i in self.world.items.items() if i.kind == "table"]

    def grasp_candidates(self, name: str):
        item = self.world.item(name)
        if not item.grasp:
            raise PlanError(f"{name}_not_graspable")
        point = item.pose @ np.array([*item.grasp["point"], 1.0])
        angle = math.radians(float(item.grasp.get("close_axis_deg", 0.0)))
        axis = item.pose[:3, :3] @ np.array([math.cos(angle), math.sin(angle), 0.0])
        if np.linalg.norm(axis[:2]) < 1e-6:
            raise PlanError(f"{name}_close_axis_vertical")
        out = []
        for tilt in C.GRASP_TILT_DEG:
            for sign in (1.0,) if tilt == 0 else (1.0, -1.0):
                rot = down_rot(axis[:2], sign * math.radians(tilt))
                for r in (rot, rot @ FLIP_Z):
                    out.append(homo(r, point[:3]))
        return out

    def approach(self, T):
        """沿夹爪轴往回退 APPROACH_M 的预备位。"""
        return _shifted(T, -T[:3, 2] * C.APPROACH_M)

    def footprint_fits(self, obj, base, margin, pose=None) -> bool:
        """``obj``（按 ``pose`` 的姿态）的水平投影在 ``base`` 的截面内，按放到 ``base`` 中心算。"""
        rot = (obj.pose if pose is None else pose)[:3, :3]
        corners = (obj.corners() - obj.pose[:3, 3]) @ obj.pose[:3, :3] @ rot.T
        local = corners @ base.pose[:3, :3]
        return bool(np.all(np.abs(local[:, :2]).max(axis=0) <= base.half[:2] - margin))

    @staticmethod
    def yawed(pose, degrees):
        """绕物体自己的竖直中心线转 ``degrees``（换个朝向放，手腕可能更好够）。"""
        out = pose.copy()
        out[:3, :3] = rot_z(math.radians(degrees)) @ pose[:3, :3]
        return out

    def release_pose(self, name, xy, bottom_z):
        """物体保持当前姿态，平移到 xy，底面落到 bottom_z。"""
        item = self.world.items[name]
        pose = item.pose.copy()
        pose[0, 3], pose[1, 3] = xy
        pose[2, 3] += bottom_z - item.bottom()
        return pose

    def resting_on(self, base: str):
        return [
            n
            for n, i in self.world.items.items()
            if n != base and i.container is None and base in self.world.supports(n)
        ]

    def put_down(self, name, poses, ignore, label, *, container=None):
        """把手上物体放到 ``poses`` 里第一个能规划通的位姿：转场 → 下降 → 张爪 → 记账 → 退出。"""
        held = self.held_as(name)
        errors = []
        for pose in poses:
            T = pose @ np.linalg.inv(self.world.grasp_tf)
            pre = _lifted(T, C.APPROACH_M)
            q0 = self.q
            try:
                p1 = self.plan.transit(q0, pre, f"{label}转场", held=held)
                p2 = self.plan.line(
                    p1[-1] if p1 else q0, T, f"{label}下降", ignore=ignore, held=held
                )
                up = _lifted(self.kin.tcp(p2[-1]), C.APPROACH_M)
                p3 = self.plan.line(p2[-1], up, f"{label}退出", ignore=[name, *ignore])
                break
            except PlanError as exc:
                errors.append(str(exc))
        else:
            raise _all_failed(errors, f"{label}_unreachable")
        plan = Plan()
        plan.move(f"{label}转场", p1)
        plan.move(f"{label}下降", p2, C.CONTACT_SPEED)
        plan.add("open", "张爪放下", C.JAW_OPEN_M)

        def release():
            tcp = self.kin.tcp(p2[-1])
            self.world.move(name, tcp @ self.world.grasp_tf)
            self.world.held, self.world.grasp_tf = None, None
            self.world.items[name].container = container

        plan.add("effect", "放下", release)
        plan.move(f"{label}退出", p3)
        return plan


# ---------------------------------------------------------------------------
# 各技能的规划
# ---------------------------------------------------------------------------
def plan_observe(r: Robot, args):
    r.sync()
    r.log(r.world.describe())
    return True


def plan_move_named_pose(r: Robot, args):
    target = C.named_pose_rad(args.pose)
    if not r.kin.within_limits(target):
        raise PlanError(f"pose_{args.pose}_outside_joint_limits")
    held = r.held_as(r.world.held) if r.world.held else None
    path = r.plan.joints(r.q, target, f"去{args.pose}", held=held)
    plan = Plan()
    plan.move(f"去{args.pose}", path)

    def arrived():
        r.world.pose = args.pose

    plan.add("effect", "记录位形", arrived)
    return plan


def plan_move_relative(r: Robot, args):
    held = r.held_as(r.world.held) if r.world.held else None
    T = _shifted(r.kin.tcp(r.q), (args.dx, args.dy, args.dz))
    plan = Plan()
    plan.move("相对移动", r.plan.line(r.q, T, "相对移动", held=held))
    return plan


def plan_set_gripper(r: Robot, args):
    if r.world.held is not None:
        raise PlanError("object_held")
    plan = Plan()
    if args.command == "open":
        plan.add("open", "张开夹爪", C.JAW_OPEN_M)
    else:
        plan.add("close", "合上夹爪")
    return plan


def _grasp_spec(item) -> dict:
    """夹爪收多紧按 fafu_config.GRIP：面条按开口停，硬的厨具力控碰到就停。"""
    width = float(item.grasp.get("close_m", 0.0))
    if item.grasp.get("mode") == "pinch":
        return {"width": width, "mode": "pinch", "effort": int(item.grasp.get("effort", 70))}
    return {
        "width": width,
        "mode": "force",
        "force": int(item.grasp.get("force", 160)),
        "effort": int(item.grasp.get("effort", 220)),
    }


def plan_pick(r: Robot, args):
    r.need_empty()
    name = args.object
    item = r.world.item(name)
    if item.kind in {"table", "appliance"} or not item.grasp:
        raise PlanError(f"{name}_not_graspable")
    ignore = [name, *r.world.contents(name), *r.world.ancestors(name), *r.world.supports(name)]
    errors = []
    for T in r.grasp_candidates(name):
        try:
            q0 = r.q
            p1 = r.plan.transit(q0, r.approach(T), "接近")
            q_pre = p1[-1] if p1 else q0
            p2 = r.plan.line(q_pre, T, "下探", ignore=ignore)
            T_real = r.kin.tcp(p2[-1])
            grasp_tf = np.linalg.inv(T_real) @ item.pose
            rims = [r.world.items[a].top() for a in r.world.ancestors(name)]
            clear = max([0.0, *(top + C.CLEARANCE_M + 0.005 - item.bottom() for top in rims)])
            lift = _lifted(T_real, max(C.LIFT_M, clear))
            p3 = r.plan.line(
                p2[-1], lift, "抬起", ignore=[*ignore, *r.tables()], held=(name, grasp_tf)
            )
        except PlanError as exc:
            errors.append(str(exc))
            continue
        plan = Plan()
        plan.add("open", "张爪", C.JAW_OPEN_M)
        plan.move("接近", p1)
        plan.move("下探", p2, C.CONTACT_SPEED)
        plan.add("grasp", "合爪", _grasp_spec(item), Planner.back(p2, q_pre))

        def attach(tf=grasp_tf):
            r.world.held, r.world.grasp_tf = name, tf
            r.world.items[name].container = None

        plan.add("effect", "记为抓住", attach)
        plan.move("抬起", p3, C.CONTACT_SPEED)
        return plan
    raise _all_failed(errors, f"{name}_unreachable")


def plan_place(r: Robot, args):
    name = args.object
    r.held_as(name)
    taught = C.named_pose_rad(args.target)
    T_drop = r.kin.tcp(taught)
    obj_pose = T_drop @ r.world.grasp_tf
    item = r.world.items[name]
    below = [
        i
        for i in r.world.items.values()
        if i.name != name
        and i.name not in r.world.contents(name)
        and i.contains_points(np.array([[obj_pose[0, 3], obj_pose[1, 3], i.pose[2, 3]]]), 0.0)
    ]
    floor = max((i.top() for i in below), default=r.world.table_z())
    bottom = float((item.corners() - item.pose[:3, 3] + obj_pose[:3, 3])[:, 2].min())
    if bottom < floor - 0.005:
        raise PlanError(f"place_{args.target}_below_surface")
    pose = r.release_pose(name, obj_pose[:2, 3], max(bottom, floor + C.PLACE_GAP_M))
    pose[:3, :3] = obj_pose[:3, :3]
    return r.put_down(name, [pose], [b.name for b in below], f"放到{args.target}")


def plan_verify(r: Robot, args):
    if r.world.held == args.object:
        return False
    T = r.kin.tcp(C.named_pose_rad(args.target))
    item = r.world.item(args.object)
    return bool(np.linalg.norm(item.pose[:2, 3] - T[:2, 3]) < 0.05)


def plan_reorient(r: Robot, args):
    name = args.object
    held = r.held_as(name)
    item = r.world.items[name]
    if (args.orientation == "upright" and item.upright) or (
        args.orientation == "upside_down" and item.upside_down
    ):
        return True
    q0 = r.q
    up = _lifted(r.kin.tcp(q0), max(0.0, C.CARRY_Z_M - r.kin.tcp(q0)[2, 3]))
    try:
        p1 = r.plan.line(q0, up, "抬高", held=held)
        target = np.array([0.0, 0.0, 1.0 if args.orientation == "upright" else -1.0])
        current = item.pose[:3, 2]
        axis = np.cross(current, target)
        if np.linalg.norm(axis) < 1e-6:
            axis = r.kin.tcp(p1[-1] if p1 else q0)[:3, 0]
        angle = math.acos(float(np.clip(current @ target, -1.0, 1.0)))
        pivot = r.kin.tcp(p1[-1] if p1 else q0)[:3, 3]
        p2 = r.plan.rotate_about(p1[-1] if p1 else q0, axis, angle, pivot, "翻转", held=held)
    except PlanError:
        raise PlanError("reorient_unreachable") from None
    plan = Plan()
    plan.move("抬高", p1)
    plan.move("翻转", p2, C.CONTACT_SPEED)
    return plan


def plan_place_on(r: Robot, args):
    name, surface = args.object, args.surface
    r.held_as(name)
    item, base = r.world.items[name], r.world.item(surface)
    if base.kind == "container":
        raise PlanError("surface_is_container_use_seat_in")
    if base.kind == "table":
        home = r.world.home.get(name, item.pose)
        xy = home[:2, 3]
    else:
        if not r.footprint_fits(item, base, 0.0):
            raise PlanError(f"{name}_larger_than_{surface}")
        xy = base.pose[:2, 3]
    pose = r.release_pose(name, xy, base.top() + C.PLACE_GAP_M)
    for other in r.world.items.values():
        if other.name in {name, surface, *r.world.contents(name)} or other.kind == "table":
            continue
        if other.top() > base.top() - 0.002 and np.all(
            np.abs(other.pose[:2, 3] - pose[:2, 3]) < item.half[:2] + other.half[:2]
        ):
            raise PlanError(f"{surface}_occupied_by_{other.name}")
    poses = [r.yawed(pose, d) for d in PLACE_YAWS_DEG]
    if base.kind != "table":
        poses = [p for p in poses if r.footprint_fits(item, base, 0.0, p)]
    return r.put_down(name, poses, [surface, *r.tables()], f"放到{surface}上")


def plan_seat_in(r: Robot, args):
    name, cname = args.object, args.container
    r.held_as(name)
    item, box = r.world.items[name], r.world.item(cname)
    if box.kind != "container":
        raise PlanError(f"{cname}_not_a_container")
    if not box.upright:
        raise PlanError(f"{cname}_not_upright")
    if r.footprint_fits(item, box, C.SEAT_MARGIN_M):
        if box.seat_depth_m is not None:
            bottom = box.top() - float(box.seat_depth_m)
        else:
            bottom = box.bottom() + C.PLACE_GAP_M
        inside = [r.world.items[n] for n in r.world.contents(cname) if n != name]
        bottom = max([bottom, *(i.top() + C.PLACE_GAP_M for i in inside)])
        container = cname
    else:
        r.log(f"  {name} 比 {cname} 的口大，架在口上")
        bottom = box.top() + C.PLACE_GAP_M
        container = None
    pose = r.release_pose(name, box.pose[:2, 3], bottom)
    poses = [r.yawed(pose, d) for d in PLACE_YAWS_DEG]
    if container is not None:
        poses = [p for p in poses if r.footprint_fits(item, box, C.SEAT_MARGIN_M, p)]
    ignore = [cname, *r.world.contents(cname), *r.world.ancestors(cname), *r.world.supports(cname)]
    return r.put_down(name, poses, ignore, f"放进{cname}", container=container)


def plan_set_control(r: Robot, args):
    r.need_empty()
    item = r.world.item(args.appliance)
    if item.kind != "appliance" or not item.control:
        raise PlanError(f"{args.appliance}_has_no_control")
    if item.state == args.state:
        return True
    ctl = item.control
    point = (item.pose @ np.array([*ctl["point"], 1.0]))[:3]
    press = float(ctl.get("press_m", C.PRESS_DEPTH_M))
    errors = []
    # 按下时夹爪尖会低于灶面，灶和桌面都要忽略。
    ignore = [args.appliance, *r.tables()]
    for yaw in (0.0, 90.0, 180.0, 270.0, 45.0, 135.0):
        rot = down_rot((math.cos(math.radians(yaw)), math.sin(math.radians(yaw))))
        T = homo(rot, point)
        T_press = _lifted(T, -press)
        try:
            q0 = r.q
            p1 = r.plan.transit(q0, r.approach(T), "去按键上方")
            p2 = r.plan.line(p1[-1] if p1 else q0, T_press, "按下", ignore=ignore)
            p3 = r.plan.line(p2[-1], r.approach(T), "抬起", ignore=ignore)
        except PlanError as exc:
            errors.append(str(exc))
            continue
        plan = Plan()
        plan.add("close", "合爪当指头")
        plan.move("去按键上方", p1)
        plan.move("按下", p2, C.CONTACT_SPEED)
        plan.add("wait", "按住", 0.3)
        plan.move("抬起", p3, C.CONTACT_SPEED)

        def switched():
            item.state = args.state

        plan.add("effect", "记录开关状态", switched)
        return plan
    raise _all_failed(errors, "control_unreachable")


def plan_pour(r: Robot, args):
    name, tname = args.source, args.target
    held = r.held_as(name)
    item, target = r.world.items[name], r.world.item(tname)
    obj_over = item.pose.copy()
    obj_over[:2, 3] = target.pose[:2, 3]
    obj_over[2, 3] += target.top() + C.POUR_ABOVE_M - item.bottom()
    q0 = r.q
    errors = []
    # 换几个朝向端过去；倾倒时绕物体中心转或绕夹爪转（后者手腕动得少），哪个通用哪个。
    for yaw in PLACE_YAWS_DEG:
        T_over = r.yawed(obj_over, yaw) @ np.linalg.inv(r.world.grasp_tf)
        try:
            p1 = r.plan.transit(q0, T_over, "端到上方", held=held)
        except PlanError as exc:
            errors.append(str(exc))
            continue
        q_over = p1[-1] if p1 else q0
        T_now = r.kin.tcp(q_over)
        for pivot in ((T_now @ r.world.grasp_tf)[:3, 3], T_now[:3, 3]):
            try:
                p2, degrees = r.plan.tilt(q_over, C.POUR_TILT_DEG, pivot, "倾倒", held=held)
                break
            except PlanError as exc:
                errors.append(str(exc))
        else:
            continue
        break
    else:
        raise _all_failed(errors, "pour_unreachable")
    r.log(f"  倾倒角 {degrees:.0f}°" + ("（没过水平，口没有完全朝下）" if degrees <= 90 else ""))
    plan = Plan()
    plan.move("端到上方", p1)
    plan.move("倾倒", p2, C.CONTACT_SPEED)
    plan.add("wait", "停一会儿", C.POUR_DWELL_S)

    def emptied():
        contents = [n for n in r.world.contents(name) if r.world.items[n].container == name]
        for n in contents:
            moved = r.world.items[n]
            if target.kind == "container":
                floor = max(
                    [
                        target.bottom() + C.PLACE_GAP_M,
                        *(r.world.items[c].top() for c in r.world.contents(tname)),
                    ]
                )
                moved.container = tname
            else:
                floor = target.top()
                moved.container = None
            moved.pose = homo(np.eye(3), (*target.pose[:2, 3], floor + moved.half[2]))

    plan.add("effect", "内容物倒出", emptied)
    plan.move("回正", Planner.back(p2, q_over), C.CONTACT_SPEED)
    return plan


def _tool_tip(r: Robot, name, T):
    """工具长方体在 TCP 位姿 ``T`` 下的最低点（世界系）。"""
    item = r.world.items[name]
    pose = T @ r.world.grasp_tf
    corners = (item.corners() - item.pose[:3, 3]) @ item.pose[:3, :3] @ pose[:3, :3].T + pose[:3, 3]
    return corners[np.argmin(corners[:, 2])]


def plan_stir(r: Robot, args):
    name, vname = args.tool, args.vessel
    held = r.held_as(name)
    vessel = r.world.item(vname)
    if vessel.kind != "container":
        raise PlanError(f"{vname}_not_a_container")
    tool = r.world.items[name]
    radius = float(min(vessel.half[:2]) - max(tool.half[:2]) - C.STIR_WALL_M)
    if radius <= 0.005:
        raise PlanError(f"{name}_too_wide_for_{vname}")
    radius = min(radius, 0.04)
    depth = min(C.STIR_DEPTH_M, 0.6 * vessel.size[2])
    q0 = r.q
    T0 = r.kin.tcp(q0)
    tip = _tool_tip(r, name, T0)
    T_in = _shifted(
        T0, (vessel.pose[0, 3] - tip[0], vessel.pose[1, 3] - tip[1], vessel.top() - depth - tip[2])
    )
    ignore = [vname, *r.world.contents(vname), *r.world.ancestors(vname)]
    p1 = r.plan.transit(q0, _lifted(T_in, depth + RIM_CLEAR_M), "到容器上方", held=held)
    q1 = p1[-1] if p1 else q0
    p2 = r.plan.line(q1, T_in, "伸进去", ignore=ignore, held=held)
    steps = 24
    frames = [
        _shifted(
            T_in,
            (
                radius * math.cos(2 * math.pi * k / steps) - radius,
                radius * math.sin(2 * math.pi * k / steps),
                0.0,
            ),
        )
        for k in range(1, steps * C.STIR_TURNS + 1)
    ]
    p3 = r.plan.frames(p2[-1], frames, "搅拌", ignore=ignore, held=held)
    p4 = r.plan.line(p3[-1], _lifted(T_in, depth + RIM_CLEAR_M), "抽出", ignore=ignore, held=held)
    plan = Plan()
    plan.move("到容器上方", p1)
    plan.move("伸进去", p2, C.CONTACT_SPEED)
    plan.move("搅拌", p3, C.CONTACT_SPEED)
    plan.move("抽出", p4, C.CONTACT_SPEED)
    return plan


def plan_scoop(r: Robot, args):
    name, sname, tname = args.tool, args.source, args.target
    held = r.held_as(name)
    source, target = r.world.item(sname), r.world.item(tname)
    depth = min(C.STIR_DEPTH_M, 0.6 * source.size[2])
    q0 = r.q
    T0 = r.kin.tcp(q0)
    tip = _tool_tip(r, name, T0)
    T_in = _shifted(
        T0, (source.pose[0, 3] - tip[0], source.pose[1, 3] - tip[1], source.top() - depth - tip[2])
    )
    ignore = [sname, *r.world.contents(sname), *r.world.ancestors(sname)]
    sweep = min(0.03, float(min(source.half[:2])) * 0.5)
    p1 = r.plan.transit(q0, _lifted(T_in, depth + RIM_CLEAR_M), "到源上方", held=held)
    q1 = p1[-1] if p1 else q0
    p2 = r.plan.line(q1, T_in, "舀下去", ignore=ignore, held=held)
    p3 = r.plan.line(p2[-1], _shifted(T_in, (sweep, 0.0, 0.0)), "刮一下", ignore=ignore, held=held)
    p4 = r.plan.line(
        p3[-1], _lifted(r.kin.tcp(p3[-1]), depth + RIM_CLEAR_M), "舀起", ignore=ignore, held=held
    )
    T_out = r.kin.tcp(p4[-1])
    tip_out = _tool_tip(r, name, T_out)
    T_over = _shifted(
        T_out,
        (
            target.pose[0, 3] - tip_out[0],
            target.pose[1, 3] - tip_out[1],
            max(0.0, target.top() + C.POUR_ABOVE_M - tip_out[2]),
        ),
    )
    p5 = r.plan.transit(p4[-1], T_over, "端到目标上方", held=held)
    q5 = p5[-1] if p5 else p4[-1]
    p6, _ = r.plan.tilt(q5, (90.0, 60.0), _tool_tip(r, name, r.kin.tcp(q5)), "倒出", held=held)
    plan = Plan()
    for label, path, speed in (
        ("到源上方", p1, None),
        ("舀下去", p2, C.CONTACT_SPEED),
        ("刮一下", p3, C.CONTACT_SPEED),
        ("舀起", p4, C.CONTACT_SPEED),
        ("端到目标上方", p5, None),
        ("倒出", p6, C.CONTACT_SPEED),
    ):
        plan.move(label, path, speed)
    plan.add("wait", "停一会儿", 0.8)
    plan.move("回正", Planner.back(p6, q5), C.CONTACT_SPEED)
    return plan


def plan_shake_off(r: Robot, args):
    name = args.object
    held = r.held_as(name)
    q0 = r.q
    T0 = r.kin.tcp(q0)
    side = np.cross([0.0, 0.0, 1.0], T0[:3, 0])
    side = side / np.linalg.norm(side) if np.linalg.norm(side) > 1e-6 else np.array([0.0, 1.0, 0.0])
    errors = []
    # 抬不了那么高就少抬一点，再不行原地抖（前一步 pick 已经把它抬离容器了）。
    for lift in (C.SHAKE_LIFT_M, C.SHAKE_LIFT_M / 2, 0.0):
        T_up = _lifted(T0, lift)
        frames = []
        for _ in range(C.SHAKE_CYCLES):
            for s in (1.0, -1.0):
                frames.append(_shifted(T_up, side * s * C.SHAKE_AMPLITUDE_M))
        frames.append(T_up)
        dense, prev = [], T_up
        for f in frames:
            dense += interpolate(prev, f)
            prev = f
        try:
            p1 = r.plan.line(q0, T_up, "抬起", held=held) if lift > 0 else []
            p2 = r.plan.frames(p1[-1] if p1 else q0, dense, "抖", held=held)
        except PlanError as exc:
            errors.append(str(exc))
            continue
        plan = Plan()
        plan.move("抬起", p1)
        plan.move("抖", p2, min(C.MOVE_SPEED * 2, 40))
        return plan
    raise _all_failed(errors, "shake_unreachable")


PLANNERS = {
    "observe": plan_observe,
    "move_named_pose": plan_move_named_pose,
    "move_relative": plan_move_relative,
    "set_gripper": plan_set_gripper,
    "pick": plan_pick,
    "place": plan_place,
    "verify": plan_verify,
    "reorient": plan_reorient,
    "place_on": plan_place_on,
    "seat_in": plan_seat_in,
    "set_control": plan_set_control,
    "pour": plan_pour,
    "stir": plan_stir,
    "scoop": plan_scoop,
    "shake_off": plan_shake_off,
}

# 执行完再按世界状态校验一次（真机没有额外传感器，抓取靠夹爪力矩判定）。
VERIFY = {
    "move_named_pose": lambda r, a: r.world.pose == a.pose,
    "set_gripper": lambda r, a: r.world.held is None,
    "pick": lambda r, a: r.world.held == a.object,
    "place": lambda r, a: r.world.held is None,
    "reorient": lambda r, a: r.world.items[a.object].upright == (a.orientation == "upright"),
    "place_on": lambda r, a: r.world.held is None,
    "seat_in": lambda r, a: r.world.held is None,
    "set_control": lambda r, a: r.world.items[a.appliance].state == a.state,
    "pour": lambda r, a: r.world.held == a.source and r.world.items[a.source].upright,
    "stir": lambda r, a: r.world.held == a.tool,
    "scoop": lambda r, a: r.world.held == a.tool,
    "shake_off": lambda r, a: r.world.held == a.object,
}


# ---------------------------------------------------------------------------
# 合同 + 绑定
# ---------------------------------------------------------------------------
def _move_contract(r: Robot):
    names = tuple(r.poses)
    args = create_model("FafuMoveArgs", __base__=Boundary, pose=(Literal[names], ...))
    return dataclasses.replace(
        SKILLS["move_named_pose"],
        arguments=args,
        description="Move to a named joint pose recorded on the real arm: " + ", ".join(names),
    )


DESCRIPTIONS = {
    "move_relative": "Offset the TCP in world XYZ meters at most 10 cm along a Cartesian line, "
    "keeping orientation; the gripper and held object are collision checked",
    "set_control": "Press an appliance's control button with the closed gripper to switch it on "
    "or off; the state is recorded, there is no sensor feedback from the appliance",
}


# 一个节点的技能目录必须装进一条 64KB 的 Zenoh 消息；15 份完整 SKILL.md 会超，
# 所以完整指南只挂在 pick 上，其余技能指过去。
GUIDE_SKILL = "pick"
GUIDE_POINTER = f"Shared robot skill guide: see the instructions of {GUIDE_SKILL}."


def contracts(r: Robot) -> dict:
    out = {}
    for name in ACTION_SKILLS:
        entry = _move_contract(r) if name == "move_named_pose" else SKILLS[name]
        changes = {}
        if name in DESCRIPTIONS:
            changes["description"] = DESCRIPTIONS[name]
        if name != GUIDE_SKILL:
            changes["instructions"] = GUIDE_POINTER
        out[name] = dataclasses.replace(entry, **changes) if changes else entry
    return out


def bound_skills(r: Robot) -> list:
    """绑定成 ActionExecutor 能用的技能：异步包一层，真正执行放到线程里。"""
    skills = []
    for name, entry in contracts(r).items():

        async def handler(world, args, stop, progress, _name=name):
            loop = asyncio.get_running_loop()

            def report(value):
                loop.call_soon_threadsafe(progress, value)

            return await asyncio.to_thread(r.execute_skill, _name, args, stop, report)

        skills.append(entry.bind(handler))
    return skills


def make_executor(r: Robot, journal_path) -> ActionExecutor:
    return ActionExecutor(
        journal_path,
        state=r.world,
        skills=bound_skills(r),
        backend="fafu",
        features_extra={
            "hardware": r.execute,
            "verification": "fafu_world_state",
            "stop_scope": "between_path_segments",
            "controller_flush": False,
            "unsupported": {
                "perception": "Object poses come from fafu_real/kitchen_scene.py, not the camera.",
                "arm_collision": "Only the gripper and held object are collision checked, "
                "not the arm links.",
                "contents_physics": "Container contents move in the world state only.",
            },
        },
    )
