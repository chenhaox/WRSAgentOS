# WRSAgentOS 仿真项目交接报告

更新日期：2026-10-25。用途：交给新对话快速接手仿真开发。内容仅覆盖软件架构、FAFU 仿真模型、厨具场景和摆放编辑器，不包含真机连接、相机训练或实物示教。

## 先读这几条

- 当前项目：`D:\WRSAgentOS\WRSAgentOS-latest`，Windows / PowerShell，使用 Conda 的 `wrs` 环境，Python 3.12。
- WRSAgentOS 负责调用技能、调度动作、同步状态；底层 WRS 负责机器人运动学、模型和三维显示。两者是上下层关系。
- **使用 `backend="wrs"` 时，默认仿真机械臂已换为 FAFU，默认环境已换为厨具场景。** 其他后端不因此自动改变。
- 当前有 17 个场景对象，运行资源已放进项目，不依赖桌面的原始 STL 文件。
- 常用组合：05 启动节点，07 显示页面，16 在终端修改厨具摆放。页面端口为 **8000**。
- **16 只改本次运行副本；重启 05 后恢复默认摆放。** 这是用户明确要求，后续开发应保留。
- 最新代码和资源有未提交改动。换到新分支或工作目录前，先处理工作区，不能只凭 GitHub 上的内容判断当前进度。

## 1. 项目大致怎样运行

可以把整个项目理解成“指挥系统 + 仿真执行器 + 展示窗口”：

```text
Python 控制脚本 / Agent
        ↓ 提交动作
System → Runtime → Zenoh 通信 → WRS 节点 → FAFU 适配层 → WRS 运动学模型
                                       ↓ 状态快照
                               07 观察器 → 浏览器 8000

16 摆放编辑器 → 本次运行的 layout.toml → 节点自动重读 → 07 同步显示
```

| 部分 | 主要职责 |
|---|---|
| `System`、同步接口 | 提供 `launch()`、`connect()`、`action()`、`snapshot()` 等入口 |
| Runtime、执行器 | 检查和调度动作，管理资源、取消及执行状态 |
| Node、Zenoh | 让独立进程发现能力、发送动作、查询状态；05 的通信端口是 7449 |
| Skill | 定义参数规则、动作说明与处理函数；执行端注册，客户端从节点发现 |
| `wrs_agent/env/` | 把项目动作转换为具体后端操作 |
| `third_party/wrs/` | WRS 源码子模块，提供机器人结构、FK/IK、几何模型和网页显示 |

FK 是“给关节角，算末端位置”；IK 是“给末端目标位置和朝向，求关节角”。当前 FAFU 适配层使用这些能力完成仿真运动。

技能相关入口是 [skills/README.md](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/skills/README.md)、[robot/definitions.py](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/skills/robot/definitions.py) 和 [greet_skill.py](D:/WRSAgentOS/WRSAgentOS-latest/examples/nodes/greet_skill.py)。当前 `Skill` 会从参数模型生成合同，不应照早期聊天重新套用旧版嵌套 `SkillSpec` 写法。

## 2. 已经完成的仿真成果

### FAFU 成为 WRS 后端的默认模型

- 项目内保存了 FAFU 的 URDF 和连杆 STL。URDF 描述“有哪些连杆、关节怎样连接、转轴和限位是什么”；STL 提供连杆外形。
- 加载器读取 URDF，解析网格路径，生成 WRS 机器人结构，并按关节名称对应 6 个活动关节。
- `VirtualModel()` 当前返回 `FafuModel()`。FAFU 适配层继承并复用部分原有模型逻辑；看到 `URVirtualModel` 类名时不要直接删除它。
- 底座位置在 `profile.toml` 中，目前是 `[-0.125, 0, 0]` 米，位于平台边缘。
- 支持 `observe`、`move_named_pose`、`move_relative`。当前 TCP 是 URDF 的 `tool_link`。

### 厨具已导入，并成为默认场景

当前 17 个对象包括：工作台、电磁炉、大锅、大锅盖、量杯、漏勺、盘子、盘上长方体、捞网架、两套“碗与支架整体＋碗架”，以及第二套碗内的 4 个淡黄色食物块。

