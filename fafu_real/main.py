"""实机入口：按 fafu_config.RUN_MODE 运行，命令行可以临时覆盖。

    python fafu_real/main.py                                  # 用 fafu_config 里的设置
    python fafu_real/main.py --mode check                     # 离线把所有技能规划一遍
    python fafu_real/main.py --mode goto_pose --pose look --execute
    python fafu_real/main.py --mode skill --skill pick --args object=noodles
    python fafu_real/main.py --mode skill --skill seat_in --args object=noodles container=strainer
    python fafu_real/main.py --mode sequence --execute
    python fafu_real/main.py --mode node --execute

``--execute`` / ``--dry-run`` 覆盖 EXECUTE_ON_REAL；``--skills`` / ``--no-skills`` 覆盖 USE_SKILLS。
真机执行时 Ctrl+C 会急停。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import traceback

import _paths  # noqa: F401
import fafu_config as C


def _parse(argv):
    p = argparse.ArgumentParser(description="fafu 实机：技能 / 示教 / 节点")
    p.add_argument("--mode", choices=C.RUN_MODES, default=C.RUN_MODE)
    run = p.add_mutually_exclusive_group()
    run.add_argument("--execute", dest="execute", action="store_true", default=None, help="真机执行")
    run.add_argument("--dry-run", dest="execute", action="store_false", help="不连硬件")
    use = p.add_mutually_exclusive_group()
    use.add_argument("--skills", dest="use_skills", action="store_true", default=None)
    use.add_argument("--no-skills", dest="use_skills", action="store_false")
    p.add_argument("--pose", default=C.MOVE_POSE, help="goto_pose 的命名位形")
    p.add_argument("--object", default=C.MOVE_OBJECT, help="goto_object 的物体")
    p.add_argument("--above", type=float, default=C.MOVE_ABOVE_M, help="goto_object 停在物体上方多高（米）")
    p.add_argument("--gripper", choices=("open", "close"), default=C.GRIPPER_COMMAND)
    p.add_argument("--skill", default=C.SKILL_NAME)
    p.add_argument("--args", nargs="+", default=None,
                   help="skill 模式的参数：object=noodles surface=plate，或一段 JSON；默认 SKILL_ARGS")
    p.add_argument("--no-confirm", action="store_true", help="真机执行时不逐步确认")
    p.add_argument("--no-ui", action="store_true", help="node 模式不起看板")
    return p.parse_args(argv)


def _say(ok, name) -> bool:
    print(f"[{name}] {'成功' if ok else '未通过校验'}")
    return bool(ok)


def run_step(robot, name, args) -> bool:
    from wrs_agent.executor import ExecutionUnknown, SkillFailure

    print(f"\n=== {name} {json.dumps(args, ensure_ascii=False)} ===")
    try:
        return _say(robot.run_skill(name, args), name)
    except SkillFailure as exc:
        print(f"[{name}] 失败（机械臂已停在安全位置）：{exc}")
    except ExecutionUnknown as exc:
        print(f"[{name}] 执行中出错，状态不确定：{exc}")
        raise
    except ValueError as exc:
        print(f"[{name}] 参数不对：{exc}")
    return False


def mode_goto_pose(robot, opts) -> bool:
    if opts.use_skills:
        return run_step(robot, "move_named_pose", {"pose": opts.pose})
    q = C.named_pose_rad(opts.pose)
    robot.arm.real.go_conf(q, label=opts.pose, speed=C.MOVE_SPEED)
    robot.sync()
    return True


def mode_goto_object(robot, opts) -> bool:
    from kinematics import down_rot, homo

    item = robot.world.item(opts.object)
    T = homo(down_rot((0.0, 1.0)), (*item.pose[:2, 3], item.top() + opts.above))
    if opts.use_skills:
        held = robot.held_as(robot.world.held) if robot.world.held else None
        robot.sync()
        path = robot.plan.transit(robot.q, T, f"去{opts.object}上方", held=held)
    else:
        q = robot.kin.ik(T, robot.q)
        if q is None:
            print(f"[goto_object] {opts.object} 上方 {opts.above:.2f}m 解不出逆解")
            return False
        from kinematics import lerp_joints

        path = lerp_joints(robot.q, q)
    from motions import Plan

    plan = Plan()
    plan.move(f"去{opts.object}上方", path)
    stop = threading.Event()
    robot.run(plan, stop, lambda v: None)
    robot.sync()
    p = robot.world.tcp[:3, 3]
    print(f"[goto_object] TCP 现在 [{p[0]:+.3f}, {p[1]:+.3f}, {p[2]:+.3f}]")
    return True


def mode_gripper(robot, opts) -> bool:
    if opts.use_skills:
        return run_step(robot, "set_gripper", {"command": opts.gripper})
    if opts.gripper == "open":
        robot.arm.open()
    else:
        robot.arm.close_jaw()
    return True


def parse_skill_args(tokens) -> dict:
    """``["object=noodles", "dz=0.05"]`` 或 ``['{"object": "noodles"}']`` → dict。"""
    text = " ".join(tokens).strip()
    if text.startswith("{"):
        return json.loads(text)
    args = {}
    for token in text.replace(",", " ").split():
        key, sep, value = token.partition("=")
        if not sep:
            raise SystemExit(f"--args 要写成 名字=值，收到 {token!r}")
        try:
            args[key] = json.loads(value)
        except ValueError:
            args[key] = value
    return args


def mode_skill(robot, opts) -> bool:
    args = parse_skill_args(opts.args) if opts.args else dict(C.SKILL_ARGS)
    return run_step(robot, opts.skill, args)


def mode_sequence(robot, opts, steps=None) -> bool:
    for name, args in steps or C.TASK_SEQUENCE:
        if not run_step(robot, name, args):
            print(f"\n序列在 {name} 停下。世界状态：\n{robot.world.describe()}")
            return False
    print(f"\n序列完成。世界状态：\n{robot.world.describe()}")
    return True


def mode_check(robot, opts) -> bool:
    """dry-run 下把整个 TASK_SEQUENCE 规划执行一遍，再把每个技能单独试一遍。"""
    print(robot.arm.describe_limits())
    ok = mode_sequence(robot, opts)
    print("\n--- 各技能单独检查（每项从新的世界状态开始）---")
    from skills import Robot

    cases = [
        ([], "observe", {}),
        ([], "set_gripper", {"command": "open"}),
        ([], "move_relative", {"dz": 0.05}),
        ([], "move_named_pose", {"pose": "look"}),
        ([], "set_control", {"appliance": "cooktop", "state": "on"}),
        ([("pick", {"object": "noodles"})], "place_on", {"object": "noodles", "surface": "plate"}),
        ([("pick", {"object": "strainer"})], "seat_in", {"object": "strainer", "container": "pot"}),
        ([("pick", {"object": "strainer"})], "shake_off", {"object": "strainer"}),
        ([("pick", {"object": "strainer"})], "pour", {"source": "strainer", "target": "plate"}),
        ([("pick", {"object": "strainer"})], "reorient", {"object": "strainer", "orientation": "upright"}),
        ([("pick", {"object": "spoon"})], "stir", {"tool": "spoon", "vessel": "pot"}),
        ([("pick", {"object": "spoon"})], "scoop", {"tool": "spoon", "source": "pot", "target": "plate"}),
    ]
    if "B" in C.named_poses_deg():
        cases.append(([("pick", {"object": "noodles"})], "place", {"object": "noodles", "target": "B"}))
    results = []
    for setup, name, args in cases:
        fresh = Robot(execute=False, logger=lambda *a, **k: None, kin=robot.kin).connect()
        try:
            for s_name, s_args in setup:
                try:
                    if not fresh.run_skill(s_name, s_args):
                        raise RuntimeError("未通过校验")
                except Exception as exc:
                    raise RuntimeError(f"准备步骤 {s_name} 失败：{exc}") from None
            passed = fresh.run_skill(name, args)
            results.append((name, "PASS" if passed else "FAIL", ""))
        except Exception as exc:
            results.append((name, "FAIL", str(exc)))
    for name, verdict, why in results:
        print(f"  {verdict}  {name:16s} {why}")
    return ok and all(v == "PASS" for _, v, _ in results)


def main(argv=None) -> int:
    opts = _parse(argv)
    execute = C.EXECUTE_ON_REAL if opts.execute is None else opts.execute
    opts.use_skills = C.USE_SKILLS if opts.use_skills is None else opts.use_skills
    if opts.mode == "check":
        execute = False
    if opts.mode == "read_joints":
        import read_joints

        return read_joints.main([] if execute else ["--dry-run"])
    if opts.mode == "calib":
        import hand_eye_calib

        return hand_eye_calib.main([] if execute else ["--no-connect"])

    from skills import Robot

    confirm = C.CONFIRM_EACH_STEP and not opts.no_confirm and opts.mode != "node"
    robot = Robot(execute=execute, confirm=confirm)
    print(f"[main] 模式 {opts.mode}，{'真机执行' if execute else 'dry-run（不连硬件）'}，"
          f"{'走技能' if opts.use_skills else '直接调用底层运动'}")
    if execute:
        print("[main] 真机：确认急停按钮在手边、工作区没人。Ctrl+C 急停。")
    robot.connect()
    q = robot.world.qs
    print("[main] 当前关节(°) " + ", ".join(f"{math.degrees(v):.2f}" for v in q))
    ok = True
    try:
        if opts.mode == "node":
            import node

            return node.run(robot, with_ui=not opts.no_ui)
        handler = {
            "goto_pose": mode_goto_pose, "goto_object": mode_goto_object, "gripper": mode_gripper,
            "skill": mode_skill, "sequence": mode_sequence, "check": mode_check,
        }[opts.mode]
        ok = handler(robot, opts)
        if opts.mode not in {"check"} and execute:
            robot.home_on_exit()
    except KeyboardInterrupt:
        print("\n[main] Ctrl+C，急停")
        robot.arm.estop()
        ok = False
    except Exception:
        traceback.print_exc()
        if execute:
            robot.arm.estop()
        ok = False
    finally:
        robot.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
