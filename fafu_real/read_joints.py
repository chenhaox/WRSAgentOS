"""读真机关节角（只读，不下发任何运动），顺带算 TCP / 法兰 / 相机的世界坐标，方便示教记录。

    python fafu_real/read_joints.py                 # 读一次
    python fafu_real/read_joints.py --watch         # 一直读，手推机械臂看数变化（Ctrl+C 退出）
    python fafu_real/read_joints.py --save look     # 读一次并记成命名位形 look
    python fafu_real/read_joints.py --dry-run       # 不连硬件，读虚拟臂（DRY_RUN_START_POSE）

记录下来的命名位形可以直接用：RUN_MODE="goto_pose" + MOVE_POSE="look"，或技能
move_named_pose(pose="look")；B / C 两个名字是 place 技能的放置位。
记物体位置：把 TCP（两指中间）对准物体，把打印的 ``"at_deg": (...)`` 贴进 kitchen_scene.py。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time

import _paths  # noqa: F401
import fafu_config as C
import handeye
import numpy as np


def _rpy_deg(rot) -> np.ndarray:
    from wrs.basis.robot_math import rotmat_to_euler

    return np.degrees(rotmat_to_euler(np.asarray(rot, dtype=float)))


def _pose_line(name, T) -> str:
    p, rpy = T[:3, 3], _rpy_deg(T[:3, :3])
    return (
        f"{name} xyz [{p[0]:+.4f}, {p[1]:+.4f}, {p[2]:+.4f}] m   "
        f"rpy [{rpy[0]:+.2f}, {rpy[1]:+.2f}, {rpy[2]:+.2f}]°"
    )


def describe(q, kin, limits, camera_mat=None) -> str:
    deg = [math.degrees(v) for v in q]
    lines = [
        "关节(°)   " + "  ".join(f"J{i + 1} {v:+8.2f}" for i, v in enumerate(deg)),
        "关节(rad) " + "  ".join(f"{v:+.4f}" for v in q),
    ]
    outside = [
        f"J{i + 1}"
        for i, (v, (lo, hi)) in enumerate(zip(q, limits, strict=True))
        if not lo <= v <= hi
    ]
    if outside:
        lines.append(f"[warn] 超出 robot.cfg 软限位：{', '.join(outside)}")
    tcp, flange = kin.tcp(q), kin.flange(q)
    lines.append(_pose_line("TCP ", tcp))
    lines.append(
        f"     夹爪朝向 z=[{tcp[0, 2]:+.3f}, {tcp[1, 2]:+.3f}, {tcp[2, 2]:+.3f}]"
        f"（竖直朝下时约为 [0, 0, -1]）"
    )
    lines.append(_pose_line("法兰", flange))
    if camera_mat is not None:
        cam = handeye.camera_pose(flange, camera_mat)
        lines.append(_pose_line("相机", cam))
    pasted = "(" + ", ".join(f"{v:.2f}" for v in deg) + ")"
    lines.append("命名位形可粘贴到 fafu_config.NAMED_POSES_DEG：" + pasted)
    lines.append('物体位置可粘贴到 kitchen_scene.py："at_deg": ' + pasted + ",")
    return "\n".join(lines)


def save_pose(name: str, q) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name):
        raise SystemExit(f"位形名只能用字母数字和 _ . -：{name!r}")
    poses = {}
    if os.path.isfile(C.TAUGHT_POSES_JSON):
        with open(C.TAUGHT_POSES_JSON, encoding="utf-8") as f:
            poses = json.load(f)
    old = poses.get(name)
    poses[name] = [round(math.degrees(v), 3) for v in q]
    with open(C.TAUGHT_POSES_JSON, "w", encoding="utf-8") as f:
        json.dump(poses, f, ensure_ascii=False, indent=2)
    note = f"（覆盖了旧值 {old}）" if old is not None else ""
    return f"已记录 {name} = {poses[name]} → {C.TAUGHT_POSES_JSON}{note}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="只读真机关节角 + TCP/法兰/相机坐标")
    p.add_argument("--watch", action="store_true", help="持续读取")
    p.add_argument("--hz", type=float, default=2.0, help="--watch 的刷新频率")
    p.add_argument("--save", metavar="NAME", help="把当前关节角记成命名位形")
    p.add_argument("--dry-run", action="store_true", help="不连硬件，读虚拟臂")
    args = p.parse_args(argv)

    from kinematics import Kinematics
    from real_arm import RealArm
    from real_config import load_real_config

    cfg = load_real_config()
    kin = Kinematics(cfg.joint_limits)
    camera_mat = handeye.try_load()
    if camera_mat is None:
        print(f"[info] 没有有效手眼标定（{C.HANDEYE_JSON}），不打印相机位姿")
    # use_servo=False：只读，不开 servo 会话，也不会有保持线程去顶关节。
    arm = RealArm(
        cfg,
        dry_run=args.dry_run,
        speed=1,
        port=C.SERIAL_PORT,
        use_servo=False,
        release_mode=C.RELEASE_MODE_ON_CLOSE,
    )
    if args.dry_run:
        arm.set_virtual_q(C.named_pose_rad(C.DRY_RUN_START_POSE))
    arm.connect()
    try:
        if args.watch:
            period = 1.0 / max(0.2, args.hz)
            while True:
                print("\n" + time.strftime("%H:%M:%S"))
                print(describe(arm.joint_values(), kin, cfg.joint_limits, camera_mat))
                time.sleep(period)
        q = arm.joint_values()
        print(describe(q, kin, cfg.joint_limits, camera_mat))
        if args.save:
            print(save_pose(args.save, q))
    except KeyboardInterrupt:
        print("\n停止读取")
    finally:
        arm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
