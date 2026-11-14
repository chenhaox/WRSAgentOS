# FAFU 实机：识别建场景 → 大模型选技能 → 规划 → 真机执行

这一组让 FAFU 六轴臂（腕上 RealSense D405 + 二指夹爪）在真实桌面上工作：

- **没有示教位、没有示教抓取、没有预先摆好的场景。** 场景里有什么、在哪，全部来自
  相机识别；抓哪里来自 GraspNet；关节角由 IK 和避障规划算出。
- 大模型只看识别出来的世界快照，选择技能（observe / pick / place_on / pour …），
  节点负责规划、下发关节、读回校验。
- 默认 **dry-run**：规划和 3D 动画照常，串口不打开，机械臂不动。确认无误后再改成实机。

```
 D405 ──► camera ──► yolo（classes.toml 词表）──► segmentation ──► graspnet
   ▲                     │ 框 + 类别                 │ 掩码           │ 抓取候选（相机系）
   │                     ▼                          ▼                ▼
 腕上   ◄── 关节 ── fafu 节点（注册为 wrs）：手眼 → 世界系 → 碰撞体 → 规划 → 下发
                        ▲
                        │ 技能调用                         看板 UI（8011 / 8002）
                      agent（大模型，.env） ◄────────────── 用户指令
```

全部节点连同一个 Router：`tcp/127.0.0.1:7451`，环境 `voice-goal`，会话 `voice`。

---

## 1. 代码在哪

| 位置 | 内容 |
|---|---|
| `wrs_agent/nodes/fafu/node.py` | `FafuNode`：读配置、手眼、开串口、建规划器，注册技能 |
| `wrs_agent/nodes/fafu/skills.py` | 技能契约和执行：observe、pick（GraspNet）、其余复用厨房技能 |
| `wrs_agent/nodes/fafu/plan.py` | `Planner` / `RealMotion`：在识别出的世界里用厨房规划器（RRT-Connect + 捷径）规划，限位取实机范围 |
| `wrs_agent/nodes/fafu/world.py` | 检测框 + 点云 → 世界系物体（位置、高度、朝向、编号、定位提示） |
| `wrs_agent/nodes/fafu/collision.py` | `classes.toml` → 碰撞体（容器有内腔，可倒入/放入） |
| `wrs_agent/nodes/fafu/arm.py` | `FafuArm`：SDK 封装（分段下发、段间可停、力控合爪、保持帧、手动拖动） |
| `wrs_agent/nodes/fafu/handeye.py` | `handeye.json` 的读写和校验 |
| `wrs_agent/nodes/fafu/calibrate.py` | 手眼标定（点云推到仿真臂和桌面上，同竞赛 `demo/hand_eye_calib.py`） |
| `wrs_agent/assets/fafu/classes.toml` | 能识别的物体类别：检测词、尺寸、壁厚、能否抓 |
| `wrs_agent/assets/fafu/real.toml` | 速度、夹爪、home、观察方式、感知阈值 |
| `wrs_agent/assets/fafu/handeye.json` | 标定结果（运行 01 后生成） |
| `fafu_arm_sdk/fafu_robot_python/` | 厂家 SDK 与 `robot.cfg`（关节限位从这里读） |
| `examples/fafu/` | 本组启动脚本 |
| `examples/grasping/01、03、04` | 相机、FastSAM、GraspNet 节点（直接复用） |
| `examples/grasping/07_live_grasps.py` | 实时查看 GraspNet 画面 |

运动学用仓库里的 `wrs_agent/assets/fafu/urdf/fafu_baseV1.urdf`（与 SDK 的
`fafu_follower.urdf` 关节原点、轴、限位一致）。世界系与 `profile.toml` 相同：
基座在 `[-0.125, 0, 0]`，Z 朝上，桌面 z = 0。

旧的 `fafu_real/` 已删除（git 历史里可恢复）；其中训练过的检测权重 `best.pt`
移到了 `.local/models/fafu/best.pt`。

---

## 2. 准备

### 2.1 Python 解释器

| 用途 | 解释器 |
|---|---|
| Router、相机、YOLO、FastSAM、fafu 节点、Agent、UI、标定、查看器 | `F:\Python312\python.exe`（已有 torch CUDA、ultralytics、pyrealsense2） |
| GraspNet | 单独的 GraspNet 环境（需要编译扩展 `pointnet2._ext`、`knn_pytorch`） |

从仓库根目录用 `-m` 启动，例如：

```powershell
cd F:\WRSAgentOS-main
F:\Python312\python.exe -X utf8 -m examples.fafu.00_start_router
```

### 2.2 模型

