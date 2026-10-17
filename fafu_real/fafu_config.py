"""实机（fafu / Panthera-HT）开关与参数。厨具、面条的位置（关节角）在 kitchen_scene.py。

改完直接运行::

    python fafu_real/main.py              # 按 RUN_MODE 运行
    python fafu_real/main.py --mode read_joints
    python fafu_real/main.py --mode sequence --execute

命令行 ``--mode`` / ``--execute`` / ``--dry-run`` / ``--skills`` / ``--no-skills``
会临时覆盖本文件。
"""

from __future__ import annotations

import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 1. 依赖的比赛仓库（wrs-competition 的 wrs-demo 分支）
# ---------------------------------------------------------------------------
# 里面要有：fafu_arm_sdk-main/（SDK + robot.cfg + fafu_motor.pyd）、demo/real/（RealArm、D405、
# servo_j）、wrs/（含 Panthera-HT 模型和 meshes1/*.stl 网格）。环境变量 WRS_COMPETITION_DIR 优先。
COMPETITION_ROOT = os.environ.get("WRS_COMPETITION_DIR") or r"F:\wrs-main-competition"

# ---------------------------------------------------------------------------
# 2. 基本开关
# ---------------------------------------------------------------------------
# True 才真的给机械臂下发；False = dry-run，只规划、打印，不连硬件。
EXECUTE_ON_REAL = False
# 走 servo_j 连续喂帧（和比赛 demo 一样）。False 退回逐点 move_j。
USE_SERVO_J = True
# 串口；None = robot.cfg 里的 port（默认 auto）。
SERIAL_PORT = None
# 关节速度档（1~40，和 demo/real 的 speed 一样，10 ≈ 18°/s）。首次上电保持低速。
MOVE_SPEED = 5
# 下探、合爪附近、搅拌、倾倒这些贴近物体的段用更慢的档。
CONTACT_SPEED = 3
# 每一步执行前在终端按回车确认（只在真机执行时生效）。
CONFIRM_EACH_STEP = True
# 一条关节路径分段下发，每段这么多个点（约 2° 一个点）。段与段之间检查停止请求，
# 看板 / Agent 的"停止"在段边界生效；每段 servo 会加减速一次，段太短动作会一顿一顿。
PATH_CHUNK = 60
# 断开时关节怎么处理："hold" 顶住 / "brake" 阻尼 / "stop" 松开（会下坠）。
RELEASE_MODE_ON_CLOSE = "hold"
# 退出前回 EXIT_POSE（手里还拿着东西时不回，避免带着东西乱走）。
HOME_ON_EXIT = True
EXIT_POSE = "home"
# dry-run 时虚拟臂从这个命名位形开始。
DRY_RUN_START_POSE = "home"

# ---------------------------------------------------------------------------
# 3. RUN_MODE
# ---------------------------------------------------------------------------
# read_joints  读关节角 + TCP/法兰/相机坐标（类似 observe），方便记录示教位
# goto_pose    移动到命名位形 MOVE_POSE（home / zero / look / 你记录的位形）
# goto_object  TCP 竖直朝下移动到物体 MOVE_OBJECT 正上方 MOVE_ABOVE_M 处
# gripper      夹爪 GRIPPER_COMMAND（open / close）
# skill        执行一个技能 SKILL_NAME(**SKILL_ARGS)
# sequence     依次执行 TASK_SEQUENCE（默认是煮面流程）
# check        强制 dry-run，把 TASK_SEQUENCE 和每个技能都规划一遍，不连硬件
# node         作为 WRSAgentOS 机器人节点上线，看板 / Agent 通过技能调用
# calib        眼在手上手眼标定（D405 + 真机）
RUN_MODE = "read_joints"
RUN_MODES = (
    "read_joints",
    "goto_pose",
    "goto_object",
    "gripper",
    "skill",
    "sequence",
    "check",
    "node",
    "calib",
)
# True：goto_pose / goto_object / gripper 也走注册好的技能（参数校验 + 前置条件 + 世界状态）。
# False：直接调用底层运动，不检查手里有没有东西，只用于调试。skill / sequence / node 始终用技能。
USE_SKILLS = True