- 大锅放在电磁炉上，位于机械臂右侧；锅盖在较远的桌角。
- 漏勺放在捞网架上，两套碗在机械臂前方，第二套靠近捞网架。
- 量杯为透明浅灰色，电磁炉为灰白色。
- 运行目录内有 10 个 STL；第二套碗复用已有网格，4 个食物块直接使用 `box`，无需复制 STL。
- 运行网格以**米**保存。新导入的毫米制 STL 必须先转换，已经转换过的文件不要重复缩放。

### 漏勺已换成“立起、中心原点”版本

- 使用用户提供的 `漏勺_立起_中心原点.stl` 替换运行资源 `skimmer.stl`，并从毫米转换为米。
- 新网格包围盒中心为 `[0, 0, 0]`，尺寸约为 **151 × 140 × 340 毫米**。
- 同步补偿了默认位置和朝向，替换前后的初始外观与摆放保持一致；默认角度现在是 `[0, 0, 0]`。
- 保留位置、只改角度时，漏勺绕自身中心转动。它仍是捞网架的子物体，移动捞网架仍会带动漏勺。
- 解决方式是**修改网格坐标原点**，没有加入编辑器专用的 `rotation_center` 字段，也没有把全部子物体改成世界坐标输入。

## 3. 接手时优先看的文件

下面的链接指向本机文件。换机器后，可按同样的目录结构定位。

| 文件 | 作用 |
|---|---|
| [env/fafu.py](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/env/fafu.py) | 从项目 URDF 和 STL 构建 FAFU 的 WRS 模型 |
| [env/fafu_backend.py](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/env/fafu_backend.py) | 6 关节映射、TCP、命名姿态和相对运动适配 |
| [assets/fafu/profile.toml](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/assets/fafu/profile.toml) | 机械臂底座位置、home/B/C；关节角单位是弧度 |
| [env/wrs.py](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/env/wrs.py) | 默认模型选择、网格加载、场景同步和重读任务接入 |
| [kitchen/scene.toml](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/assets/kitchen/scene.toml) | 物体清单、网格、尺寸、颜色、透明度 |
| [kitchen/layout.toml](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/assets/kitchen/layout.toml) | 默认位置、角度和父子关系 |
| [scene.py](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/scene.py)、[scene_layout.py](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/scene_layout.py) | 配置读取、校验、角度转矩阵、父子坐标换算 |
| [layout_session.py](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/layout_session.py) | 创建一次性副本、登记当前场景、退出时清理 |
| [layout_editor.py](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/layout_editor.py) | 终端交互、选择物体、校验输入、保存临时摆放 |
| [scene_reload.py](D:/WRSAgentOS/WRSAgentOS-latest/wrs_agent/scene_reload.py) | 节点空闲时重读配置，更新场景状态 |
| [05_start_node.py](D:/WRSAgentOS/WRSAgentOS-latest/examples/wrs/05_start_node.py)、[07_viewer.py](D:/WRSAgentOS/WRSAgentOS-latest/examples/wrs/07_viewer.py)、[10_edit_kitchen.py](D:/WRSAgentOS/WRSAgentOS-latest/examples/wrs/10_edit_kitchen.py) | 常用三个启动入口 |

更详细的使用说明见 [KITCHEN_LAYOUT.md](D:/WRSAgentOS/WRSAgentOS-latest/docs/KITCHEN_LAYOUT.md)，FAFU 接入说明见 [FAFU_KITCHEN.md](D:/WRSAgentOS/WRSAgentOS-latest/docs/FAFU_KITCHEN.md)。

## 4. 摆放配置怎样写

`scene.toml` 开头通过 `layout = "layout.toml"` 引用同目录摆放文件。两份配置以物体 ID 对应，例如 `skimmer`，不是按中文名称关联。

模型和材质示例：

```toml
[objects.skimmer]
source = "configuration"
label = "漏勺"
rgb = [0.58, 0.63, 0.68]
alpha = 1.0
geometry = { kind = "mesh", asset = "kitchen/meshes/skimmer.stl" }
```