| 模型 | 路径 | 状态 |
|---|---|---|
| YOLO-World | `.local/models/yolo/yolov8m-worldv2.pt` | 已有 |
| CLIP（YOLO 文本） | `.local/models/fastsam/ViT-B-32.pt` | 已有 |
| FastSAM-x | `.local/models/fastsam/FastSAM-x.pt` | 已有 |
| GraspNet | `.local/models/graspnet/checkpoint-rs.tar` + `.local/src/graspnet-baseline` | **还没有**，按 `docs/VISUAL_GRASP.md` 和 `scripts/setup_graspnet.ps1` 准备 |

没有 GraspNet 时：observe、move_named_pose、place_on 等都能跑，pick 会因为拿不到抓取候选而失败。

### 2.3 硬件

- 机械臂 USB 串口；`real.toml` 的 `serial_port = "auto"` 使用 `robot.cfg` 的设置，
  多个串口时填 `"COM5"` 这类名字。
- D405 装在腕上（相对 link6 固定），USB 3。

---

## 3. 启动顺序

每个脚本开一个终端，保持运行：

| 顺序 | 脚本 | 作用 | 解释器 |
|---|---|---|---|
| 0 | `examples/fafu/00_start_router.py` | Router 7451，生成会话口令 | Python312 |
| 1 | `examples/grasping/01_start_camera.py` | D405：RGB / 深度 / 点云 | Python312 |
| 2 | `examples/fafu/02_start_locator.py` | YOLO-World，词表来自 `classes.toml` | Python312 |
| 3 | `examples/grasping/03_start_fastsam.py` | 框提示分割 | Python312 |
| 4 | `examples/grasping/04_start_graspnet.py` | 抓取候选 | GraspNet 环境 |
| 5 | `examples/fafu/03_start_node.py` | FAFU 节点（`EXECUTE = False` 为 dry-run） | Python312 |
| 6 | `examples/fafu/04_start_agent.py` | 大模型 Agent（读 `.env` 的 `LLM_*`） | Python312 |
| 7 | `examples/fafu/05_attach_ui.py` | 看板 http://127.0.0.1:8011/ ，3D 画面 8002 | Python312 |
| 可选 | `examples/grasping/07_live_grasps.py` | 实时 RGB + GraspNet 候选窗口 | Python312 |

注意：

- **不要**同时运行 `examples/grasping/02_start_locator.py` 或 `examples/camera/02_start_yolo.py`，
  它们也注册 `yolo`；本组用 `fafu/02`。
- **不要**运行 `examples/grasping/05_start_agent.py`，它是不带大模型的第二个 Agent。
- `grasping/00_start_router.py` 监听的是 7449，本组用 `fafu/00`（7451）。
- `examples/wrs/05_start_node.py`（7449 / 8010 / 8000）是仿真，和本组互不干扰，可以同时开。
- 手眼标定时不要开 `grasping/01` 和 `fafu/03`：标定脚本自己占 D405 和串口，见第 4 节。

---

## 4. 手眼标定（眼在手上）

求的是 `T_link6_cam`：相机系的点 → link6 系。之后任意关节角下：
`世界点 = FK(q) 的 link6 位姿 @ T_link6_cam @ 相机点`。

做法同竞赛 `demo/hand_eye_calib.py`：页面里画仿真臂和桌面（z=0），实时点云按当前
矩阵变到世界系叠上去，用键盘推到重合。没有 `handeye.json`（或里面是单位阵）时，
用竞赛的 `handeye_eye_in_hand.json`（法兰→相机）换算到 link6 作初值。

### 4.1 步骤

1. 关掉 `grasping/01` 和 `fafu/03`。
2. 把臂摆到能看见桌面的姿势，运行：

   ```powershell
   F:\Python312\python.exe -X utf8 -m examples.fafu.01_calibrate_handeye
   ```

3. 电机会松开，**先用手托住**。浏览器打开 http://127.0.0.1:8003/ ，点一下页面再按键：

   | 键 | 动作 |
   |---|---|
   | `f` | 贴桌面：点云主平面摆平并压到 z=0 |
   | `w` / `s`、`a` / `d`、`q` / `e` | 点云沿世界 ±X、±Y、±Z 平移 |
   | `z` / `x`、`c` / `v`、`b` / `n` | 绕世界 X、Y、Z 转（相机位置不动） |
   | `1` / `2` | 平移步长减半 / 加倍（默认 2 mm） |
   | `3` / `4` | 旋转步长减半 / 加倍（默认 1°） |
   | `p` | 保存 |
   | `r` | 回到进入时的矩阵 |
   | `8` | 打印当前关节角和相机世界坐标 |
   | `Esc` | 退出，重新锁住电机 |

4. 先 `f` 贴桌面，再用 `wasd` 对齐桌沿和臂本体。手拖到几个不同姿态，点云都还贴在
   桌面和臂上，就算标好了。每次微调都会写入 `wrs_agent/assets/fafu/handeye.json`。