MOVE_POSE = "look"
MOVE_OBJECT = "pot"
MOVE_ABOVE_M = 0.10
GRIPPER_COMMAND = "open"

SKILL_NAME = "pick"
SKILL_ARGS = {"object": "strainer"}

# 煮面：漏勺进锅 → 面进漏勺 → 漏勺出锅、沥水 → 倒到盘子 → 漏勺放回桌上。
TASK_SEQUENCE = [
    ("move_named_pose", {"pose": "home"}),
    ("pick", {"object": "strainer"}),
    ("seat_in", {"object": "strainer", "container": "pot"}),
    ("pick", {"object": "noodles"}),
    ("seat_in", {"object": "noodles", "container": "strainer"}),
    ("pick", {"object": "strainer"}),
    ("shake_off", {"object": "strainer"}),
    ("pour", {"source": "strainer", "target": "plate"}),
    ("place_on", {"object": "strainer", "surface": "table"}),
    ("move_named_pose", {"pose": "home"}),
]

# ---------------------------------------------------------------------------
# 4. 命名位形（关节角，度，J1~J6）
# ---------------------------------------------------------------------------
# 用 read_joints 读真机：python fafu_real/read_joints.py --save look
# --save 写进 taught_poses.json，会覆盖这里同名的值。B / C 是 place 技能的两个放置位。
NAMED_POSES_DEG = {
    "home": (0.79, 0.07, 0.22, -5.69, 5.58, -0.40),
    "zero": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "look": (9.33, 71.74, 78.36, -85.76, 0.41, 13.11),
}
TAUGHT_POSES_JSON = os.path.join(HERE, "taught_poses.json")

# ---------------------------------------------------------------------------
# 5. 运动规划参数（世界系，米；基座原点、Z 朝上，和比赛 demo 一致）
# ---------------------------------------------------------------------------
TABLE_Z_M = 0.0  # 桌面高度；手眼标定把桌面标到别的高度就改这里
APPROACH_M = 0.08  # 抓取 / 放置前，沿夹爪方向在目标上方停的距离
LIFT_M = 0.08  # 抓起后竖直抬高（在容器里的东西至少抬到离开容器口）
CARRY_Z_M = 0.16  # 转场时 TCP 的最低高度（竖直朝下时 0.16 以上 x>0.35 就够不着了）
PLACE_GAP_M = 0.005  # 放下时物体底面离支撑面的空隙
SEAT_MARGIN_M = 0.005  # 放进容器时，物体外沿到容器内壁至少留这么多
CLEARANCE_M = 0.01  # 夹爪 / 手上物体离其他物体、桌面的最小距离
CART_STEP_M = 0.01  # 笛卡尔直线的插值步长
ANGLE_STEP_DEG = 5.0  # 姿态变化的插值步长
MAX_JOINT_JUMP_DEG = 25.0  # 相邻两帧任一关节跳变超过它就认为 IK 换了臂型，放弃这条路
IK_RANDOM_SEEDS = 12  # 种子解不出来时，随机再试多少次
JOINT_LIMIT_TOL_DEG = 0.5  # robot.cfg 软限位之外容许的误差
GRASP_TILT_DEG = (0.0, 20.0, 35.0)  # 顶抓够不着时依次试的倾斜角
# 夹爪碰撞盒（TCP 坐标系，米）：x 宽、y 两指方向、z 沿夹爪朝下为正。
GRIPPER_BOX_M = {"x": (-0.02, 0.02), "y": (-0.04, 0.04), "z": (-0.09, 0.01)}
JAW_OPEN_M = 0.055  # 夹爪最大开口（与 demo/real 的 SIM_JAW_OPEN_MAX 一致）
# 夹爪收多紧，按物体分。力矩是 robot.cfg 的原始单位（demo 阈值约 220、电机上限约 300）。
#   mode="pinch"：合到 close_m（米）就停，力矩上限 effort。软的东西（面条）用这个，不会夹碎。
#                 现场捏一下：刚好夹住、面没扁，就把那个开口填进 close_m。
#   mode="force"：一直往里合，力矩到 force 就停（上限 effort）。硬的厨具用这个，夹得牢。
#                 close_m 只是标称值。夹不稳加 force，夹得太狠减 force。
GRIP = {
    "noodles": {"mode": "pinch", "close_m": 0.025, "effort": 70},
    "strainer": {"mode": "force", "close_m": 0.010, "force": 160, "effort": 220},
    "spoon": {"mode": "force", "close_m": 0.015, "force": 160, "effort": 220},
}