`asset` 相对于 `wrs_agent/assets/`，不能填写用户桌面的绝对路径。`rgb` 和 `alpha` 范围为 0～1。

默认摆放示例：

```toml
[objects.induction_cooker]
pos = [0.02, -0.29, 0.0]
rpy_deg = [0.0, 0.0, 0.0]

[objects.pot]
parent = "induction_cooker"
pos = [0.0, 0.0, 0.0450001359]
rpy_deg = [0.0, 0.0, 0.0]
```

- `pos` 是模型原点的 X/Y/Z 位置，单位**米**；`rpy_deg` 是绕 X/Y/Z 的角度，单位**度**。
- X 红、Y 绿、Z 蓝。按当前默认机械臂朝向，+X 为前方，+Y 为左侧，-Y 为右侧，+Z 为上方。
- 无 `parent`：相对于世界坐标。有 `parent`：相对于父物体坐标，终端输入也遵循这一规则。
- 电磁炉未旋转时，锅的世界位置就是两个位置相加；父物体旋转时，加载器会先旋转相对位置，再相加。
- 角度换算使用 `R = Rz(yaw) × Ry(pitch) × Rx(roll)`。父物体旋转后，子物体输入的轴也随父坐标系转动。
- `parent` 是摆放跟随关系，不是物理约束。仅保持中心不动，不保证物体在任意倾角下仍能受到支撑。

已有跟随关系：电磁炉→锅；捞网架→漏勺；盘子→盘上长方体；两套碗架分别→各自的碗与支架整体；第二套碗→4 个食物块。

### 临时编辑的关键约定

05 启动时创建 `.local/kitchen-preview/run-*/scene.toml` 和 `layout.toml`。16 通过当前会话记录找到这一副本，只修改副本。正常关闭会清理它；下次启动始终重新复制默认配置。

编辑器支持编号、完整中文名称或 ID；位置和角度各输入 3 个数，回车保留，`q` 取消。提示已经改为：

```text
新位置 X(红), Y(绿), Z(蓝)（回车保留，q 取消本次修改）：
新角度 绕X(红), 绕Y(绿), 绕Z(蓝)（回车保留，q 取消本次修改）：
```

保存前会校验输入、父子结构及当前会话；写入使用临时文件替换，保留原有注释。节点每约 0.5 秒检查配置，在没有活动动作时应用。只改变位置或角度时复用网格；更新期间的执行器状态保护避免新动作插入场景刷新过程。

**要永久改变默认摆放，手动修改 assets 中的 `layout.toml`；日常试位置则使用 16。** 模型、颜色、透明度和物体清单改变后需重启 05/07，当前热更新只支持摆放。

## 5. 最短运行步骤

每个终端先执行：

```powershell
conda activate wrs
cd "D:\WRSAgentOS\WRSAgentOS-latest"
```

然后在三个终端分别运行：

```powershell
# 终端一：场景节点，保持运行
.\scripts\run.ps1 examples/wrs/05_start_node.py
```

```powershell
# 终端二：观察器，保持运行；打开 http://127.0.0.1:8000
.\scripts\run.ps1 examples/wrs/07_viewer.py
```

```powershell
# 终端三：临时编辑摆放；可随时退出和重新打开
.\scripts\run.ps1 examples/wrs/10_edit_kitchen.py
```

位置修改后刷新 8000 页面即可查看。重启 05 后也需重启 07，因为观察器绑定原节点的启动实例。只有重开 16 时，不需要重启 05/07。

只看默认场景，可单独运行 `09_imported_kitchen.py`，端口 **8007**。它是静态预览，不读取 05 的临时摆放，也不跟随节点运动。`06_control_arm.py` 是已存在的仿真动作客户端，场景布置不要求运行它。

环境沿用 Python 3.12、项目 `.local/deps` 和 WRS 子模块。跨机器需按项目依赖文档准备环境；当前 WRS 子模块实际提交为 `7815e6f110fd161fe3c3b7e6f978e5393c3cf502`，不要只照旧 README 的版本号判断本机状态。