### 4.2 什么时候需要重新标定

相机支架动过、拆装过相机、换过夹爪、`robot.cfg` 的零位改过。

---

## 5. 从 dry-run 到实机

1. **dry-run**（`03_start_node.py` 里 `EXECUTE = False`）：
   - 打开看板 8011，3D 画面能看到 FAFU 臂和桌面。
   - 让大模型执行“观察一下桌面”。dry-run 下关节只在内存里动，但相机是真实的，
     所以 observe 出来的物体位置来自真实画面，只是视角是按模型算的，**dry-run 下物体
     位置不可信**（真实相机没有移动到观察位）。dry-run 主要用来检查节点、技能、规划是否连通。
2. **实机**：`EXECUTE = True`，重启 `03`。
   - 第一次：`real.toml` 里 `speed = 5`（低速），手放在急停上。
   - 上电后机械臂处于收拢位（各关节约 0），节点读回当前关节作为起点。
   - 先只执行 `observe`，看 3D 画面里物体是否和真实桌面一致（位置误差应在 1–2 cm 内）。
   - 再执行一次 pick，确认抓取位置正确后再放开速度。
3. 停止：
   - 看板/Agent 的停止：在**当前路径段结束时**生效（`chunk = 60` 个点一段）。
   - 紧急情况用**硬件急停**。Ctrl+C 关闭节点时电机按 `release_on_close`
     （默认 `hold` 保持，不会下坠）。

---

## 6. 技能与大模型流程

| 技能 | 作用 |
|---|---|
| `observe` | 用手眼和 IK 算出让相机俯视 `observe.target` 的关节角，规划过去；静止后取 YOLO 结果和同一时刻之后的点云，重建整个世界（桌面 + 物体） |
| `pick(object, candidate_set_id)` | 用 GraspNet 候选抓起物体并提起 |
| `place_on(object, surface)` | 放到某物体上；`surface = "table"` 表示放回拿起的位置 |
| `seat_in(object, container)` | 放进容器 |
| `pour(source, target)` | 把容器里的东西倒进另一个容器 |
| `shake_off(object, times)` | 抖动 |
| `move_relative(dx, dy, dz)` | 末端平移（±0.1 m） |
| `set_gripper(command)` | 开/合爪 |
| `move_named_pose("home")` | 回收拢位 |

### 6.1 一次抓取 = 五步，在同一个任务里

```
observe
locate_object(target=bowl, selection=only)        # 来自快照里该物体 label 的提示
segment_region(location_id ← 上一步)
infer_grasps(segment_id ← 上一步)
pick(object=bowl_1, candidate_set_id ← 上一步)
```

- 快照里每个物体的 label 写着怎么定位它，例如
  `碗（locate_object target=bowl selection=only）`。同类两个时是 `leftmost` /
  `rightmost`（按画面左右）；同类超过两个时写“无法单独定位”，大模型应该请用户挪开。
- pick 会核对：候选属于本任务、是机械臂**最后一次停下之后**拍的、距今 < 20 s、
  分割出的点落在 `object` 这个物体的盒子里（否则 `grasp_target_mismatch`）、
  该类可抓（`graspable`）。
- 从 8 个最高分候选里依次试（每个试两种夹爪朝向），选第一个可达且无碰撞的。
  GraspNet 的 `translation` 是手指起点，指尖 = translation + 接近方向 × depth_m。
- 合爪按力矩停；力控判定没夹到东西会张开并报 `grasp_missed`。

### 6.2 例子

“把碗里的面倒进锅里，再把碗放回去”：大模型会排
`observe → locate/segment/infer → pick(bowl_1) → pour(bowl_1, pot_1) → place_on(bowl_1, table)`。

---

## 7. classes.toml：识别哪些东西

检测和 GraspNet 都不给物体的完整体积，也不知道它是不是容器；规划避障和倒入需要
这两样，所以每个类别写一次。位置**不写**，全部来自识别。

```toml
[classes.bowl]
prompt = "bowl"        # YOLO-World 的检测词（英文、简短），也是 locate_object 的 target
name = "碗"            # 给大模型看的中文名
kind = "vessel"        # vessel = 有开口的容器（可倒入/放入）；solid = 实心
size = [0.158, 0.158, 0.072]   # 外形长宽高（米）
wall = 0.006           # 容器壁厚
floor = 0.006          # 容器底厚
graspable = true       # false 时 pick 直接拒绝（例如锅、电磁炉）
rgb = [0.85, 0.85, 0.80]
```

- 现有尺寸取自 `wrs_agent/assets/kitchen/collision.toml` 的 STL 包围盒；
  **真实物体不同就用尺子量了改**。
- 加新类别：复制一段改 key、prompt、尺寸，重启 `fafu/02`（词表）和 `fafu/03`。
- `[table]` 是桌面碰撞盒的尺寸；桌面高度由点云自动估计（找不到时用 `real.toml` 的 `table.z`）。

