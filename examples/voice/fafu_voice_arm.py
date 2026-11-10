"""用 Python 3.10 独占 fdcanusb，按一行一条 JSON 驱动 Fafu 机械臂。

本文件只由 examples/voice/12_arm_desk.py 拉起，解释器是
~/miniconda3/envs/panthera/bin/python。标准输出只写 JSON；SDK 日志走标准错误。

方向是基座坐标，距离由指令里的 dx、dy、dz 决定，单位米。停止从另一线程调用 emergency_stop。
回到初始位置会把六个关节缓慢收到 0 弧度。退出时先抱闸再关串口。
"""

import json
import math
import queue
import sys
import threading
from pathlib import Path

SDK = Path("/home/fafu_robot2/zx/fafu_arm_sdk/fafu_robot_python")
CFG = SDK / "robot.cfg"
PORT = "/dev/ttyACM0"
NUDGE_SPEED = 15
HOME_SPEED = 8

_out = sys.stdout
sys.stdout = sys.stderr
sys.path.insert(0, str(SDK))


def reply(payload):
    print(json.dumps(payload, ensure_ascii=False), file=_out, flush=True)


def _ready_to_move(arm, robot_state):
    state = arm.state
    if state is robot_state.DEAD:
        raise RuntimeError("通信已断开，没有自动恢复。")
    if state is robot_state.ESTOP:
        arm.resume()
        state = arm.state
    if state in {robot_state.DISABLED, robot_state.BRAKED}:
        arm.enable()


def _nudge(arm, message):
    import numpy as np

    delta = [
        float(message.get("dx") or 0),
        float(message.get("dy") or 0),
        float(message.get("dz") or 0),
    ]
    if not all(math.isfinite(value) for value in delta):
        raise ValueError("位移不是有效数字。")
    if not any(delta):
        raise ValueError("没有位移。")
    position, rotation = arm.get_pose()
    target = np.asarray(position, dtype=float) + np.asarray(delta, dtype=float)
    arm.move_p(target, rotation, speed=NUDGE_SPEED, block=True)


def _shutdown(arm, robot_state):
    state = arm.state
    if state is robot_state.ESTOP:
        arm.resume()
        state = arm.state
    if state is robot_state.IDLE:
        arm.brake()
    arm.close_connection()


def serve(arm, robot_state):
    inbox = queue.Queue()

    def read_stdin():
        for line in sys.stdin:
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                reply({"ok": False, "status": "指令不是 JSON。"})
                continue
            command = message.get("cmd")
            if command == "stop":
                arm.emergency_stop()
                reply({"ok": True, "status": "已急停。"})
                continue
            if command == "shutdown":
                if arm.state in {robot_state.MOVING, robot_state.GRASPING}:
                    arm.emergency_stop()
                inbox.put(message)
                continue
            inbox.put(message)

    threading.Thread(target=read_stdin, daemon=True).start()
    while True:
        message = inbox.get()
        command = message.get("cmd")
        try:
            if command == "shutdown":
                _shutdown(arm, robot_state)
                reply({"ok": True, "status": "已抱闸并断开。", "bye": True})
                return
            _ready_to_move(arm, robot_state)
            if command == "nudge":
                _nudge(arm, message)
                status = "这一步已到位。"
            elif command == "home":
                arm.go_home(speed=HOME_SPEED, block=True)
                status = "已回到各关节零位。"
            elif command == "open":
                arm.open_gripper()
                status = "夹爪已打开。"
            elif command == "close":
                arm.close_gripper()
                status = "夹爪已关闭。"
            else:
                status = "真机不认识这条指令。"
            reply({"ok": True, "status": status})
        except Exception as exc:
            reply({"ok": False, "status": f"没有动：{exc}"})


def main():
    if not Path(PORT).exists():
        reply({"ready": False, "error": f"找不到串口 {PORT}"})
        return
    from fafu_robot_controller import FafuRobotController, RobotState

    arm = None
    try:
        arm = FafuRobotController(
            str(CFG),
            port=PORT,
            has_gripper=True,
            gripper_motor_id=7,
            auto_enable=True,
        )
        arm.setup_dynamics()
        arm.get_pose()
        reply({"ready": True, "port": arm.port, "state": str(arm.state)})
        serve(arm, RobotState)
    except Exception as exc:
        reply({"ready": False, "error": str(exc)})
    finally:
        if arm is not None:
            try:
                arm.close_connection()
            except Exception:
                pass


if __name__ == "__main__":
    main()
