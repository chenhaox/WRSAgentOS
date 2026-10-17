# fafu_real：fafu（Panthera-HT）实机脚本

这个文件夹让 WRSAgentOS 的动作技能跑在比赛用的 fafu 六轴臂上。底层直接复用比赛仓库
[wrs-competition（wrs-demo 分支）](https://github.com/2210619703-gif/wrs-competition) 的三样东西：

- `fafu_arm_sdk-main/`：电机 SDK、`robot.cfg` 软限位
- `demo/real/`：`RealArm`（servo_j 连续下发、力控合爪、hold 断开）、`GripperMap`、`D405Camera`
- `wrs/`：带 Panthera-HT 模型的那份 WRS，用于 FK / TracIK 和标定窗口

技能的合同（名字、参数、前置条件）直接用本仓库的 `wrs_agent.skills.robot.SKILLS`，
所以看板 / Agent 看到的技能和仿真节点完全一样。现在**不接视觉**：厨具和面条固定摆放，
`kitchen_scene.py` 里给每个物体记一组关节角 `at_deg`（TCP 对准它时的关节角）。启动时用正运动学
换成世界坐标，作为场景交给 Agent，Agent 按指令自己选技能、技能自己规划路径。
以后接上 YOLO，只是把场景里的位置来源从 `at_deg` 换成识别结果，技能不用改。

## 准备

1. 本机要有完整的 wrs-competition 仓库，并且带网格文件
   `wrs/robot_sim/manipulators/panthera_ht/meshes1/*.stl`。比赛仓库的 `.gitignore` 排除了
   `*.stl`，所以从 GitHub 新拉的副本没有网格，要用你自己电脑上原来那份。
2. 把 `fafu_config.py` 里的 `COMPETITION_ROOT` 改成那份仓库的根目录（默认 `F:\wrs-competition`），
   或者设置环境变量 `WRS_COMPETITION_DIR`。
3. `fafu_motor.pyd` 要和运行用的 Python 版本匹配（比赛 demo 能连上的那个 Python 就行）。
   依赖和 demo 一样：numpy、scipy、panda3d、pyrealsense2（标定用）、trac_ik 等，
   另外 node 模式要能 `import wrs_agent`（本仓库根目录）。

所有命令都在本仓库根目录 `F:\WRSAgentOS-main` 下运行。

## 推荐顺序

| 步骤 | 命令 | 作用 |
|---|---|---|
| 1 | `python fafu_real/main.py --mode check` | 不连硬件，把煮面流程和每个技能都规划一遍 |
| 2 | `python fafu_real/read_joints.py --watch` | 手推机械臂，看关节角和 TCP 坐标 |
| 3 | `python fafu_real/read_joints.py --save look` | 记录观察位（同理记 `home`、`B`、`C`） |
| 4 | `python fafu_real/hand_eye_calib.py` | 眼在手上手眼标定 |
| 5 | 改 `kitchen_scene.py` | 手推 TCP 对准每个物体，跑 `read_joints.py`，把打印的 `"at_deg": (...)` 贴到对应物体，再跑一次 check |
| 6 | `python fafu_real/main.py --mode goto_pose --pose look --execute` | 真机第一次动，低速、逐步确认 |
| 7 | `python fafu_real/main.py --mode skill --skill pick --args object=noodles --execute` | 单个技能上真机 |
| 8 | `python fafu_real/main.py --mode sequence --execute` | 整个煮面流程 |
| 9 | `python fafu_real/main.py --mode node --execute` | 接看板 / Agent |

不加 `--execute` 时一律是 dry-run：不连硬件，只规划、打印，和真机走同一套代码
（RealArm 自带的 dry-run）。真机执行时默认每一步都要在终端回车确认（`CONFIRM_EACH_STEP`），
`Ctrl+C` 急停。

## 每个文件的作用

| 文件 | 作用 |
|---|---|
| `fafu_config.py` | **开关和参数**：是否真机执行、servo_j、速度、RUN_MODE、各模式的参数、煮面任务序列、命名位形、运动规划参数、`GRIP`（每个物体夹爪收多紧：面条按开口停 + 小力矩，厨具力控）、手眼标定文件位置、node 模式端口 |
| `kitchen_scene.py` | **厨具 / 面条的位置**：每个物体只有名字、类型和 `at_deg`（TCP 对准它时的 6 个关节角，度）。能抓的东西按记录时的夹爪方向去抓；锅 / 盘子对准顶面中心，灶对准按键。没填 `at_deg` 的用占位位置，只给离线 check 用 |
| `main.py` | 入口，按 `RUN_MODE` 分发到下面各模式；命令行参数可临时覆盖配置 |
| `read_joints.py` | **读关节角**（类似 observe，只读，不下发运动）：打印关节角（度 / 弧度）、TCP、法兰、相机（有标定时）的世界坐标，`--watch` 持续读，`--save 名字` 记成命名位形写进 `taught_poses.json` |
| `hand_eye_calib.py` | **眼在手上手眼标定**，和比赛 demo 的 `demo/hand_eye_calib.py` 同一套做法：仿真臂跟着真机关节动，点云按 `FK(法兰) @ T_flange_cam` 变到世界系，键盘把点云推到和桌面 / 厨具长方体重合 |
| `handeye.py` | 标定文件读写，格式和 demo 的 `handeye_eye_in_hand.json` 完全一样，demo 标好的文件可直接拷来用 |
| `skills.py` | **15 个动作技能的真机实现**：规划 → 执行 → 更新世界状态 → 校验；并把它们 `bind` 到 WRSAgentOS 的合同上，生成 `ActionExecutor` |
| `motions.py` | 运动规划：笛卡尔直线、抬高转场、绕点倾斜，每一帧做夹爪 / 手上物体的碰撞检查 |
| `kinematics.py` | Panthera-HT 的 FK / IK（比赛仓库的 TracIK），每个解都按真机 `robot.cfg` 软限位检查 |
| `world.py` | 世界状态：每个物体在哪、手上拿着什么、什么装在什么里面、灶开没开；生成 WRSAgentOS 的 `SceneData` 快照 |
| `arm.py` | 对 `demo/real/real_arm.RealArm` 的薄封装：夹爪用米、路径分段下发、段与段之间检查停止请求 |
| `node.py` | node 模式：起 zenohd 路由器、本进程的机器人节点（`node_type="wrs"`）、Agent 节点和看板 |
| `_paths.py` | 把比赛仓库挂到 `sys.path` 最前面（`import wrs` 要解析到带 Panthera-HT 的那份），缺文件时给出明确提示 |

运行后生成的文件：`taught_poses.json`（`read_joints --save` 记录的位形）、
`handeye_eye_in_hand.json`（标定结果）、`.local/runs/fafu-real/`（node 模式的日志和记录）。

## RUN_MODE

在 `fafu_config.py` 里设 `RUN_MODE`，或命令行 `--mode`：

| 模式 | 作用 | 相关配置 / 参数 |
|---|---|---|
| `read_joints` | 读关节角和坐标 | — |
| `goto_pose` | 移动到某个命名位形 | `MOVE_POSE` / `--pose` |
| `goto_object` | TCP 竖直朝下，移动到某个物体正上方 | `MOVE_OBJECT`、`MOVE_ABOVE_M` / `--object`、`--above` |
| `gripper` | 张开 / 合上夹爪 | `GRIPPER_COMMAND` / `--gripper` |
| `skill` | 执行一个技能 | `SKILL_NAME`、`SKILL_ARGS` / `--skill`、`--args object=noodles` |
| `sequence` | 依次执行 `TASK_SEQUENCE`（默认煮面流程） | `TASK_SEQUENCE` |
| `check` | 强制 dry-run，规划整个序列并单独检查每个技能 | — |
| `node` | 作为 WRSAgentOS 机器人节点上线，配看板和 Agent | `ENDPOINT`、`ENV_ID`、`UI_PORT`、`NODE_START_AGENT`、`NODE_START_UI` |
| `calib` | 手眼标定（不加 `--execute` 时不连真机，用 `look` 位形） | `HANDEYE_JSON`、`CAMERA_*` |

`USE_SKILLS`（或 `--skills` / `--no-skills`）决定 `goto_pose`、`goto_object`、`gripper`
是否经过技能：走技能时有参数校验、前置条件（例如手上有东西不让张爪）、碰撞检查和世界状态更新；
不走技能时直接调底层运动，只用来调试。`skill` / `sequence` / `node` 始终走技能。

## 已注册的技能

全部是动作型技能（TTS / STT 之类以后再接）：

| 技能 | 真机上做什么 |
|---|---|
| `observe` | 读一次关节角，刷新世界状态 |
| `move_named_pose` | 关节插值到命名位形；可选位形就是 `fafu_config` + `taught_poses.json` 里的名字 |
| `move_relative` | TCP 沿世界 XYZ 平移（≤10cm），笛卡尔直线，有碰撞检查 |
| `set_gripper` | 张开 / 合上；手上有东西时拒绝 |
| `pick` | 张爪 → 转场到抓取点上方 → 下探 → 力控合爪 → 抬起；没检测到抓住就张开退回并报 `grasp_not_detected` |
| `place` | 把手上的东西送到示教位 `B` / `C` 放下（先用 `read_joints --save B` 记录） |
| `verify` | 检查物体是否已放在 `B` / `C`、手上为空（不动） |
| `reorient` | 已经是要求的朝向就直接成功；否则抬高后翻转，够不着报 `reorient_unreachable` |
| `place_on` | 放到盘子 / 灶 / 桌面上；检查放不放得下、上面有没有别的东西；放桌面时放回它开机时的位置 |
| `seat_in` | 放进容器：放得下就放到口下 `seat_depth_m`（或容器底），放不下就架在口上 |
| `set_control` | 合爪当手指按灶的按键，记录开关状态（灶本身没有反馈） |
| `pour` | 端到目标上方，倾斜、停留、回正；容器里的东西在世界状态里转到目标 |
| `stir` | 把工具伸进容器转圈，再抽出来 |
| `scoop` | 工具伸进源容器、刮一下、舀起、端到目标上方倾倒 |
| `shake_off` | 抬起后左右抖几下沥水 |

所有逆解和碰撞检查在动之前做完；一帧不通过整个技能就不执行，报 `unreachable:…` 或
`collision:…`（失败原因会写清是哪一段、碰到什么）。

## 眼在手上标定

D405 装在法兰上，标定结果是 4x4 的 `T_flange_cam`：

```text
世界点 = FK(当前关节的法兰) @ T_flange_cam @ 相机点
```

做法、按键、文件格式都和比赛 demo 一样：先把臂摆到看得见桌面的 `look` 位，运行
`python fafu_real/hand_eye_calib.py`，按 `f` 贴桌面，`wasd/qe` 平移、`zxcvbn` 旋转把点云对齐桌沿、
锅和盘子（窗口里会画出 `kitchen_scene.py` 的长方体），`p` 保存。默认每次微调自动保存。
标好后 `read_joints.py` 会多打印相机位姿。现在技能不用相机，标定是为后面接视觉准备的。

## 已知限制

- **不接视觉**：物体位置全靠 `kitchen_scene.py`。东西被碰歪了程序不知道，摆回去后重启。
- **碰撞检查只覆盖夹爪和手上的物体**，不检查机械臂连杆；容器按实心长方体处理，
  放进 / 伸进容器时会忽略这个容器。
- **够得着的范围小**：夹爪竖直朝下时 x 0.15~0.35、|y| ≤ 0.2、z ≤ 0.16 基本都能到，
  z 到 0.20 只剩 x ≤ 0.25。物体摆远了、摆高了，check 会报 `unreachable`。
- **倾倒倾不过水平**：竖直顶抓漏勺边沿时，离线算最多只能倾到约 85~90°，口没有完全朝下，
  日志会提示。要倒得干净，记漏勺 `at_deg` 时改成对准把手，或者斜着抓（`GRASP_TILT_DEG`）。
- **停止在段边界生效**：一条路径分成 `PATH_CHUNK` 个点一段下发，看板 / Agent 的“停止”
  要等当前段走完；真正的紧急情况用急停按钮或 `Ctrl+C`。
- **容器里的东西只在世界状态里移动**，倒没倒出来、面有没有沥干，没有传感器确认。
- 以上可达性数据是用不带网格的离线副本算的（只影响显示，不影响运动学）；真机上先 check、
  再低速逐步确认。