## 8. real.toml 常改的参数

| 参数 | 说明 |
|---|---|
| `arm.speed` | 关节速度百分比，首次 5 |
| `arm.path_step_deg` / `arm.chunk` | 下发路径加密间隔、每段点数（停止在段边界生效） |
| `arm.arrive_tolerance` | 读回关节与规划终点差超过它（弧度）判为未到位，准入关闭 |
| `gripper.force` / `gripper.effort` | 合爪力矩阈值 / 上限 |
| `observe.target` / `height` | 观察点（世界 xy）和相机高度 |
| `observe.tilts_deg` / `yaws_deg` | 依次尝试的相机俯仰 / 转角 |
| `perception.min_score` | 低于它的检测不进场景 |
| `perception.track_m` | 两次 observe 之间同类物体在这个距离内沿用编号 |

---

## 9. 实时查看 GraspNet

```powershell
F:\Python312\python.exe -X utf8 -m examples.grasping.07_live_grasps
```

- 左：实时 RGB。右：**最近一次推理用的那一帧**（不是最新帧），绿色掩码是分割结果、
  橙框是 YOLO 框，候选按分数画出：绿色最优、黄色第 2–3、灰色其余；横线是夹爪张开宽度，
  两条竖线是手指沿接近方向到指尖，短尾线指向接近的来向。
- 只读，不提交任务，不影响机械臂。候选由 pick 流程、`grasping/06` 或任何 `infer_grasps` 产生。
- `Q` / `Esc` 关闭。

---

## 10. 安全

- 首次实机保持低速，手在急停上；机械臂周围不要站人。
- `R` 松开电机时机械臂会因重力下坠，**先托住**。
- 规划失败**不会**松开电机；只有 Ctrl+C / 急停会停。
- 读回关节与规划不一致时节点报 `joint_readback_off_target`，准入关闭，需要人工确认后重启节点。
- 夹爪最大开口 `open_m = 0.055`；GraspNet 节点的 `MAX_WIDTH_M = 0.05` 会过滤更宽的候选。

## 11. 已知限制

- **可达范围小**：竖直向下抓取大约在世界 x 0.05–0.30 m（z 0.08 处），越高越近
  （z 0.15 处约到 0.20 m）。物体放在这个范围内，离基座太远会 `pick_unreachable_or_colliding`。
- **pour 很难够到**：倒入需要把容器举到目标容器口上方并倾斜，离线测试里锅放在
  x 0.14–0.22 m 都报 `pour_position_unreachable`。要用倒入，目标容器放低、放近基座，
  或先用 `seat_in` 代替。
- 物体用**轴对齐/绕竖直轴转的盒子**近似；碗是圆的，碰撞体是方的，规划偏保守。
- 同类物体最多区分两个（左/右）。
- observe 时场景要静止；拿着东西时 observe 只移动相机、不重建世界（手会挡住视野）。
- 盖子、把手、按键等没有专门处理；`set_control`、`stir`、`scoop` 在实机上不提供。
- GraspNet 的分数不是成功率，候选未做场景碰撞检查（规划器会再查一次手指和物体碰撞）。

## 12. 排障

| 现象 | 原因 / 处理 |
|---|---|
| `还没有手眼标定：handeye.json` | 先做第 4 节 |
| `找不到 fafu_arm_sdk 文件` | 检查 `fafu_arm_sdk/fafu_robot_python/` 下的 SDK 和 `.pyd` |
| `observe_pose_unreachable` | `observe.target` 太远或 `height` 太高；改近/改低，或增加 `tilts_deg` |
| `perception_unavailable` | YOLO 或相机没在运行 / 6 s 内没有静止后的新结果；检查 `fafu/02`、`grasping/01` |
| `arm_moved_during_observe` | 取图期间关节读回变了；检查是否有人推动或电机未锁 |
| observe 后物体位置偏 | 重新标定；检查 `classes.toml` 尺寸；看 07 窗口里检测框是否框准 |
| 物体没进场景 | 检测分数低于 `min_score`、框内深度点太少、或 prompt 与检测标签不一致 |
| `grasp_candidates_unavailable` | GraspNet 没启动或候选已过期 |
| `grasps_taken_before_arm_stopped` | 候选是在机械臂移动前拍的；重新执行感知三步 |
| `grasp_target_mismatch` | 分割出的物体不是 `object`；检查 locate 的 target / selection |
| `grasp_missed` | 力控判定没夹到；检查抓取位置、`gripper.force` |
| `joint_readback_off_target` | 实际没到位（负载过大、卡住、限位）；人工确认后重启节点 |
| 看板没有画面 | 8011 / 8002 被占用；改 `05_attach_ui.py` 的端口 |