POUR_ABOVE_M = 0.04  # 倾倒时手上物体最低点离目标顶面的高度
# 依次尝试的倾倒角。过 90° 口才朝下；竖直顶抓漏勺边沿时这台臂离线算只能倾到约 85~90°，
# 想倒得更干净就把漏勺 at_deg 记在把手上 / 斜着抓。
POUR_TILT_DEG = (120.0, 100.0, 90.0, 85.0)
POUR_DWELL_S = 1.5  # 倾倒到底后停多久
STIR_TURNS = 2
STIR_DEPTH_M = 0.03  # 工具尖端伸到容器口以下多深（不超过容器高的 60%）
STIR_WALL_M = 0.01  # 工具离容器内壁至少多远
SHAKE_LIFT_M = 0.05
SHAKE_AMPLITUDE_M = 0.02
SHAKE_CYCLES = 3
PRESS_DEPTH_M = 0.01  # set_control 按键默认按下深度

# ---------------------------------------------------------------------------
# 6. 眼在手上（D405 装在法兰上），和比赛 demo 同一种标定
# ---------------------------------------------------------------------------
HANDEYE_JSON = os.path.join(HERE, "handeye_eye_in_hand.json")
CAMERA_RESOLUTION = "mid"
CAMERA_SERIAL = None
MAX_DEPTH_M = 1.2
# 标定窗口里显示点云的世界范围
WORKSPACE_BOUNDS_M = {"x": (0.0, 0.6), "y": (-0.4, 0.4), "z": (-0.05, 0.45)}

# ---------------------------------------------------------------------------
# 7. node 模式（接 WRSAgentOS 看板 / Agent）
# ---------------------------------------------------------------------------
ENDPOINT = "tcp/127.0.0.1:7461"
ENV_ID = "fafu-kitchen"
UI_PORT = 8011
# 看板点技能要经过 Agent 节点下发任务，所以默认一起起。Agent 不接大模型（live_model=False），
# 要让它听自然语言，在看板里填大模型设置即可（key 不会写进这里）。
NODE_START_AGENT = True
NODE_START_UI = True

# ---------------------------------------------------------------------------
if RUN_MODE not in RUN_MODES:
    raise ValueError(f"RUN_MODE 只能是 {RUN_MODES}，现在是 {RUN_MODE!r}")


class ConfigError(RuntimeError):
    pass


def named_poses_deg() -> dict[str, tuple[float, ...]]:
    """配置里的命名位形，再叠加 read_joints --save 记录的。"""
    poses = {name: tuple(map(float, deg)) for name, deg in NAMED_POSES_DEG.items()}
    if os.path.isfile(TAUGHT_POSES_JSON):
        with open(TAUGHT_POSES_JSON, encoding="utf-8") as f:
            for name, deg in json.load(f).items():
                poses[name] = tuple(map(float, deg))
    for name, deg in poses.items():
        if len(deg) != 6:
            raise ConfigError(f"命名位形 {name} 要 6 个关节角，现在是 {deg!r}")
    return poses


def named_pose_rad(name: str) -> list[float]:
    poses = named_poses_deg()
    if name not in poses:
        raise ConfigError(
            f"没有命名位形 {name!r}。先摆到位后运行 python fafu_real/read_joints.py --save {name}"
        )
    return [math.radians(v) for v in poses[name]]