## 6. 已验证什么，还有哪些限制

| 证据 | 已确认范围 |
|---|---|
| 位置编辑器阶段的历史验证 | 140 项相关单元测试通过；真实节点→终端编辑→07 HTTP/WebSocket→新页面连接→重启复位的集成测试通过 |
| 最近漏勺替换时的验证 | 35 项布局/编辑器测试通过；实际 WRS 模型加载 `--check` 返回 PASS，含 17 个对象和有效 FAFU 运动学状态 |
| 漏勺顶点比较 | 新中心为零；初始摆放与替换前逐顶点比较，最大误差约 `2.98×10⁻⁸` 米 |
| 最近提示文字修改 | 已核对 WRS 的 RGB 轴配色，并通过 Python 语法检查 |

这些是已有验证记录。本报告整理不代表重新跑过全套测试，也不代表最近做过网页目视验收。

接手后可按需要运行：

```powershell
.\scripts\run.ps1 examples/wrs/09_imported_kitchen.py --check
.\scripts\run.ps1 -m pytest tests/unit/test_scene_layout.py tests/unit/test_layout_editor.py tests/unit/test_scene_reload.py -q
.\scripts\run.ps1 -m pytest tests/integration/test_kitchen_layout_reload.py -q
```

当前默认场景主要完成**模型展示、摆放编辑与运动学接入**。没有接入完整的重力、接触响应、碰撞路径规划或物理抓取；FAFU 的夹爪技能也未开放。不能把 STL 有外形理解为已经能真实抓取。旧 MuJoCo 小示例的历史成果不等于当前默认场景已具备物理效果。

## 7. Git 状态与新对话接手注意事项

报告核对时：

- 分支：`feature/cooking-scene`。
- 最近提交：`d0a8e6c`，`90周年校庆展览_展示专用场景`。
- `origin`：`https://github.com/chenhaox/WRSAgentOS.git`。
- `my_update`：`https://github.com/LLLLLLF3505/WRSAgentOS.git`。
- **存在未提交文件，最新摆放编辑器等内容尚不能仅靠远程仓库获得。** 本次只编写交接文档，没有自动创建分支、提交或推送。

当前工作区的重点改动：新增 `layout.toml`、`scene_layout.py`、`layout_editor.py`、`layout_session.py`、`scene_reload.py`、16 入口及相关测试；修改场景加载、05 启动、WRS 同步、执行器刷新保护和文档；替换 `skimmer.stl`。本文也需要按需纳入提交。

开启新对话或创建新分支时，先运行 `git status` 并保留这些修改。独立克隆、从 HEAD 创建新的 worktree，通常不会自动带上当前未提交成果。`.local/`、缓存和本机口令不应提交。

两处历史说明需要特别留意：

1. 根 README 仍残留 UR7E 默认模型的旧描述，当前 `VirtualModel()` 的代码和 FAFU 专项文档更能反映本机状态。
2. 旧文档提到 `design/kitchen/` 下的 Blender 文件和截图，但当前项目中不存在 `design/`。运行不依赖它；不要把这些历史路径当作仍可用的文件，也不要声称新 STL 已同步到 Blender 源场景。

## 8. 可直接发送给新对话的开场说明

> 请先读取这份报告，再检查 `D:\WRSAgentOS\WRSAgentOS-latest` 当前工作区。我的任务是 FAFU 机械臂与厨具的 WRS 仿真环境，不涉及真机连接。目前 WRS 后端默认使用 FAFU，厨具场景有 17 个对象；05 启动节点，07 在 8000 显示，16 在终端临时调整位置与角度。重启 05 必须恢复默认，这是已确定的需求。漏勺已替换为立起且中心原点的模型。请保留未提交代码和资源，先核对 Git 状态再安排新分支；后续具体任务由我补充。

建议接手顺序：先看默认场景和布局，再读 05/16 的入口及其调用模块，最后按新任务选择是否深入机器人适配层或技能执行层。
