# RealSense 目标分割与抓姿候选

本组使用真实 D405、YOLO-World、FastSAM-x 和 GraspNet。每个脚本直接运行，
配置在文件顶部，没有命令行参数。当前做到生成相机坐标系中的抓取候选；机械臂
执行接口和实测外参尚未完成，所以不会启动 WRS、驱动机械臂或报告抓取成功。

## 按顺序启动

00–05 分别在终端或 IDE 运行配置中启动并保持运行，最后运行 06：

| 文件 | 作用 | Python 解释器 |
|---|---|---|
| [00_start_router.py](00_start_router.py) | 本地 Zenoh Router | `D:/code/venv312/.venv/Scripts/python.exe` |
| [01_start_camera.py](01_start_camera.py) | D405 同帧 RGB、深度和 SDK 点云 | 同上 |
| [02_start_locator.py](02_start_locator.py) | YOLO-World 按目标名称定位 | `.local/venvs/vision/Scripts/python.exe` |
| [03_start_fastsam.py](03_start_fastsam.py) | 对定位的同一帧执行框提示分割 | 同上 |
| [04_start_graspnet.py](04_start_graspnet.py) | 对同一帧目标 XYZ 推理抓取候选 | `.local/venvs/graspnet/Scripts/python.exe` |
| [05_start_agent.py](05_start_agent.py) | 调度下面的固定感知任务 | `D:/code/venv312/.venv/Scripts/python.exe` |
| [06_propose_grasps.py](06_propose_grasps.py) | 提交一次任务并显示本次候选 | 同上 |

在 06 顶部修改 `TARGET = "banana"` 为实际物体的简短英文名称，再运行即可。
`SELECTION = "only"` 在多个匹配时拒绝；明确指定方位时才改为 `leftmost` 或 `rightmost`。
01 默认打开真实相机；多台设备时修改 `SERIAL`。这不是离线测试入口。
采集配置为 `WIDTH, HEIGHT, FPS = 848, 480, 30`，可在 01 顶部修改后重启相机节点。
该频率只控制 RGB/Depth/SDK 点云采集；02–04 的推理仍由任务触发。

与 camera 共用 `tcp/127.0.0.1:7449`、`wrs-demo` 和 `wrs` 会话。若 camera 的
00、01、05 已运行，直接复用，**不要重复启动同名节点**。camera/02 与这里的 02
都注册 `yolo`，应关闭 camera/02 后再启动本组的 YOLO-World。可用
[camera/06_read_frame.py](../camera/06_read_frame.py) 查看 RGB 和 YOLO 结果，需将其
ENDPOINT / ENV_ID / SESSION 与本组对齐；关闭预览不影响采集。
Router 配置复用 [camera/router.json5](../camera/router.json5)。

命令行也无需添加脚本参数；例如从仓库根目录启动 GraspNet：

```powershell
& .local/venvs/graspnet/Scripts/python.exe -m examples.grasping.04_start_graspnet
```

基础解释器的入口可以用既有运行器，例如：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py examples/grasping/01_start_camera.py
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py examples/grasping/06_propose_grasps.py
```

这里的 `-m`、`-X` 是解释器选项，例子本身不解析参数。IDE 选择表中的解释器后直接运行。
独立模型环境都由固定基础 Python 创建；不要用 `scripts/run.py` 覆盖它们的模型依赖。

## 结果与准备

06 提交 `locate_object → segment_region → infer_grasps`，通过 `Step.inputs` 传递
本次任务的数据编号。RGB、深度、点云和分割始终绑定同一观测。它检查 RealSense
观测和节点就绪状态；失败时显示原因，不回退到合成相机或上一轮结果。
成功时显示候选数量、观测编号/年龄、相机坐标系、首选位置和开口。
模型质量分数不是成功概率，候选尚未做真实场景碰撞检查。

本机已准备权重和两套模型环境，启动入口不会下载或安装。新机器的环境/模型准备
步骤、固定资产哈希与许可见 [VISUAL_GRASP.md](../../docs/VISUAL_GRASP.md)。
03 使用 FastSAM-x；SAM3 适配器仍在源码中，本组不包含缺少权重的 SAM3 启动入口。
GraspNet 权重和源码限非商业内部研究，不能把当前配置用于未经确认的商业用途。

虚拟数据和模型替身仅保留在 `tests/fixtures/grasping`，用于自动回归；本目录的入口
都使用上述真实组件。当前未验证本组在 D405 和全部模型同时运行时的时延/显存表现。
