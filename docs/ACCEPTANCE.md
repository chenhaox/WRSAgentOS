# WRS-Agent V1 验收合同

## 2026-10-14：YOLO 推理同帧标注及显式置信度

修正上轮“检测常滞后，只显示文字”的限制：06 左侧实时 RGB，右侧是 YOLO 推理时
原图及框/类别/分数。YoloNode 新增认证只读 request/vision/frame，按 result_id 取
原图；最多两份 RGB、采集起两秒有效，失败/换相机会话清除，不保留深度/点云。
客户端复用相机二进制解包，校验观测/会话/来源/采集时间/尺寸，加完整往返年龄；
重复读取不能刷新有效期。仅查询已有结果，不触发检测、规划或动作。

置信度过滤原本已有：YoloOptions.confidence=0.35 → YoloDetector → predict(conf)。
本次仅在 camera/02 顶部增加 CONFIDENCE 配置并传入，启动打印阈值。没有叠加第二套
显示阈值；发布、描述、定位使用同一过滤后的检测。Planner 本轮未修改，说明依据
下方真实在线验收和当前 wire/openai_chat/Runtime 代码，不新增在线调用。

修改：nodes/vision/node.py、camera/02 与 06、相关单元/集成测试与检测器 fixture；
CAMERA.md、examples/README.md、voice/README.md 和执行计划。没有增加依赖。

实际命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_opencv.py tests/unit/test_camera_example_config.py tests/unit/test_vision_results.py tests/unit/test_camera.py tests/integration/test_camera_examples.py tests/integration/test_vision_speech.py -q --junitxml=reports/yolo_overlay.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera/06_read_frame.py examples/camera/02_start_yolo.py wrs_agent/nodes/vision/node.py tests/unit/test_camera_opencv.py tests/unit/test_camera_example_config.py tests/unit/test_vision_results.py tests/integration/test_camera_examples.py tests/fixtures/vision_node.py --output-format concise
git -c safe.directory=D:/code/ch/agentOS -c core.safecrlf=false diff --check --ignore-submodules
```

55 passed / 0 failed / 0 skipped，10.53 秒；修正三个行宽问题后 Ruff 与 diff 检查通过。
覆盖 0.35/0.65 阈值传给模型、示例传入节点、两帧容量、过期/缺帧/失联、错观测/会话/
来源/尺寸/时间戳拒绝、框在推理原图而非最新 RGB、关闭与取消。真实 Zenoh 下用
250 ms 检测器夹具延迟模拟相机缓存已前进，仍读取正确图像并绘框；随后 Mock 播报
SUCCEEDED。人工检查 reports/yolo_overlay_preview.png：合成杯子在左侧移动，右侧框
与旧图对应，标签 cup 90%。该图是绘图夹具，不是模型识别实测。

证据 reports/yolo_overlay.xml；依赖沿用下节实测版本：Python 3.12.0、Zenoh 1.9.0、
Pydantic 2.13.5、NumPy 2.5.3、OpenCV 5.0.0.93、pytest 9.1.1、Ruff 0.16.8。
阻塞：无；真实 D405/YOLO 准确率、桌面交互、有声播报仍 unverified；本轮模型调用 0。
下一入口：保持 Router 和 camera/01，重启 camera/02_start_yolo.py 与 06_read_frame.py。

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py examples/camera/06_read_frame.py
```

## 2026-10-14：相机示例排序与 RGB / YOLO 联合预览

相机示例现为 00 Router、01 Camera、02 YOLO、03 TTS、04 Agent、05 播报、06 预览。
下方历史验收命令保留当时编号；当前入口见 CAMERA.md。06 从相机读取 RGB，后台
读取 YOLO snapshot 与 request/vision/result，显示类别、置信度、来源及观测编号；
同帧同尺寸才画框。重复结果不重置有效期，过期/缺节点/旧相机会话清除显示，
有效零检测与不可用分开。查询不触发额外推理、模型规划或播报。

修改文件：examples/camera 的 02 至 06 重命名及预览实现；scripts/example_catalog.py；
camera_opencv、camera_example_config 单元测试及 camera_examples 集成测试；
examples、voice、grasping 的运行说明、CAMERA.md、DEPENDENCIES.md 与执行计划。
保留原有相机后端、模型权重、TTS 后端、连接配置以及其他未提交改动。

实际验证命令（仓库根目录）：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_opencv.py tests/unit/test_camera_example_config.py tests/unit/test_camera.py tests/integration/test_camera_examples.py tests/integration/test_vision_speech.py -q --junitxml=reports/camera_reorder.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera tests/unit/test_camera_opencv.py tests/unit/test_camera_example_config.py tests/integration/test_camera_examples.py scripts/example_catalog.py --output-format concise
git -c safe.directory=D:/code/ch/agentOS -c core.safecrlf=false diff --check --ignore-submodules
```

结果：41 passed in 8.90s，失败/跳过均 0；ruff 与 diff 检查通过；check_catalog(Path('examples'))
通过。真实 Zenoh 进程下，06 读取并绘制合成 RGB 和检测器夹具结果，随后 05 的固定
播报任务 SUCCEEDED；仅 GUI 调用被替换，绘图/认证/查询是真实实现。初次集成运行
因测试客户端未使用 LocalStack 临时口令失败，补齐测试环境变量后通过，未放宽认证。
沙箱内测试停滞后终止，有限离线测试在沙箱外完成。另渲染并人工查看
reports/camera_reorder_preview.png，确认不同帧检测显示在右侧、无错帧叠框。
图片上的 FPS 是明确标记的显示夹具值，不作为帧率测量。机器结果保存于
reports/camera_reorder.xml。

依赖实测：Python 3.12.0、eclipse-zenoh 1.9.0、Pydantic 2.13.5、NumPy 2.5.3、
opencv-python 5.0.0.93、pytest 9.1.1、ruff 0.16.8；未增加依赖或修改锁文件。
阻塞：无。未验证：D405 实机、真实 YOLO 准确率、桌面窗口交互和真实音频；本轮不启动
设备、不加载检测权重、在线模型调用 0 次。下一入口：已有 voice/07 就绪后运行 camera/01、
02、06；旧 IDE 运行配置需指向重命名后的文件。06 可在没有 YOLO 时只看 RGB。

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py examples/camera/06_read_frame.py
```

## 2026-10-13：点云与 GraspNet 候选进入 WRS 主场景

行为：voice/09 的点云标签复用已有 WRS WebGPU 场景，不创建点云 canvas。“实时点云”
保持 5 Hz、最多 8192 个均匀采样点；“抓取候选”每秒最多检查两次最近有效引用，按编号
读取推理时保留的点云，同一候选集不重复下载，显示最高分的至多 8 个平行夹爪示意。
新入口 request/grasp/latest 只读小型引用，复用成功状态、boot/epoch、上游分割与 TTL
检查；新推理开始清空当前展示引用，不回退旧任务。切换/失效释放本层的 GPU 缓冲。
无外参时在明确标为未标定的相机展示区布局，不改数据坐标或机器人执行权限。

修改：nodes/camera 的 preview.py、panel.js 与新增 scene.js；nodes/grasp 的 data.py、
node.py；09 顶部 GRASP_NODE 与传入配置；pyproject 包资源、对应测试和接入文档。
复用已固定 WRS 7815e6f110fd161fe3c3b7e6f978e5393c3cf502 的 window.wrs.renderer/camera，
未修改 submodule 或增加依赖。特别处理其 remove(model) 不释放共享几何的行为：本层
固定命名前缀、替换时释放自己的几何，不清空整个场景。通用 Planner/Runtime 未改动。

实际命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera.py tests/unit/test_grasp_perception.py tests/unit/test_voice_examples.py tests/unit/test_viewer_hold_button.py tests/integration/test_camera_viewer.py tests/integration/test_grasp_pipeline.py -q --junitxml=reports/camera_scene.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent/nodes/camera/preview.py wrs_agent/nodes/grasp/data.py wrs_agent/nodes/grasp/node.py examples/voice/09_viewer.py tests/unit/test_camera.py tests/unit/test_grasp_perception.py tests/fixtures/camera_viewer.py tests/integration/test_grasp_pipeline.py reports/viewer_scene_page.py
& 'C:/Program Files/nodejs/node.exe' --check wrs_agent/nodes/camera/panel.js
& 'C:/Program Files/nodejs/node.exe' --check wrs_agent/nodes/camera/scene.js
& 'C:/Program Files/nodejs/node.exe' reports/check_camera_panel.cjs
& 'C:/Program Files/nodejs/node.exe' reports/check_camera_scene.mjs
& 'C:/Program Files/nodejs/node.exe' reports/check_camera_browser.cjs
git diff --check --ignore-submodules
```

最终 76 passed / 0 failed / 0 skipped，44.75 秒；Ruff、JS 语法与轮询检查、diff 检查通过。
回归包括完整 Zenoh SAM/FastSAM/框提示三种链路读取同一推理帧，拒绝观测身份不匹配，
未成功动作、过期与取消期间的结果发现，重复有效性检查不重新下载数组。前端检查
验证夹爪闭合轴/显示变换、连续 200 次几何替换缓冲数恒定、清除不影响机器人，以及
WRS scene_init 清空后恢复可视化。测试夹具首次缺临时口令导致 3 失败；修正后回环
HTTP 一次受系统代理干扰，固定 trust_env=False 后全部通过，未放宽断言。

真实页面检查使用独立无界面 Chrome/155.0.8059.40、本机默认图形后端、真实 WRS
WebGPU renderer。页面背景含实际 WRS UR7E 几何；相机和候选为合成测试数据，无设备
连接。验证点云/抓姿/定位、切回深度保留 13 个原场景模型并释放本层几何、结果过期
清除，页面无额外 canvas、无 JS 异常。已检查截图 reports/camera_scene_points.png、
camera_scene_grasps.png、camera_scene_focus.png，报告 camera_scene_browser.json。
首次强制软渲染没有 WebGPU adapter，不算通过；换默认后端后两次检查通过。

依赖沿用本节下方 FPS 记录：Python 3.12.0、NumPy 2.5.3、Pillow 12.3.0、Zenoh 1.9.0、
websockets 15.0.1、pytest 9.1.1、Ruff 0.16.8，没有安装或下载。网络/浏览器测试经批准
在沙箱外有限执行，临时服务和独立浏览器在检查完成后退出，未重启用户服务。

D405 与真实 GraspNet 节点联合显示仍 **unverified**；无相机外参，不能声明 world 配准。
现场入口：按 examples/voice/README.md 的同环境接入说明设置 02/03/04/06 为
7451 / voice-goal / voice，复用既有 voice/07、camera/01；重启更新后的 GraspNet/09，
运行 06 生成候选，再点“点云 → 抓取候选”。查看只读；没有在线规划或自动推理。

## 2026-10-13：严格结构输出最终 15 项在线检查全部通过

用户允许本轮最多 30 次调用、要求可用。新增显式 LLM_OUTPUT_MODE=json_schema；
PlanDecision 提供决策/计划结构，当前 SkillSpec 提供各技能参数、版本、可绑定结果。
Schema 使用 strict=true，args/inputs 展开为封闭字段，拒绝 plan.text 等多余层级。
本地 PlanDecision/Runtime 的依赖、资源、权限、版本、epoch 校验继续执行。普通
问候不创建任务；含糊目标澄清；明确不播报的视觉目标只描述。生产无自动重试或模型切换。

本轮实现文件：wrs_agent/planner/providers/{llm,openai_chat,wire}.py、wrs_agent/errors.py；
回归文件：tests/unit/{test_llm,test_step_outputs}.py、tests/integration/test_llm_runtime.py。
配置/说明：本机 .env、.env.example、examples/voice/README.md、本验收及 EXECPLAN。
其他聊天的相机/抓取等修改保持原样。诊断脚本和报告位于忽略目录 reports，未提交凭据。

账号服务 models 列表包含 qwen3.8-flash，实际请求确认支持本次结构约束；同一端点、
同一账号凭据未更改。依据 [Qwen 官方结构化输出文档](https://help.aliyun.com/en/model-studio/qwen-structured-output)
（2026-10-13 查阅），严格模式与 json_object 的语法保证不同。服务端不支持本项目
原有任意键字典形状，不能只删除 patternProperties；最终按技能合同展开而不放宽
本地检查。技能 Schema 仍来源于现有参数模型，没有另一套手写参数表。

本轮在线记录 reports/voice_strict_30.json（不含凭据、地址或原始 HTTP/模型错误正文）：

| 请求号 | 内容 | 结果 |
| --- | --- | --- |
| 1 | 只读模型列表 | 200，计入预算但不是模型生成 |
| 2–3 | 初版 json_schema | HTTP 400，任意键字典的 Schema 不兼容 |
| 4–13 | 展开技能参数后的视觉问法 | 10 个合法 describe_objects→speak 计划 |
| 14 | 普通问候 | 计划合法但误生成 speak，未符合预期；未在现场执行 |
| 15–17 | 完成提示约束后的问候、含糊目标、不播报 | ANSWER、CLARIFY、仅 describe_objects，3/3 |
| 18–26 | 最终 9 种视觉问法 | 9/9，均合法两步计划，文本绑定 describe_objects 输出 |
| 27–29 | 真实模型→隔离 Runtime→合成视觉→Mock TTS | 三个原始目标全部 SUCCEEDED |

总计 **29/30 次请求预算**，其中 **28 次模型请求尝试**（包含两次 400），最终配置
的 **15/15** 符合预期。未隐藏探索期失败，未将有限样本当作永久可靠性保证。
同样的最终 12 份真实决策另在本地 Zenoh 上离线回放，问候/澄清零动作，不播报目标
只描述；其余文本均为“合成测试画面中识别到：杯子1个。”，与同一 observation_id
关联的 describe_objects 输出一致。回放 reports/voice_strict_replay.json，真实模型
模拟执行 reports/voice_strict_live_runtime.json。没有读取/上传现场图像、提交现场
任务、触发真实相机或扬声器；相机和 YOLO 的诊断输入只有只读节点状态/技能。

实际命令（各命令次数以以上 ledger 为准，不可无授权重复运行在线命令）：

```powershell
# 只读现场技能和节点状态，无模型请求。
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/inspect_voice_planning.py --goal '看看画面里有什么。' --report-name voice_strict_context.json
# 用户授权的在线检查；--suite 会在首个不符合预期的结果后停止。
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/voice_strict_probe.py --live-model --list-models
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/voice_strict_probe.py --live-model --model qwen3.8-flash --mode json_schema --suite visual
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/voice_strict_probe.py --live-model --model qwen3.8-flash --mode json_schema --suite contracts
# 12 份真实决策，离线回放。
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py reports/replay_voice_strict.py
# 只修改四项公开配置，凭据和其他设置不变；随后 3 次真实模型/模拟节点执行。
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/set_voice_strict_config.py
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/replay_voice_strict.py --live-model
# 不联网的 07 入口导入/配置/客户端构造预检。
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/voice_strict_preflight.py
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_llm.py tests/unit/test_step_outputs.py tests/unit/test_errors_and_versions.py tests/unit/test_voice_examples.py tests/integration/test_llm_runtime.py tests/integration/test_vision_speech.py -q --junitxml=reports/voice_strict_regression.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent/planner/providers wrs_agent/errors.py examples/voice/07_start_llm_voice.py tests/unit/test_llm.py tests/unit/test_step_outputs.py tests/integration/test_llm_runtime.py
git diff --check --ignore-submodules
```

离线回归 **307 passed / 0 failed / 0 skipped**（25.03 秒），Ruff/diff 通过。
最终错误提示文字同步后，另重跑 tests/unit/test_errors_and_versions.py：19 passed，
0.97 秒，reports/voice_strict_messages.xml；这是上述套件的子集复核，不累加成新增用例。
保留已有 HTTP/任务取消和语音控制优先级回归；增加严格请求体、技能字段、可绑定参数、
无技能时只回答/澄清、非法返回通道/截断/权限字段/错误层级拒绝等检查。
依赖仍是 Python 3.12.0、Pydantic 2.13.5、httpx 0.28.1、pytest 9.1.1、Ruff 0.16.8、
Zenoh 1.9.0，无安装或锁文件修改。诊断用的 dotenv 来自既有解释器环境。

过程限制：第一次真实模型/模拟执行尝试因独立夹具缺少项目导入路径而在节点启动
阶段失败，尚未发出模型请求；固定夹具子进程使用 -S scripts/run.py 后三次成功。
配置预检在沙箱中停滞，已仅结束本轮诊断进程，在沙箱外运行通过，未改动用户服务。

**落地**：本机 .env 已配置 qwen3.8-flash/openai_chat/json_schema，reasoning_effort
留空、enable_thinking=false；其他变量经前后比较保持相同。07 入口预检通过；IDE
运行配置加载 .env 且无 LLM_* 覆盖，报告 voice_strict_preflight.json。旧 07 不热加载，
操作者需重新运行并看到 `qwen3.8-flash｜openai_chat｜output_mode=json_schema`。
相机01/YOLO03 ready 后，在09说原句。真实音频、真实检测质量和现场执行仍未验证；
不能把 Mock 的 SUCCEEDED 当作真实相机/扬声器验收。未提交或发布仓库。

## 2026-10-13：相机采集与 viewer 提速，推理频率保持独立

用户要求提高相机 FPS，但不提高 YOLO/SAM/GraspNet 频率。camera/01 与 grasping/01
显式配置 848×480/30 FPS。viewer 原先前后端各等待 250 ms，现 RGB/深度预览上限为
30 FPS、点云预览为 5 FPS。只查询当前视图所需组件；同一观测不重复编码，单 worker、
至多三个视图缓存，无近期浏览器请求时停止拉帧。浏览器单在途请求，忽略切换前的迟到
回复，按不同 observation_id 统计接收 FPS。旧画面年龄沿用源帧，不以重复查询刷新。
SDK 点云仍是每次采集的必需输出，未降为派生数据。YOLO 的 5 Hz 上限以及目标定位、
SAM/FastSAM、GraspNet 的按任务调用没有改动；本轮没有修改 Planner/Runtime。

实际执行命令（回环临时服务与合成帧，没有启动用户服务或真实设备）：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera.py tests/unit/test_camera_example_config.py tests/integration/test_camera_viewer.py -q --junitxml=reports/camera_fps.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_grasping_example.py -q --junitxml=reports/camera_fps_grasping.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent/nodes/camera/preview.py tests/unit/test_camera.py tests/unit/test_camera_example_config.py tests/fixtures/camera_viewer.py examples/camera/01_start_camera.py examples/grasping/01_start_camera.py reports/measure_camera_preview.py
& 'C:/Program Files/nodejs/node.exe' --check wrs_agent/nodes/camera/panel.js
& 'C:/Program Files/nodejs/node.exe' reports/check_camera_panel.cjs
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py reports/measure_camera_preview.py
git diff --check --ignore-submodules
```

最终结果：27 passed（9.98 秒）+ 12 passed（0.23 秒），共 39 passed / 0 failed /
0 skipped；Ruff、JS 语法检查、DOM-stub 轮询行为检查和 diff 检查通过。回归包含选定
分量、重复帧不编码/不重绘、编码耗时计入年龄、无人观看停止拉帧、按视图独立限频、
切换视图后丢弃旧回复，以及真实 HTTP/Zenoh 下的断线重连。DOM-stub 不等于真实浏览器
绘制测试。有限网络测试经批准在沙箱外执行，自动关闭本轮临时服务。

848×480/30 FPS **合成源**通过真实本机 Zenoh→编码→HTTP 的三秒采样结果：

| 视图 | 不同帧 / 请求数 | 采样秒数 | 接收 FPS |
| --- | --- | --- | --- |
| RGB | 64 / 89 | 3.015 | 21.23 |
| 深度 | 64 / 83 | 3.032 | 21.11 |
| 点云 | 14 / 15 | 3.078 | 4.55 |

测量前各视图预热两秒；详细记录 reports/camera_fps_probe.json。采集到 HTTP 的接收
FPS 不代表 D405 帧率、模型并行负载表现或浏览器绘制 FPS，不宣称满 30 FPS。
实测依赖：Python 3.12.0、NumPy 2.5.3、Pillow 12.3.0、eclipse-zenoh 1.9.0、
websockets 15.0.1、pytest 9.1.1、Ruff 0.16.8；未增加依赖或改动锁文件。

硬件验证仍 **unverified**：下一入口为操作者重启所用 01 相机节点、重启 viewer 并刷新
网页，读取面板的接收 FPS。底层 CameraOptions 缺省仍为 15，需要自定义启动的调用者
显式传 fps=30。两个运行示例与 viewer/文档已更新；离线测量仅置于 tests/reports。
本轮既未调用付费模型，也未重启既有相机/推理/音频/机器人服务。

## 2026-10-13：JSON 模式的一次在线复核未通过计划校验

用户明确允许新增 1 次在线验证。脚本读取当前七项技能和就绪节点状态（剔除 lease_id，
无图像）；相机检测节点 health=ready。本次 qwen-flash/openai_chat 请求显式
json_object、enable_thinking=false、不发送 reasoning_effort 或工具。返回 finish=stop，
614 字节协议响应，正文合法 JSON，但字段层级不符合 PlanDecision：
`extra_forbidden`，位置 `plan.text`。即顶层 text 被放进了只允许 steps 的 plan。
未保存原始正文；证据 reports/voice_json_mode_live.json，actions_submitted=false。

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/inspect_voice_planning.py --live-model --json-mode --goal '看看画面里有什么。' --report-name voice_json_mode_live.json
```

此前相同命令带 -S 的首次尝试在 import dotenv 阶段失败，未连接节点或发送模型请求。
正常解释器能读取现有 venv 的 dotenv；没有安装依赖。上面命令共发送 1 次模型请求，
本轮额度已用完，未自动重试。诊断进程的成功退出只表示报告已写出，**不代表计划通过**。

结论：JSON Object 的服务端兼容性已验证，但该句在线计划验证 **失败**。本机 .env
未切换，用户服务未重启，也未上传图像、创建 Runtime 任务或播报。新增错误形状回归
验证合法 JSON 的 plan.text 仍被拒绝、动作数为零；不挪动字段或丢弃字段后执行。
JSON Object 不保证 schema，依据 [Qwen 官方文档](https://help.aliyun.com/en/model-studio/qwen-structured-output)。
下一阶段需适配严格结构输出并确认账号支持的模型；当前不宣称修复完成或长期稳定。

离线复核命令：固定解释器 `-X utf8 -S scripts/run.py -m pytest tests/unit/test_llm.py
tests/unit/test_step_outputs.py -q --junitxml=reports/voice_json_nested_text.xml`；
207 passed / 0 failed / 0 skipped，3.12 秒。新增 3 项层级错误拒绝用例（JSON mode
解析，以及 tools/json_object 两种 Runtime 通道），其余为相关回归；Ruff 与限定改动
文件的 diff --check --ignore-submodules 通过。依赖同下节，没有重跑硬件或付费测试。

## 2026-10-13：显式 JSON 输出模式（离线验证）

用户“看看画面里有什么。”再次触发 llm_plan_json_invalid；只读确认相机、YOLO、
TTS 等七节点 ready，任务为空。此前 auto 3/3 仅是样本，不是语法保证。错误消息已
去掉“必须换支持工具的模型”的单一建议；新增 LLM_OUTPUT_MODE=json_object，仅
openai_chat 可选。请求使用 response_format、不混发 tools，正文由相同 PlanDecision
与 Runtime 校验。默认 tools 的正文依旧只作回答。禁止自动修 JSON、回退与付费重试。

JSON 模式依据 [Qwen 官方结构化输出文档](https://help.aliyun.com/en/model-studio/qwen-structured-output)
（2026-10-13 查阅）：qwen-flash 非思考模式支持 json_object，明确使用
enable_thinking=false。不推断旧 qwen-flash 支持严格 json_schema，也不把 JSON
语法保证当作正确计划保证。本机 .env 与运行服务未切换，无新增在线模型请求。

实际命令（固定解释器，HTTP 为 Mock、Zenoh 回环、视觉/音频为夹具）：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_llm.py tests/unit/test_step_outputs.py tests/unit/test_errors_and_versions.py tests/unit/test_voice_examples.py tests/integration/test_llm_runtime.py tests/integration/test_vision_speech.py -q --junitxml=reports/voice_json_mode.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent/planner/providers wrs_agent/errors.py wrs_agent/skills/vision/__init__.py examples/voice/07_start_llm_voice.py tests/llm_fixtures.py tests/unit/test_llm.py tests/unit/test_step_outputs.py tests/integration/test_llm_runtime.py
```

结果：273 passed / 0 failed / 0 skipped，22.48 秒；Ruff 通过。新测试包括显式模式
隔离、配置与 extra_body 冲突拒绝、回答/澄清、截断/拒绝/坏 JSON/非法权限/依赖环
不创建任务、JSON 候选经真实 Zenoh 到 Mock 节点，以及“看看画面里有什么。”的
HTTP 夹具→描述→Mock TTS，文本绑定同一 observation_id。这里的自然语言由夹具
固定响应，不是模型对这句话的实测。报告 reports/voice_json_mode.xml。

依赖实测：Python 3.12.0、Pydantic 2.13.5、httpx 0.28.1、pytest 9.1.1、Ruff 0.16.8、
eclipse-zenoh 1.9.0，报告 reports/voice_json_versions.json。diff --check --ignore-submodules
通过；不忽略 submodule 的初次检查受 .git/modules/third_party/wrs 读取权限限制。

运行限制：沙箱内 asyncio 单测首项后停滞，已中止；同一有限命令经审批在沙箱外
执行后通过。未启动用户相机/音频/机器人或改动现有服务。未增加依赖或修改锁文件。
在线兼容性与真实视觉播报仍 **unverified**；下一入口是在用户给出新的明确额度后，
使用 reports/inspect_voice_planning.py --live-model --json-mode 对同句只规划一次；
不会触发 Runtime 任务或上传图像。之前在线额度已全部用完。

## 2026-10-13：完整现场上下文的 auto 工具选择对照

用户回复已就绪后，只读确认七节点均 ready，总览有 planning_error，最近 Goal 错误
为 llm_plan_json_invalid，尚无任务。说明现场已加载错误分类修复，但先前最小合同
检查不足以证明完整场景成功。保留 reports/planner_runtime_state.json 作为此状态证据。

用户明确授权最多四次在线对照后，固定同一份现场七项技能与动作节点快照（排除
lease_id、无图像），使用同一 qwen-flash/openai_chat、当前提示与 Schema：

| 检查 | 工具选择 | 结果 |
| --- | --- | --- |
| 播报画面中的物体 | required 映射指定 propose_plan | 失败：参数括号不匹配，251 字符，第 236 列非法 JSON，finish=stop |
| 播报画面中的物体 | auto | 合法 execute，describe_objects → speak |
| 同句复查 | auto | 合法 execute，describe_objects → speak |
| 桌子上有哪些物体？ | auto | 合法 execute，describe_objects → speak |

三份计划都用 depends_on 与 inputs.text 引用视觉步骤的 text，没有生成物体名单。
在线检查没有向 Runtime 提交任务、读取图像或触发设备/扬声器。报告保留结构、脱敏
语法标点和已验证计划，不保留原始回复或私有端点：reports/voice_compare_named.json、
voice_compare_auto.json、voice_compare_auto_repeat.json、voice_compare_auto_question.json。
这是三次样本的观察，不是成功率保证；未把强制模式失败静默改写或作为自动重试依据。

落地：只将本机 .env 的 LLM_TOOL_CHOICE 从 required 改 auto，示例同样改为默认 auto，
README 记录型号差异和现场证据。07 新增不含凭据/地址的模型、协议、工具选择输出，
帮助核对 IDE 的实际环境。PyCharm 的 07 启用了 ENV_FILES，未发现同名显式变量覆盖。
现有 07 子进程不热加载环境变量，需用户重启；本轮没有代为启停节点。

实际命令：

```powershell
# 先只读保存完整上下文；没有 --live-model。
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/inspect_voice_planning.py --goal '播报画面中的物体' --report-name voice_full_context_readonly.json
# 以下两种设置分别执行一次；auto 另以同句和原始问题各复查一次，共四次付费请求。
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/inspect_voice_planning.py --live-model --context-file reports/voice_full_request.json --goal '播报画面中的物体' --tool-choice required --report-name voice_compare_named.json
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/inspect_voice_planning.py --live-model --context-file reports/voice_full_request.json --goal '播报画面中的物体' --tool-choice auto --report-name voice_compare_auto.json
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q reports/replay_voice_plans.py --junitxml=reports/voice_actual_plan_replay.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_voice_examples.py tests/unit/test_llm.py --junitxml=reports/voice_auto_config_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/voice/07_start_llm_voice.py examples/voice/09_viewer.py --output-format concise
git -c safe.directory=D:/code/ch/agentOS -c core.safecrlf=false diff --ignore-submodules --check
```

**PASS**：3 个真实返回计划的离线 Runtime 回放（0.86 s），203 项协议/语音入口回归
（2.12 s），0 failed/skipped；Ruff、diff 通过。回放为合成杯子与 Mock TTS，三次均
SUCCEEDED，播报数据为“合成测试画面中识别到：杯子1个。”，与视觉 action.output
一致。每份回放证据 reports/replayed_voice_compare_*.json；不能当成真实检测证据。
依赖沿用 Python 3.12.0、Pydantic 2.13.5、httpx 0.28.1、pytest 9.1.1、Ruff 0.16.8，
未安装包或更改锁文件。

**未验证**：真实相机→扬声器完整执行、更多语句和长期成功率。下一入口：重启 07，
确认启动行 tool_choice=auto，再按既有顺序接入 01/03/09。此前四次诊断授权已用尽；
本次另授权四次也已全部使用，不隐含更多在线调用权限。本轮未提交或发布代码。

## 2026-10-13：旧规划错误显示的后续核对

用户再次贴出 invalid_request 的通用英文正文与“故障（无）”。本轮检查时 7451/8001
无监听，本项目 Python/Router 未运行；只读 reports/check_planner_runtime.py 因连接
失败未读取到 Goal，不能据该截图定位这次失败的模型内容。最后的 agent.log ready
时间为本地 15:56:14，runtime 分类修复时间 15:56:35、最终提示 15:59:46；记录中的
最后一次启动早于修复。固定 Python 直接 import 的 runtime/wire 均指向当前仓库，
源码检查含 planning_error 与 llm_plan_json_invalid；不存在已发现的包路径遮蔽。

修改 09：FAILED 且总览不含 planning_error 时提示重启 07 加载新代码，不再将这类
详情缺失显示为“故障（无）”。README 明确 07 子进程不会热加载，仅刷新浏览器或
重启 09 不会更新 Agent 提示。没有重启/关闭用户节点、连接硬件或新增在线模型请求。

实际检查：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_voice_examples.py --junitxml=reports/voice_stale_viewer_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/voice/09_viewer.py --output-format concise
git -c safe.directory=D:/code/ch/agentOS -c core.safecrlf=false diff --ignore-submodules --check
```

PASS：34 项（0.16 s）、Ruff、diff；0 failed/skipped。没有为文案增加镜像测试。
依赖与上一条一致，无安装或锁文件修改。现场检查 BLOCKED 于服务未运行，模型问题
仍以此前的完整上下文未验证为边界；恢复后先运行上述只读诊断，不自动产生付费重试。

## 2026-10-13：语音视觉问答的工具选择、JSON 与故障显示

用户原句“桌子上有哪些物体？”已进入 Planner；现场 describe_objects/speak 可发现。
实际在线诊断按用户逐次许可共发出 4 次请求，均没有任务提交、图像上传或真实播报：

1. 原提示/required：返回未提供的 describe_objects 工具，llm_invalid_reply。
2. 指定 propose_plan、补充计划示例：工具名正确，参数 JSON 无效。
3. 强调 JSON：仍失败；脱敏结构表明 238 字符的参数在第 239 列 EOF，末尾对象未闭合，
   finish=stop 而非 length；没有自定义 stop 字段。没有补括号或放宽校验。
4. 分行示例、text 末置、明确内外闭合：同一 qwen-flash 返回合法 execute 计划，
   see=describe_objects → say=speak，say.inputs.text 指向 see.text，依赖正确。

第 4 次仅使用本地两项技能合同与空 world：审批等待超时没有执行命令，重试时现场
Router 已离线，因此改为显式合同检查。它不证明完整现场状态下的稳定性。此前的
Router 连接失败和一次本地合同索引错误均发生在模型请求前，不算在线调用。
证据：reports/voice_planning_before_fix.json、voice_planning_after_tool_fix.json、
voice_planning_unclosed_json.json、voice_planning_contracts.json。只保存结构/安全错误
与成功验证的计划；不保存原始回复、API 凭据或私有地址。

实现：Chat 的 required 映射为指定 propose_plan（auto 和其他协议保持原行为）；明确
技能只能放在 plan.steps，提供含结果引用的示例。Runtime 总览增加 planning_error，
09 显示规划错误；未提供工具、非法 JSON、非法计划结构各有安全消息。坏返回不执行、
不缓存、不自动付费重试。VISION 技能说明与 voice README 同步；无依赖/账号配置修改。

实际检查命令（固定 Python 3.12.0）：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_llm.py tests/unit/test_step_outputs.py tests/unit/test_errors_and_versions.py tests/unit/test_runtime.py tests/unit/test_voice_examples.py --junitxml=reports/voice_visual_planner_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_vision_speech.py tests/integration/test_llm_runtime.py --junitxml=reports/voice_visual_planner_integration.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent/runtime.py wrs_agent/errors.py wrs_agent/planner/providers/openai_chat.py wrs_agent/planner/providers/wire.py wrs_agent/skills/vision/__init__.py examples/voice/09_viewer.py tests/unit/test_llm.py tests/unit/test_step_outputs.py tests/unit/test_errors_and_versions.py tests/integration/test_llm_runtime.py --output-format concise
git -c safe.directory=D:/code/ch/agentOS -c core.safecrlf=false diff --ignore-submodules --check
```

**PASS**：243 单元（4.35 s）+ 11 回环集成（12.68 s），共 254，0 failed/skipped。
包含直接技能调用拒绝、解析/结构错误分类、安全故障通过 RPC 到达客户端、错误副本
隔离、新目标清除旧故障、真实提示中的示例可执行、视觉结果与 Mock TTS 同一输出、
相机断开/取消不产生迟到播报。最终 Ruff/diff 通过；初次 Ruff 一处长行已修正。
版本：Pydantic 2.13.5、httpx 0.28.1、pytest 9.1.1、Ruff 0.16.8、Zenoh 1.9.0。

**UNVERIFIED**：最终提示在完整现场上下文下的成功率、真实相机→YOLO→扬声器、
桌面检测准确率。没有启动真实硬件或下载权重，没有全仓 WRS/硬件验收。下一入口：
按 07 → camera/01 → camera/03 → 09 重启，原句可直接提交；TABLE_ROI=None 时描述
全画面，具体桌面区域需按实际视野配置。本轮未执行 Git 提交或推送。

## 2026-10-13：grasping 无参数 RealSense 示例整理

用户入口现在为 examples/grasping/00–06：Router、RealSense、YOLO-World、FastSAM、
GraspNet、Agent、一次候选任务。配置统一在顶部，main 无参数，无 argparse/sys.argv；
默认连接与 camera 一致（7449 / wrs-demo / wrs）。01 显式选择 realsense 并允许打开
相机；02–04 使用已有固定本地模型，05 不启用在线模型。06 拒绝非 RealSense 观测，
按同一任务串联定位/分割/推理，只读取该任务的 action/output/candidate_set，不执行运动。
虚拟模型/相机/标定移至 tests/fixtures/grasping，相关测试导入和路径已同步。移除旧
00_virtual_grasp 与缺少权重的 SAM3 示例入口；生产适配器与 Runtime/设备逻辑未改。
新增目录 README，更新 examples/README、VISUAL_GRASP 和 example_catalog。

实际检查命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_grasping_example.py tests/unit/test_fixed_camera.py tests/unit/test_locations.py -q --junitxml=reports/grasping_examples_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/integration/test_visual_grasp.py -q --junitxml=reports/grasping_examples_virtual.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py reports/check_grasping_entries.py
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check . --output-format concise
git -c core.safecrlf=false diff --ignore-submodules --check
```

**PASS**：28 单元测试（0.43 s）+ 5 既有集成测试（68.02 s），共 33 项，0 failed /
skipped。覆盖缺少任一依赖节点、相机断开/合成源拒绝、任务失败不读取旧结果、正确
Step.inputs、仅本任务 action、错误观测拒绝，以及迁移后的 WRS 碰撞/虚拟抬升/取消。
配置探针拦截 serve_node 后运行 01–05 的实际 main，并用生产 Options 校验参数；
全部七个入口无参数、共享会话、真实后端和模型本地路径、目录清单检查通过，输出
reports/grasping_entries.json。首次沙箱内探针停滞已中止，不算通过；同一有限逻辑
在沙箱外通过。Ruff 和 Git diff 检查通过。所有测试子进程已正常结束。

**UNVERIFIED / 未运行**：没有运行本组真实入口、打开 D405 或加载训练模型，没有
麦克风/扬声器、机械臂或付费调用。本轮集成测试使用明确的测试夹具，不能当作真实
相机证据。当前阶段仅整理例子，沿用基础 Python 3.12.0 / pytest 9.1.1 / Ruff 0.16.8，
SDK/权重/锁文件未改变；已有单模型离线推理证据见下面历史记录，全部模型同时常驻
和 RealSense 整链仍需现场验证。下一入口为 examples/grasping/README.md 的启动表；
真实机械臂抓取仍需实测标定与执行接口，不能把感知任务 SUCCEEDED 报为抓取成功。

## 2026-10-13：相机 01/02/03 实际切换到语音环境

上一轮只有说明，没有修改相机的实际连接常量，用户按 07 → 01 启动仍访问 7449。
本轮将 camera/01、02、03 的 ENDPOINT / ENV_ID / SESSION 实际统一为
`tcp/127.0.0.1:7451` / `voice-goal` / `voice`，保留相机/权重配置；01/03 启动时
输出路由信息。同步 Voice、Camera、总示例文档；独立 7449 模式需显式切回三项配置。
集成测试按常量名替换临时副本路由，避免默认配置变化后误连到用户现有 7451 服务。

实际命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/camera_voice_connection.py
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_example_config.py tests/unit/test_camera_opencv.py tests/integration/test_camera_examples.py -q --tb=short --junitxml=reports/camera_voice_config.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera/01_start_camera.py examples/camera/02_read_frame.py examples/camera/03_start_yolo.py tests/integration/test_camera_examples.py --output-format concise
git -c core.safecrlf=false diff --ignore-submodules --check
```

PASS：16 passed in 5.07s，0 failed/errors/skipped；Ruff/diff 通过。探针加载三份脚本
但不运行 main，核对真实常量后，只读连接现有 Router 的节点目录。实际发现 camera、
yolo、asr、tts、voice、agent 为 ready，wrs 为 held；报告保存于
`reports/camera_voice_connection.json`。未提交动作、发模型请求或启停用户服务。
集成仅在随机端口启动有限 Mock 服务/合成相机，验证独立相机任务仍可通过显式配置运行。

依赖未变：固定 Python 3.12.0，沿用项目 Zenoh 1.9.0 / Pydantic 2.13.5 /
pytest 9.1.1 / Ruff 0.16.8。测试和只读回环探针经授权在沙箱外运行。
UNVERIFIED：目录 ready 不代表设备新鲜帧、检测质量或页面渲染已验收；没有读取真实
相机图像或执行在线语音目标。当前相机/YOLO 已加入 voice-goal，用户可刷新 09 查看；
以后启动保持 voice/07 → camera/01 → 按需 camera/03 → voice/09，02 预览可选。

## 2026-10-13：语音 viewer 空握手、相机路由提示与接入说明

根因有两项：WRS World 的页面端口探测建立 TCP 后不发送 HTTP 即关闭，本项目的
相机扩展页面服务漏掉了 WRS 普通服务的日志过滤；camera 默认 7449/wrs-demo/wrs，
voice 默认 7451/voice-goal/voice，节点不在同一环境。后者不能通过改变启动顺序解决。

修改：`wrs_agent/env/wrs.py` 为带 HTTP 扩展的 viewer 单独配置 logger，只过滤
InvalidMessage → 请求行 EOF → 0 字节 EOF 这一条已验证异常链，保留其他错误。
`nodes/camera/preview.py` 和 `panel.js` 显示查询地址、site/env、node_id，回复无口令。
相机 01/03 注释、Voice README 与 CAMERA.md 补全语音接入设置及顺序；保留用户现有
相机/权重/default 会话配置、WRS 子模块和其他未提交修改。没有安装依赖或改锁文件。

实际命令（均从仓库根目录运行）：

```powershell
# 修改前运行第一条复现失败；修改后最终再次运行，通过。
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/integration/test_camera_viewer.py::test_viewer_empty_probe_keeps_real_errors_and_http_websocket_working -q --tb=short --junitxml=reports/viewer_handshake_before.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera.py tests/unit/test_camera_example_config.py tests/unit/test_voice_examples.py tests/integration/test_camera_viewer.py -q --tb=short --junitxml=reports/voice_camera_viewer.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/integration/test_camera_viewer.py::test_viewer_empty_probe_keeps_real_errors_and_http_websocket_working -q --tb=short --junitxml=reports/viewer_handshake_final.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent/env/wrs.py wrs_agent/nodes/camera/preview.py examples/camera/01_start_camera.py examples/camera/03_start_yolo.py tests/fixtures/viewer_handshake.py tests/fixtures/camera_viewer.py tests/integration/test_camera_viewer.py --output-format concise
node --check wrs_agent/nodes/camera/panel.js
git -c core.safecrlf=false diff --ignore-submodules --check
```

**PASS**：相关组 56 passed in 8.00s，0 failed/errors/skipped。握手测试日志等待改为
asyncio.Event 后复跑 1 passed in 1.77s，属于前述 56 项，不重复计数。回环探针验证
空连接不刷堆栈、非空无效/截断请求仍记录、HTTP handler 抛错仍返回 500 并记录、
服务随后可继续处理 HTTP 和正常 WebSocket。相机集成仅使用 Mock 节点与合成图像，
验证离线/接入/断开/重连、路由信息正确且 JSON 不含会话口令。初次 Ruff 发现测试中
两处轮询等待，改为事件后通过；JS 语法与 Git diff 检查通过。

**已复现失败**：修改前握手测试 1 failed，准确复现用户的 `stream ends after 0 bytes`
异常链，报告保存在 `reports/viewer_handshake_before.xml`；不是遗留失败。
测试在获准的沙箱外运行，HTTP/WebSocket/Zenoh 均使用随机回环端口和有限生命周期，
未使用用户的 7449/7451/8001，不改变现有常驻进程。依赖：Python 3.12.0、websockets
15.0.1、Zenoh 1.9.0、httpx 0.28.1、Pydantic 2.13.5、NumPy 2.5.3、Pillow 12.3.0、
pytest 9.1.1、Ruff 0.16.8；未下载模型或调用云端。

**UNVERIFIED**：真实浏览器渲染、真实相机/音频、在线语音到 YOLO 的完整执行未运行。
下一入口：01/03 同时设置 `ENDPOINT="tcp/127.0.0.1:7451"`、`ENV_ID="voice-goal"`、
`SESSION="voice"`，保持设备/模型配置，依次运行 voice/07 → camera/01 → camera/03
→ voice/09；07 已提供 Router/Agent/TTS，无需 camera/00/04/05。只看画面可省略 03。
已启动的 09 需重启并刷新页面才能加载本次修复，操作说明见 Voice README。

## 2026-10-13：GraspNet 官方训练权重与离线香蕉候选

用户明确批准从官方固定文件 ID 下载并用于非商业研究，之前的权重确认阻塞解除。
首次下载用固定 Python 的 urllib 流式写临时文件，核对响应为 checkpoint-rs.tar 和
12,468,415 字节后原子落盘；初次沙箱网络 WinError 10013，授权的网络执行成功。
模型在 `.local/models/graspnet/checkpoint-rs.tar`，同目录有 LICENSE 和
checkpoint-rs.provenance.json。首次本地 SHA256：
`60680087c61cba2b6791614fef1519071e294f6dcaf99b3f581bb95f7c51a868`。
公开官方来源没有 SHA256；该值固定首次取得的内容，**不是官方哈希核验**。
新增 `nodes/grasp/assets.json`、显式 `scripts/download_graspnet.py`，示例 02 默认
使用这些资产与 FastSAM segmentation 节点；加载无网络或任意 pickle 回退。

实际复核命令（下载脚本这次复核已有文件，不重复联网）：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/download_graspnet.py --download
& .local/venvs/graspnet/Scripts/python.exe scripts/check_graspnet.py --weights .local/models/graspnet/checkpoint-rs.tar --sha256 60680087c61cba2b6791614fef1519071e294f6dcaf99b3f581bb95f7c51a868 --input reports/graspnet_rgbd_corrected_0_input.npz --report reports/graspnet_trained.json
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_grasp_perception.py -q --junitxml=reports/graspnet_weights_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check . --output-format concise
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py examples/grasping/02_start_graspnet.py --help
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.local/uv-cache'
& .local/tools/bin/uv.exe lock --check --offline --python 'D:/code/venv312/.venv/Scripts/python.exe'
git -C .local/src/graspnet-baseline diff --exit-code HEAD --
git check-ignore .local/models/graspnet/checkpoint-rs.tar
git -c core.safecrlf=false diff --ignore-submodules --check
```

**PASS**：首次大小检查与后续 SHA256 一致性复核；9 项真实 CPU/CUDA 算子参考检查；
训练 checkpoint 以 weights_only=True 加载且模型参数严格匹配，生产适配器推理通过。
输入是官方 RGB-D 示例经 YOLO-World/FastSAM 分割的同一香蕉：13,865 个有效目标点，
采样到 20,000 点，返回 32 个候选，开口 .0426838–.0499959 m，质量分数
.118507–.141937（非概率）。另用 SciPy cKDTree 检查候选种子位置到目标点云距离全为 0；
这只核对坐标关联，不证明夹爪姿态或接触质量。原样输入 NPZ 哈希为
`6b89d627da154fa401b46549d97fe6d18fd1711d126aca1da5563611a9293bbf`。

报告 `reports/graspnet_trained.json` 保存输入来源、权重哈希、坐标约定和全部候选，
`reports/graspnet_trained_review.json` 保存坐标/范围复核；推理日志保留上游 torch.cross
弃用警告，无运行失败。13 单元测试通过（0 failed/skipped）；Ruff、示例 help、lock
检查（152 包）、官方源码干净及 Git 忽略/空白检查通过。基础 Python 3.12.0，GraspNet
环境 Torch 2.9.0+cu128、NumPy 2.5.3、SciPy 1.18.1，RTX 5060 Laptop GPU，未改 SDK。

**UNVERIFIED / 未运行**：collision_checked=false、live_observation=false、
physical_grasp=not_tested。没有启动常驻节点、相机、机器人、麦克风/扬声器或云模型；
没有重新执行未改变的虚拟抓取例子或全测试集。真实视觉抓取 Environment 仍未实现，
实测外参/工具变换、真实场景碰撞和物体夹持反馈仍缺失。下一入口为 VISUAL_GRASP.md
中的常驻 GraspNet 命令及实测标定/执行适配工作；完整目标不能标为完成。
以下“等待权重确认/未下载”段落是历史检查记录，当前状态以上述结果为准。

## 2026-10-13：在线语音 IDE 的 .env 配置修复

根因：`.env` 已填写模型、端点、密钥，但本机 PyCharm 的 `07_start_llm_voice`
运行配置 `ENV_FILES` 为空。已将 `.idea/workspace.xml` 中仅这一项改为
`$PROJECT_DIR$/.env`，补充 `examples/voice/README.md` 的设置说明。Runtime 继续
仅读环境变量，未改 `.env`、模型选择、凭据、源码或依赖；IDE 文件保持 Git 忽略。

实际命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 scripts/run.py reports/voice_llm_env_preflight.py
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_llm.py tests/unit/test_voice_examples.py -q --tb=short --junitxml=reports/voice_llm_env_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/voice/07_start_llm_voice.py tests/unit/test_voice_examples.py wrs_agent/planner/providers/llm.py
git -c core.safecrlf=false diff --ignore-submodules --check
```

PASS：有限预检复现无环境变量时的缺失模型报错，按 IDE 文件路径加载配置后，原
07 main 的真实配置/凭据预检通过并抵达被拦截的 LocalStack 入口。探针同时拦截
HTTP 发送；实际请求 0、节点启动 0，不输出配置值或密钥。报告为
`reports/voice_llm_env_preflight.json`。已有单元 **203 passed in 1.64s**，0 failed /
errors / skipped；Ruff 和 Git diff 检查通过。初次沙箱检查停滞后已中止，获准在
沙箱外重跑上述两项 Python 检查，未将中止运行算作通过。

环境：固定 Python 3.12.0；项目 httpx 0.28.1 / Pydantic 2.13.5 / pytest 9.1.1 /
Ruff 0.16.8。仅诊断脚本复用共享解释器已有 python-dotenv 1.1.1，不新增 Runtime
依赖或隐式 `.env` 加载。UNVERIFIED：PyCharm 界面是否立即重载磁盘改动、在线账号
模型可用性、完整语音服务及真实音频未运行。下一入口是重新运行 07；若 IDE 保留
旧缓存，在 `Paths to .env files` 中重新选择仓库 `.env` 并应用，再运行。

## 2026-10-13：相机播报顺序、独立 Agent 与缺失提示

原 05_start_tts 改为 04_start_tts，新增 05_start_agent，原 04_announce 改为
06_speak_objects；01/03 的真实相机/权重配置保留。05 只连接现有 Router，默认
live_model=False，不需要 WRS 或云模型；06 先等待 Agent/YOLO/TTS，缺失时提示启动
文件及会话配置，任务失败不再打印上一轮 TTS 文本。目录清单、README、CAMERA.md 和
集成用例同步。YOLO 观测/重连实现未修改；根据现有循环说明相机与预览的依赖区别。

实际命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_example_config.py tests/unit/test_vision_results.py -q --tb=short --junitxml=reports/camera_speech_order_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera scripts/example_catalog.py tests/unit/test_camera_example_config.py tests/integration/test_camera_examples.py --output-format concise
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/integration/test_camera_examples.py --collect-only -q
git -c core.safecrlf=false diff --ignore-submodules --check
```

**PASS**：16 passed in 0.33s，0 failed/errors/skipped；Ruff、Git diff 检查通过。
固定 Python 的 AST 检查遍历 camera 全部 7 个 main，均无参数且没有 argparse，
`scripts.example_catalog.check_catalog(Path('examples'))` 通过。初次 Ruff 的一处长行
已修正。依赖未修改，沿用 Python 3.12.0 / pytest 9.1.1 / Ruff 0.16.8 的项目环境。
单元使用模拟节点与连接，确认缺 Agent、YOLO 或 TTS 不会提交任务，成功时绑定同次
describe_objects 输出给 speak，失败时关闭连接且不读取旧 TTS 文本。

**UNVERIFIED / 未运行**：集成用例收集 1 项，仅收集不算通过；现改为 Router 单独启动，
真实 Agent 必须来自新增 05 脚本，且临时副本强制关闭 live_model、使用合成相机和 Mock
TTS。遵照用户此前要求，本轮未启动任何 Router/节点/相机/音频服务，新版多进程顺序
及实际有声播报未实跑。下一入口：用户保持 00、01、03 运行，启动 04、05，再运行 06；
02 窗口可关闭。04 默认 Mock 无声，需要真实播放时按 CAMERA.md 切换已准备的 Qwen。

2026-10-13 完整目标复核：重新检查模型目录、技能注册、UR 与 WRS 虚拟执行模块及标定
文件，结果见 VISUAL_GRASP.md 的完成条件表。GraspNet 训练权重仍缺失；真实视觉抓取
Environment 尚未实现，不能声称只剩实机验证。Planner 的 infer_grasps 说明已补上
segment_region，Ruff、Git diff 检查和固定 Python 的直接导入检查通过。仅说明文本
变更，没有重复运行已有 48 项测试/模型探针；未产生新的设备或模型执行证据。

## 2026-10-13：弯曲目标匹配修正与真实 RGB-D 离线输入

发现与修复：固定 GraspNet 示例的 banana 被 YOLO-World 找到（.850），但正确 FastSAM
掩码因旧像素 mask/box IoU=.315 被拒绝。人工核对候选 9 为香蕉、候选 39 混入相邻
物体；改为外接框 IoU 后分别为 .921/.542，仍保留 .5 门槛和 .05 歧义分差。
score_kind 明确改为 box_iou，没有把新分数冒充旧分数。核心相机/Runtime/UR 逻辑未改。

实际命令（Python 基础解释器仍固定为 D:/code/venv312/.venv/Scripts/python.exe）：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.local/uv-cache'
& .local/tools/bin/uv.exe lock --python 'D:/code/venv312/.venv/Scripts/python.exe'
./scripts/setup_graspnet.ps1
& .local/venvs/graspnet/Scripts/python.exe scripts/prepare_graspnet_example.py
& .local/venvs/vision/Scripts/python.exe scripts/check_box_segmentation.py --observation reports/graspnet_example.npz --target banana --target drill --report reports/graspnet_rgbd_corrected.json
& .local/venvs/vision/Scripts/python.exe scripts/check_box_segmentation.py --report reports/box_iou_bus.json
& .local/venvs/vision/Scripts/python.exe scripts/check_box_segmentation.py --image D:/code/test_ultralytics/images/f80a59131c9db080322649c050170f1b.jpg --target 'green pepper' --target tomato --target onion --report reports/box_iou_table.json
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_locations.py tests/unit/test_fastsam.py tests/unit/test_grasp_perception.py tests/unit/test_vision_results.py --junitxml=reports/box_iou_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_grasp_pipeline.py tests/integration/test_visual_grasp.py --junitxml=reports/box_iou_pipeline.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q 'tests/integration/test_visual_grasp.py::test_voice_planner_virtual_grasp_and_speech[False-True]' --showlocals --junitxml=reports/box_iou_cancel_retry.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_visual_grasp.py --junitxml=reports/box_iou_visual_final.xml
```

PASS：40 单元 + 3 数据链 + 5 最终虚拟抓取 = **48 个不同测试**。初次合并集成运行
为 7 passed / 1 failed，失败发生在规划前 provider_not_found，并未执行动作；原测试
单独重跑通过。该测试之前先启动 Mock WRS 再热替换，现改为启动时配置 wrs-grasp，
去掉与语音抓取场景无关的替换步骤；随后整个 visual_grasp 文件 5 passed。
不是以重试次数充当新增通过数，初始失败报告保留。没有改变 Runtime 的拒绝规则。

真实模型：Ultralytics 8.4.175 / Torch 2.9.0 CU128，YOLO-World、CLIP、FastSAM 权重
仍为此前已校验本地文件。香蕉 13,865 个分割像素都有有效深度，.421–.488 m，生产
sample_target 函数产生 20,000 个点。NPZ 保留原 RGB、depth、points 数组逐项完全一致，
mask 与保存 PNG 完全一致。与固定上游 data_utils.create_point_cloud_from_depth_image
对照，float32 XYZ 最大误差 1.1546e-7 m；按 float32 精度预算检查通过。初次人为设定
1e-7 m 绝对阈值略小于该舍入误差而失败，未将其算作通过。数值一致性不是设备精度证据。
证据为 reports/graspnet_rgbd_review.json、graspnet_rgbd_corrected.json 及输入 NPZ。

旧图片回归：bus / green pepper / tomato 掩码与旧报告逐像素相同，新框 IoU 为
.941/.946/.957；elephant 不存在拒绝、person 多目标拒绝。onion 和示例 drill 漏检
仍是质量限制，未记为成功。官方示例与两张旧图片均没有物理抓取结果。

依赖：graspnet 隔离环境新增 SciPy 1.18.1、Pillow 12.3.0；uv 实际生成锁仍 152 包，
pip check 23 包通过。全仓 Ruff、Git diff 检查通过。一次依赖操作在自动审批阶段
超时，命令未启动；按返回指示重试后成功，无待处理的审批拒绝。所有模型推理离线，
没有新权重下载、设备连接、录音或云调用。完整抓取目标仍未完成，待权重下载确认、
训练模型抓姿验证、实测标定与实机动作验收。

有限例子也已实际运行：
`D:/code/venv312/.venv/Scripts/python.exe -X utf8 -S scripts/run.py examples/grasping/00_virtual_grasp.py`
输出 `虚拟抓取结果： SUCCEEDED` 和 `虚拟场景中已夹持并抬起测试方块。`，使用明确模型
夹具、真实 WRS 虚拟碰撞/抬升运算与 Mock TTS；不是相机实物或扬声器验证。

## 2026-10-13：GraspNet Windows CUDA 扩展与无权重网络验证

固定源码 `280c215129f759ed8649cb4e89fc5dfee55f4f80`。实际命令：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.local/uv-cache'
& .local/tools/bin/uv.exe lock --python 'D:/code/venv312/.venv/Scripts/python.exe'
./scripts/setup_graspnet.ps1 -BuildExtensions
./scripts/setup_graspnet.ps1
& .local/venvs/graspnet/Scripts/python.exe scripts/check_graspnet.py
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_grasp_perception.py tests/unit/test_locations.py --junitxml=reports/graspnet_environment_unit.xml
& .local/tools/bin/uv.exe pip check --python .local/venvs/graspnet/Scripts/python.exe
& .local/tools/bin/uv.exe lock --check --offline --python 'D:/code/venv312/.venv/Scripts/python.exe'
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts
git -c core.safecrlf=false diff --ignore-submodules --check
git -C .local/src/graspnet-baseline diff --exit-code HEAD --
```

PASS：Python 3.12.0、Torch 2.9.0+cu128、NVIDIA CUDA nvcc 12.8.93、MSVC 14.44，
RTX 5060 Laptop。Ninja 1.13.2 / setuptools 84.0.0 / wheel 0.48.0。
两个真实扩展编译和安装成功；再次执行完整安装、再默认刷新 SDK 均通过，扩展保留。
独立环境 pip check 为 21 包，真实 lock 为 152 包。没有改动共享或语音/vision 环境。

9 项真实算子数值检查：FPS、gather、3NN、插值、球邻域、group、旋转圆柱邻域、
CPU KNN int64、CUDA KNN int64，包含两批数据和非默认 CUDA 流；全部与小型参考计算
一致。随机初始化的完整网络在 20,000 点输入上完成前向与 pred_decode，输出有限且
维度正确，最新单次约 .345 s；这不是训练模型或实时性能基准。训练权重推理及物理
抓取均明确 not_tested。源码保留 torch.cross 默认 dim 弃用提示，当前锁定版本通过。

现有抓姿/定位回归 **23 passed，0 failed/errors/skipped**；全仓 Ruff、PS 语法解析、
Git diff 检查通过；官方源码仍干净。没有重跑全仓集成测试或使用夹具结果冒充 SDK 检查。
报告：reports/graspnet_sdk.json、graspnet_check.log、graspnet_setup_verified.log、
graspnet_sdk_refresh.log、graspnet_environment_unit.xml。扩展编译输入哈希保存在
.local/build/graspnet-extensions/build_sources.json，官方组件清单在
.local/downloads/cuda-12.8.1/manifest.json，许可随本地源码和构建副本保存。

首次编译准备在 VS 环境命令引号处失败，修正后完整入口通过；首次沙箱回归出现失败
并停在异步测试，已中断该次进程，在具备临时目录/本地通信权限的环境重跑取得上述
23 passed。初次 pip check 也因沙箱缓存权限失败，指定项目缓存后复核通过。失败未
算通过，初始安装日志保存在 reports/graspnet_setup.log。所有构建/探针进程已结束。

未完成：官方 checkpoint-rs.tar 下载（公开大小 12,468,415，未发布 SHA256；用户的
非商业研究用途/首次来源信任确认尚未到）、训练模型真实点云推理、D405 标定及实机
夹持/停止验收。完整目标继续进行中。没有自动连接相机、机器人或音频设备。

## 2026-10-13：YOLO26n 本地权重和直接启动例子

用户授权下载；从 `https://api.github.com/repos/ultralytics/assets/releases/tags/v8.4.0`
读取 yolo26n.pt 的官方资产信息（asset_id 340096055），以 Python urllib 下载
`https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt` 到同目录临时文件，
核对 5,544,453 字节及官方 SHA256 后原子放入 `.local/models/yolo/yolo26n.pt`。
哈希：`9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef`。
来源/许可记录 `.local/models/yolo/yolo26n.source.json`；文件被 Git 忽略。
03 默认路径、GitHub 链接与 CAMERA/DEPENDENCIES 文档已更新，继续无命令行参数。

实际验证命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 .local/check_yolo26.py
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_example_config.py tests/unit/test_vision_results.py -q --tb=short --junitxml=reports/yolo26_example_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera/03_start_yolo.py --output-format concise
git check-ignore .local/models/yolo/yolo26n.pt .local/models/yolo/yolo26n.source.json
```

**PASS**：下载 SHA256/字节数匹配；实际读取 03 的路径和 device 配置，通过现有
YoloDetector / Ultralytics 在 CPU 上加载并推理本地安装包的 bus.jpg，80 类模型得到
1 个 bus、4 个 person，结果保存在 `reports/yolo26_local_inference.json`。
模型加载和推理期间阻止 socket connect/connect_ex；没有额外下载、云调用或硬件读取。
11 单元 passed in 0.30s，0 failed/errors/skipped；Ruff 与 Git 忽略检查通过。
真实推理环境：固定 Python 3.12.0、Ultralytics 8.4.24、Torch 2.9.0+cu128；依赖未修改。
初次沙箱联网被拒，批准后完成下载；沙箱内 resolve 权重路径被拒，批准后离线推理通过。
Ultralytics 提示项目 `.local/ultralytics` 设置格式重置，加载/推理正常且 sync 被关闭。
**UNVERIFIED**：未启动常驻节点或相机，未测试实时吞吐和 D405 画面的检测效果；本轮
单图推理只验证模型与适配器兼容。用户保持 00/01 运行后直接运行 03 即可。

## 2026-10-13：RealSense RGB 黑洞与彩色网格对齐修复

修改 RealSense 驱动、CameraFrame/Observation、节点传递与 CAMERA.md；02 仍为简单的
RGB→BGR/FPS 预览，不增加配置项。彩色图保持原样，深度对齐到彩色网格，XYZ/内参/坐标
名称同步。新增实际 pyrealsense2 软件帧测试：不枚举或连接设备，注入有完整 RGB、
大量零深度的不同分辨率帧，复现旧方向黑洞，验证修复保留颜色且不会伪造无效三维点。
同时检查节点事件和二进制 Query 往返保留 color_grid/坐标标识及全部数组。

实际命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_alignment.py tests/unit/test_camera_driver.py tests/unit/test_camera.py tests/unit/test_camera_opencv.py tests/unit/test_camera_example_config.py tests/unit/test_vision_results.py tests/unit/test_fixed_camera.py -q --tb=short --junitxml=reports/camera_color_alignment.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent/drivers/cameras wrs_agent/nodes/camera examples/camera tests/unit/test_camera_alignment.py tests/unit/test_camera_driver.py tests/unit/test_camera.py tests/unit/test_camera_opencv.py tests/unit/test_camera_example_config.py tests/unit/test_vision_results.py tests/unit/test_fixed_camera.py --output-format concise
git -c core.safecrlf=false diff --ignore-submodules --check
```

**PASS**：37 passed in 1.47s，0 failed/errors/skipped，报告为上述 JUnit 文件；
Ruff（All checks passed!）与 diff 空白检查通过。
最初沙箱试跑 SDK 同步测试通过，但异步测试未完成，已中止；改在批准的环境运行同一批
离线检查后完成。初次 Ruff 报 zip 的 strict 参数与行宽问题，已修正。
依赖未变：Python 3.12.0、pyrealsense2 2.56.5.9235；其余沿用上一轮项目依赖。
**UNVERIFIED**：未启动 Router/真实相机/GUI；用户需重启 01、02 核对实机画面。
机器人外参需要匹配新的 color-optical frame，不能复用 depth-optical 的坐标声明。

> 本文件按日期保留历史验收命令。2026-09-20 示例已全部重写，旧路径和旧参数仅供历史审计；当前运行入口见 [示例目录](../examples/README.md)，本次结果见文末“全部示例重写”。

## 2026-10-13：OpenCV 预览、FPS 与相机示例配置简化

修改 01/02/03、camera extra/dev 依赖、uv.lock、示例目录、单元/集成用例与说明。
01 删除顶部 ALLOW_HARDWARE，选择 realsense 即显式准入；节点边界保持默认拒绝。
03 删除手填 WEIGHTS_SHA256，自动记录用户选择的本地文件指纹并交节点复核，不宣称
已核验模型来源，也不下载权重。02 改为 RGB OpenCV 预览：最新帧事件触发读取，统计
不同观测的接收 FPS，重复不计数，超时或一秒过期清除旧画面，Q/Esc/关窗退出并清理。

实际命令：

```powershell
& '.local/tools/bin/uv.exe' --cache-dir .local/uv-cache lock --offline --python 'D:/code/venv312/.venv/Scripts/python.exe'
& '.local/tools/bin/uv.exe' --cache-dir .local/uv-cache export --frozen --extra camera --extra realsense --extra llm --no-emit-project --output-file .local/camera-requirements.txt
& '.local/tools/bin/uv.exe' --cache-dir .local/uv-cache pip install --python 'D:/code/venv312/.venv/Scripts/python.exe' --target .local/deps --require-hashes -r .local/camera-requirements.txt
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_opencv.py tests/unit/test_camera_example_config.py tests/unit/test_camera.py tests/unit/test_camera_driver.py tests/unit/test_vision_results.py -q --tb=short --junitxml=reports/camera_opencv_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera tests/unit/test_camera_example_config.py tests/unit/test_camera_opencv.py tests/integration/test_camera_examples.py scripts/example_catalog.py --output-format concise
& '.local/tools/bin/uv.exe' --cache-dir .local/uv-cache lock --check --offline --python 'D:/code/venv312/.venv/Scripts/python.exe'
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/integration/test_camera_examples.py --collect-only -q
```

**PASS**：30 单元 passed，0 failed/errors/skipped；Ruff、锁检查（150 包）、目录分类通过。
真实 OpenCV 像素转换/绘字配合窗口和网络替身验证颜色顺序、重复帧不虚增 FPS、断流清图、
关闭窗口与取消清理。例子的硬件准入映射、本地模型缺失拒绝和自动指纹均有离线证据。
集成只收集 1 项不算通过；02 已移为交互例子，默认验证不打开窗口。

锁定 opencv-python 5.0.0.93，实际模块 5.0.0 / WIN32UI；通过带哈希的导出文件向
.local/deps 增量安装 1 包。共享 Python 的 OpenCV 4.10.0 未动；其他版本沿用本节之前
项目环境。首次离线安装缓存不足、沙箱内联网 socket 被拒，获准沙箱外安装后成功。
**UNVERIFIED**：未启动 Router、相机、模型节点或窗口；实际 D405 帧率和真实 GUI 未验证。
修改已完成；用户自行运行 00/01/02。03 仍需本地 YOLO 检测权重，现有 FastSAM/CLIP
不能代替；本轮未下载模型，不能把配置简化当作 YOLO 推理已通过。

## 2026-10-13：RealSense 空序列号选默认设备

用户要求 SERIAL 留空使用默认相机，已自行设置 ALLOW_HARDWARE=True；保留该配置。
修改 01 的配置提示、CameraOptions、RealSense driver 和采集线程：空序列号先由 SDK
resolve 选可满足配置的设备，取得真实序列号，在采集线程中按该编号取得设备锁后开流。
同一 driver 在重连期间保留设备编号；指定设备消失不改选其他相机；帧坐标系包含实际编号。
本机 pyrealsense2 `config.resolve` / `enable_device` docstring 已核对，无实际设备调用。
同步相机文档、README 和单元/集成用例；集成副本固定 synthetic/mock，避免继承用户配置。

实际命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera.py tests/unit/test_camera_driver.py tests/unit/test_camera_example_config.py -q --tb=short --junitxml=reports/camera_default_device.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera wrs_agent/drivers/cameras wrs_agent/nodes/camera tests/unit/test_camera.py tests/unit/test_camera_driver.py tests/unit/test_camera_example_config.py tests/integration/test_camera_examples.py --output-format concise
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/integration/test_camera_examples.py --collect-only -q
```

**PASS**：20 单元 passed，0 failed/errors/skipped；Ruff（初次两处超长行已修复）。
覆盖默认选择前不开流、固定序列号重连、指定设备不回退、开流失败清理、按真实设备加锁、
锁冲突不开流、硬件 opt-in 保留和示例允许空序列号。集成收集 1 项不算通过。
依赖未变，Python 3.12.0 / pyrealsense2 2.56.5.9235，其余沿用下述项目依赖。
**UNVERIFIED**：本轮未启动任何常驻服务或真实相机，实际 SDK 采集/拔插与多进程未运行。
代码修改完成，无剩余编辑阻塞；用户可以保留 SERIAL="" 自行按 00 → 01 → 02 运行。

## 2026-10-13：camera 全部例子取消命令行参数

后续配置提示修复：01 已被用户改为 realsense，但序列号为空且硬件开关仍为 False。
示例新增连接前的中文提示，保留该配置与 CameraOptions 的校验；未启动任何服务。
实际运行以下命令，**6 passed，0 failed/errors/skipped**；相关两文件 Ruff 检查通过。
这是离线配置校验，不代表 D405 已连接；Python/依赖版本沿用下述环境，未改依赖。

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_example_config.py tests/unit/test_camera.py::test_hardware_requires_both_explicit_serial_and_opt_in -q --tb=short --junitxml=reports/camera_config_guard.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera/01_start_camera.py tests/unit/test_camera_example_config.py --output-format concise
```

01–05 的地址、设备/后端、序列号、模型路径/哈希及目标文本改为文件顶部常量，
`main()` 均无参数；00 已符合要求。删除 argparse 与旧 CLI 帮助测试，集成用例改为
修改临时脚本副本的地址后直接运行；未给教学代码增加环境变量覆盖或配置框架。
同步修改 CAMERA.md 与 examples/README.md。真实设备选择仍需在代码中显式确认；
未提供 YOLO 权重或哈希会在连接前提示修改，不下载或绕过现有模型校验。

实际命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_example_config.py tests/unit/test_camera.py tests/unit/test_camera_driver.py tests/unit/test_vision_results.py -q --tb=short --junitxml=reports/camera_no_cli_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera tests/unit/test_camera_example_config.py tests/integration/test_camera_examples.py --output-format concise
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera_example_config.py tests/integration/test_camera_examples.py --collect-only -q
git -c core.safecrlf=false diff --ignore-submodules --check -- examples/README.md docs/ACCEPTANCE.md docs/EXECPLAN_V1.md
```

**PASS**：18 单元 passed，0 failed/errors/skipped；两项配置拒绝用例确认无网络/模型加载。
Ruff、六份 Python AST/无 CLI/无参数 main 检查、示例目录检查与 diff 检查通过。
单元和集成共 3 项收集成功（仅收集不算通过）；新版集成用例未实跑。
初次 Ruff 导入空行格式失败已修正；版本查询首次未加入项目依赖路径，修正后成功。
依赖未修改：Python 3.12.0、eclipse-zenoh 1.9.0、Pydantic 2.13.5、pytest 9.1.1、Ruff 0.16.8。

**UNVERIFIED / 未运行**：按用户要求，本轮没有启动 Router、相机、WRS、音频或模型服务，
没有进行新版示例的真实多进程运行、D405/YOLO/音频验证。无剩余编辑阻塞；下一入口
是用户修改各文件顶部常量，按 CAMERA.md 的顺序直接运行文件，无需追加启动参数。

## 2026-10-13：相机独立 Router 启动例子

新增 `examples/camera/00_start_router.py` / `router.json5`：无 argparse 或必填命令行参数，
固定本机 7449、准备共享口令，Ctrl+C 清理自己的 Router。更新 `_session.py` 的入口提示、
`example_catalog.py`、CAMERA.md、examples/README.md 与执行记录；未修改既有相机接口。

实际命令（固定解释器，第一条在用户澄清“不代为启动”之前完成）：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_camera.py tests/unit/test_camera_driver.py tests/integration/test_camera_transport.py tests/integration/test_camera_examples.py -q --tb=short --junitxml=reports/camera_startup_regression.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_example_tokens.py -q --tb=short --junitxml=reports/camera_router_tokens.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check examples/camera/00_start_router.py examples/_session.py scripts/example_catalog.py --output-format concise
git -c core.safecrlf=false diff --ignore-submodules --check -- examples/_session.py examples/README.md scripts/example_catalog.py docs/CAMERA.md docs/EXECPLAN_V1.md docs/ACCEPTANCE.md
```

**PASS**：原有相机软件回归 15 passed；共享口令 17 passed；Ruff、AST 语法、JSON 配置、
`check_catalog(Path('examples'))` 和 diff 检查通过。最终两组均 0 failed/errors/skipped。
最初沙箱 socket/硬链接权限分别导致中止和 3 failed/14 passed，获准在沙箱外重跑后通过。
只读进程核对确认临时服务已退出；用户澄清后未启动 Router、相机或 WRS 服务。

依赖未变：Python 3.12.0、eclipse-zenoh 1.9.0、Pydantic 2.13.5、NumPy 2.5.3、
pyrealsense2 2.56.5.9235、pytest 9.1.1、Ruff 0.16.8。直接运行共享解释器另会加载
Zenoh 1.10.1 / Pydantic 2.12.3，不能将该差异视为本次连接失败的已证实原因。
**UNVERIFIED**：遵照用户要求，新增常驻 00 未实际启动；D405 采集未运行。
无剩余编辑阻塞；下一入口由用户在三个终端按 00 → 01 → 02 启动。

完整 V1 矩阵仍按阶段验收；最新结果见文末及 reports/cancel_summary.json；旧阶段报告为历史证据，未运行项不视为通过。报告必须区分 PASS、FAIL、SKIP、BLOCKED 与 UNVERIFIED。SKIP/BLOCKED 不算通过。

## 1. 环境层级

A：离线单元测试，无网络、模型凭据、麦克风和 WRS。

B：真实 Zenoh 本机多进程集成，使用 Mock Environment。

C：真实 WRS 虚拟环境，使用实际 robot/model 与规划接口，但不宣称真实接触成功。

D：显式开启的真实 GLM 与音频测试，不连接真实动作设备。

E：人工监督的实际机器人测试，必须另行 opt-in；不作为无人值守 Codex 默认动作。

软件发布报告必须列出各层级状态。不得以 A/B 通过宣称 C/D/E 通过。

## 2. 最小自动化矩阵

| ID | 层级 | 情景 | 通过标准 |
|---|---|---|---|
| T01 | A | 未知消息版本/字段、非有限数值 | 拒绝，不产生动作 |
| T02 | A | 不存在技能、参数类型或范围错误 | 明确错误，不动态导入或执行代码 |
| T03 | A | 计划依赖环、重复节点、缺失依赖 | 拒绝，无部分动作提前启动 |
| T04 | B | 两个进程 publish/query | 使用真实 Zenoh 通信得到匹配结果 |
| T05 | B | query 超时 | 可取消等待，主控制循环仍工作 |
| T06 | B | 长动作运行时查询状态 | 返回 RUNNING，不阻塞至动作结束 |
| T07 | B | 长动作中 hold | 先撤权，再确认停止；两种反馈可区分 |
| T08 | B | 重复 interrupt_id | 同一停止请求只处理一次控制版本切换 |
| T09 | B | 重复 action_id、参数相同 | 不重复执行，返回同一动作状态 |
| T10 | B | action_id 相同、参数不同 | 明确拒绝 |
| T11 | B | hold 后迟到动作/轨迹 | 旧 boot/epoch 动作拒收 |
| T12 | A/B | 修改目标后旧模型结果返回 | 旧 revision 结果丢弃，不重新开启运动 |
| T13 | B | 设备 hold 后没有 allow_actions | 新动作不能自行越过设备关闭准入状态 |
| T14 | B | allow_actions | 仅允许新动作，不续跑旧轨迹 |
| T15 | B | 同机械臂两个技能 | 资源互斥，不并发下发冲突动作 |
| T16 | B | 停止时运动资源被占用 | 控制通道不等普通动作锁释放 |
| T17 | B | 收到动作但回执丢失 | 使用 status 恢复，不创建新 ID 盲重试 |
| T18 | B | 设备执行状态不明 | UNKNOWN，后继依赖阻塞，禁止重试 |
| T19 | B | Environment 重启 | 新 boot_id，旧动作不恢复 |
| T20 | B | 两个相同 env_id 服务启动 | 独占检查失败，不允许多写同设备 |
| T21 | A/B | 执行 COMPLETE 但物体未持有 | verification FAIL，任务不算成功 |
| T22 | A/B | 验证 INCONCLUSIVE | 不解锁依赖持物成功的下一步 |
| T23 | A | Provider 输出截断/拒绝/非法 JSON | 不提交任何物理动作 |
| T24 | A | 流式参数只到一半 | 不执行；完整后仍做 Schema 校验 |
| T25 | A/B | LLMClient 切换三种线协议 | 同一 ModelPlanner/Runtime/Skill/Environment，均通过真实客户端解析 |
| T26 | A | Provider 不支持指定参数 | 启动/请求构建时明确失败，不静默伪装支持 |
| T27 | A/B | GLM 请求永不返回 | 本地停止路径仍然工作 |
| T28 | A/B | 相同适用任务再次执行 | 模型调用减少，动作 ID 与授权重新生成 |
| T29 | A/B | 否定词/数量/目标/时序改变 | 错误缓存不复用 |
| T30 | A/B | 技能、能力或标定版本改变 | 相关缓存失效并记录原因 |
| T31 | A/B | 物体位姿改变 | 重新绑定与规划，不播放旧轨迹 |
| T32 | A/B | 无关物体变化 | 不无条件失效；路径变化仍需几何检查 |
| T33 | A | semantic shadow 命中 | 仅记录，不直接触发动作 |
| T34 | A/B | 已知可恢复抓取失败 | 重新观察后最多一次重试，再验证 |
| T35 | A/B | UNKNOWN 或停止未确认 | 不进入恢复重试 |
| T36 | A/B | “嗯，对” | 不取消当前机器人动作 |
| T37 | A/B | “做到哪一步了” | 并行回答，不改变控制版本 |
| T38 | A/B | “做完再拿另一个” | 入队，不抢占当前动作 |
| T39 | A/B | “停，换成蓝色那个” | 受控停止并改任务，不沿用旧计划 |
| T40 | A/B | 只有 VAD/noise | 不直接拥有运动取消/修改权限 |
| T41 | A/B | “不要放进去”/引用“停”字 | 不误生成被否定的物理动作；谨慎暂停与新动作授权分开 |
| T42 | B | 相机流/日志/计算负载 | 内存有界，停止路径仍可处理 |
| T43 | B | 断线、恢复连接 | 不自动重发旧物理动作 |
| T44 | B | 事件乱序或丢失 | 通过版本和 status 判断，不倒退状态 |
| T45 | B | 未授权来源伪造 priority/operator | 不获得动作执行权 |
| T46 | B | 日志回放 | 只读/Mock，不发送到实机 namespace |
| T47 | C | WRS import、实际 robot/model、虚拟运动 | 是真实 WRS 接口，不是 mock 冒名 |
| T48 | C/E | stop/flush 能力缺失 | 硬件交互模式拒绝启用 |
| T49 | D | 模型真实调用 | 真实端点/模型有记录；生成计划但默认不动硬件 |
| T50 | D | 麦克风输入、模型挂起时说停 | 本地识别并发控制请求；不是文本回放 |
| T51 | D | TTS 与用户插话 | 播报和机器人中断行为可分开，记录回声限制 |
| T52 | A–D | 没有 API key、缺语音模型 | 明确缺少条件，不 fallback 后谎称接通 |

## 3. 三个最终场景

场景一：执行 A→B，期间查询当前进度，之后追加 D。进度查询不中断运动，D 排队。不得为只读查询变更环境控制 epoch。

场景二：执行 A→B，期间说“停，改放到 C”。记录旧 task revision、旧控制 epoch、停止接收、实际停止、更新世界状态、新计划和最终验证。持物状态必须保留，不能因为 cancel 就打开夹爪。

场景三：重复一个适用任务以验证缓存减少模型调用，然后改变目标位置或技能版本，观察缓存重新绑定/拒绝复用。在同一段演示里证明快路径和失效路径都存在。

上述场景在 B 和 C 分别运行。在 C 中若使用虚拟抓持规则，应明确标注。E 层涉及实际执行时必须人工监督，报告实际停止反馈与验证传感器。

## 4. 性能测量

首轮本机工程目标：可信控制事件发送到 Environment 接收并撤权确认的 RTT，P95≤20ms，P99≤50ms。这是未测目标，不是保证。达不到时报告真实结果和原因，不更换计时边界来美化结果。

固定记录机器、系统、CPU、内存、Python、Zenoh 包、zenohd、WRS commit、配置和采样数量。至少测试空载、持续观测流、模型等待和 CPU 工作负载。测试期间不发送真实硬件控制。

建议基线载荷：小控制请求 256B–2KiB、100Hz 状态消息、15fps 图像背景流，记录实际编码与图像体积。控制延迟采样不少于 1000 次；稳态前先预热，冷启动单列。阈值需注明负载定义，不能从无负载结果推断高负载性能。

在线图像消费者使用最新数据和有界缓冲；报告数据年龄、丢帧和队列高水位。可靠控制请求遇到拥塞应明确处理，而不是静默丢弃。

跨机没有时钟同步时只测请求方 monotonic RTT；不相减两台机器的 monotonic_ns。需要报告单程延迟时，另行说明时钟同步、偏差和精度。

模型 latency、ASR latency、软件控制 latency 和真实设备停止 latency 分开。缓存报告命中率、拒绝原因、模型调用节省与错误复用率。虚拟状态不能验证真实制动时间和距离。

## 5. 报告产物

在实现仓库生成 `reports/acceptance.json` 与 `reports/benchmark.json`。每项带 test_id、profile、status、命令/配置、结果摘要和证据路径；不要生成没有真实运行来源的 PASS。

最终 README 给出能复现的最短命令。自动测试清理自己启动的进程，不杀掉用户其他服务，不扫描和驱动未明确指定的硬件。


## 6. 2026-09-17 能力节点增量验收

实际命令：`./scripts/run.ps1 scripts/verify.py`。44 单测、6 真实 Zenoh 集成测试、两个示例、Ruff、doctor 通过；0 failed / 0 skipped。输出及 JUnit 在 reports/。

| 用户增量要求 | tests/integration/test_nodes.py 中的实际测试 | 结果 |
|---|---|---|
| 独立能力节点并行 | test_parallel_nodes_dependency_and_resources | PASS：WRS/TTS 同时运行，pick 等 observe、place 等 pick |
| 模型挂起时查询/直连控制 | test_hung_model_direct_voice_cancel_and_stop | PASS：挂起的是 MockClient，无 GLM API 调用 |
| 查询不中断、TTS 取消独立 | 同上及 test_cancel_tts_allows_robot_branch_to_finish_and_queue_runs_after_success | PASS：WRS epoch 不变，完成自己的分支 |
| 幂等与迟到结果 | test_dedup_missed_terminal_and_authentication、挂起模型测试；单测 test_late_model_revision_is_rejected | PASS：旧 epoch/revision 拒绝，重复动作执行一次 |
| 漏终态后查询恢复 | test_dedup_missed_terminal_and_authentication | PASS：两节点不订阅终态，以原 ID 查询 SUCCEEDED/验证结果 |

另测：独占进程、超时/取消等待、callback 线程桥接、router 重连不重提动作、停止不等磁盘写入、UNKNOWN 禁止恢复、过期授权拒绝。

这不是完整 M8 矩阵通过声明。真实 WRS 仅 import/FK 通过；Vision、GLM 服务、真实音频、实机、远程 ACL、断网中运动的自动安全停车和高负载百分位均 UNVERIFIED。

## 2026-09-17：GLM 协议与版本管理增量

真实命令：`./scripts/bootstrap.ps1 -Extra glm`、`./scripts/run.ps1 examples/04_glm_task.py --dry-run`、`./scripts/run.ps1 scripts/verify.py`。

最终结果：92 项单元测试、8 项真实 Zenoh 集成测试通过，0 failed、0 skipped；roundtrip、并行中断、GLM 离线示例、Ruff、doctor 均 PASS。报告仍仅保存在本地 reports/。指定 Python 3.12.0、Zenoh/router 1.9.0、httpx 0.28.1，锁文件由 uv 0.12.15 生成。

另外从 Git 暂存区导出独立快照，使用相同项目依赖与 router，设置 RUST_LOG=info 执行 `./scripts/run.ps1 -m pytest -q tests/unit tests/integration -m 'not live_model and not audio_live and not hardware and not wrs'`：100 passed；GLM 离线示例和 Ruff 通过。快照包含已验证的 router 版本解析修复。

新增 GLM 证据：原生单工具计划、纯文本回答、多工具/未知工具拒绝、权限字段和依赖环拒绝、截断/拒绝/非法 JSON、配置能力限制、总超时/取消、HTTP 错误脱敏、响应大小上限、无重定向/计费端点回退；GLM HTTP 夹具迟到结果仍受 Runtime revision 栅栏限制。工具建议从不直接执行。

真实 GLM 请求仍 UNVERIFIED：用户确认国内账号，未确认可用模型及自建 Runtime 的套餐授权，本轮未联网调用。Claude 格式、流式输出、GLM TTS、真实音频和实机未验证。默认节点继续使用 MockClient；没有把离线夹具称为真实服务验收。

## M3–M5 本轮结果（2026-09-17）

最终命令：`./scripts/run.ps1 scripts/verify.py --wrs`。117 单元、19 真实 Zenoh/Mock 集成、5 真实 WRS 虚拟节点集成，共 141 passed，0 failed / 0 errors / 0 skipped。按 marker 分组的 deselected 不算跳过或失败。既有测试保留，新增 41 个测试用例。

01/02/04 原有示例、03 正常完成与 --cancel、05_cache_reuse、09_skill_library、Ruff 和 doctor 均 PASS。本地证据 reports/acceptance.json、unit.xml、zenoh.xml、wrs.xml、m3_m5_summary.json 及对应 .txt；报告不上传 Git。

关键新增验证：
- 真实 WRS Lite6：ACCEPTED/进度、snapshot/status、关节与 FK 后置条件、cancel/hold、停止后状态稳定、resume 仅恢复准入、旧 epoch/revision 拒绝、幂等、Runtime 调度与进程清理。
- 单元慢 FK：同步调用未返回时可收 hold，但 stop_confirmed=false；返回后才确认停止。异常进入 UNKNOWN，硬件使能拒绝。未支持的 WRS pick 使整个计划在任何 TTS 副作用前失败。
- GLM 原生 HTTP 夹具→ModelPlanner→Runtime→真实 Zenoh→Mock 节点闭环；HTTP 等待期间可查询、只取消 TTS、独立停止 WRS，迟到结果和迟到错误不可恢复执行。
- 技能别名/标签检索与能力过滤；缓存拒绝否定、数量、时序、目标/相关位置/标定/能力/技能/绑定/Schema 变化，无关对象变化仍适用。命中后的动作 ID 全新，失败任务不写入可用条目。
- 一次恢复：grasp_once/localization_once 成功；持续失败只重试一次；UNKNOWN/inconclusive、前置条件错误不重试；恢复观察期间停止后无新抓取。

缓存示例实际 model_calls=1→1→2→3，第二次命中减少一次 Mock ModelClient/Planner 调用。另有原生 GLM HTTP 夹具三次任务只生成两次 HTTP 请求，全部动作仍走真实 Zenoh 和验证。未发送真实 GLM，因此不报告付费请求节省或云延迟。

WRS 限制：headless FK 虚拟运动，固定模型单线程所有权；每个 FK 不可抢占，仅边界协作停止，没有控制器队列。pick/place/接触验证/碰撞规划/IK/RRT 动作链和实机均未验证或 unsupported。已有科学环境可运行，干净机器完整科学依赖重建未验证。真实 GLM 模型/用途授权尚未落实，流式输出、音频、硬件、双机保护与负载基准未验证。

## System Structure Consolidation 验收（2026-09-17）

命令：./scripts/run.ps1 scripts/verify.py --wrs。
120 unit + 26 真实 Zenoh/Mock + 5 真实 WRS FK = **151 passed**，
0 failed / errors / skipped；原 141 项保留，新增 10 项。
01/02/04/05/09/10 示例、03 完成/取消两模式、Ruff、doctor 全部 PASS。
证据：reports/consolidation_summary.json、acceptance.json、unit.xml、zenoh.xml、wrs.xml。

新增测试 tests/unit/test_registry.py、tests/integration/test_system.py 覆盖：
- 四节点身份/启动版本/ready、Vision 只声明、视图过期/离线/重启/错误身份与能力拒绝；
- 配置中的 WRS provider 改名后，Runtime 执行与停止不依赖名称；
- WRS/TTS 并行、查询不打断、Voice 局部取消和直控停止；
- Agent 退出后直连仍有效；离线/held provider 使任务在任何分支副作用前拒绝；
- 泛化 ActionContext 无物体字段、TTS 无 hold/resume、去重与重复取消；
- 不订阅终态也能按 ID 恢复状态，丢提交回执后查询补偿且只执行一次；
- Runtime 控制通道不调用普通 capabilities 查询。

WRS 仍仅验证 Lite6 FK 虚拟运动、停止边界和准入；没有新增机器人能力。
GLM 仍仅离线 HTTP 夹具；没有真实调用（账号模型/适用服务授权未落实）。
真实 ASR/TTS/Vision/硬件/双机 ACL 未验证。没有推进 M6/M7/M8 功能。
缓存算法未改，05 仍为 Mock model_calls 1→1→2→3。

## 同步脚本 API 验收（2026-09-17）

使用普通 with launch()、action/status/wait/cancel；主示例 03/10 不需要 async/await。
原 System.local() 异步入口保留。同步模块 105 行，无新增依赖、后台事件循环线程、
调度器、消息协议或设备实现；方法转发到原 System/Action。

实际运行 ./scripts/run.ps1 scripts/verify.py --wrs：
125 unit + 30 真实 Zenoh/Mock + 5 真实 WRS FK = **160 passed**，
0 failed/errors/skipped；原 151 项保留，新增 9 项。
最终 full run 的 Ruff 对一个参数化测试的 timeout 参数名报错；
仅改名 wait_timeout 后，单独重跑 tests/unit/test_sync_runner.py 为 5 passed，
ruff check wrs_agent tests examples scripts 为 PASS。未重复或降低功能测试。
所有示例、WRS 完成/取消、doctor 通过。

证据 reports/sync_api_summary.json、acceptance.json、JUnit；
先前 lint 输出保留 reports/sync_lint_before.txt，后续实测结果在 lint.txt。
新增覆盖：调用端 time.sleep 期间节点并行完成、wait 超时不取消动作、
TTS 局部取消/WRS 停止、任务依赖/进度迭代、异常/KeyboardInterrupt 退出清理、
启动失败、异步环境/跨线程/关闭后误用、watch 不跨 yield 取消调用者，
以及 timeout=None 兼容。

仍仅 Mock 音频与 WRS FK 虚拟结果；没有真实 GLM、ASR/TTS 或硬件调用。


## API 小步简化（2026-09-17）

基线 b69bd7a；实际 verify --wrs：134 unit + 32 Zenoh/Mock + 5 WRS virtual =
171 passed，0 failed/errors/skipped。示例、doctor、Ruff 通过；客户端命名清理后网络回归
32 passed，02/05 示例和 Ruff 再次通过。语义、公开名字、命令和限制统一记录在
[api_simplification.md](api_simplification.md)，证据 reports/api_simplification_summary.json。
未调用真实 GLM、音频或硬件；没有新增延迟保证。按要求未 commit/push。


## 不可变任务身份与 Zenoh Liveliness（2026-09-18）

命令：`./scripts/run.ps1 scripts/verify.py --wrs`。
**144 unit + 37 Zenoh/Mock + 5 WRS virtual = 186 passed**；0 failed/errors/skipped，保留前轮 171 的行为回归，新增 15 用例。
37 项网络组运行时 deselect 5 项 WRS；WRS 组独立运行全部 5 项，未将 deselection 计为通过。
所有示例、Ruff、doctor PASS。报告 reports/task_liveliness_summary.json、acceptance.json、JUnit。
02 示例验证 replacement 的新 ID 与 supersedes；新网络测试为 test_task_identity.py、
test_liveliness.py，覆盖旧控制/规划拒绝、重启和能力缓存失效、重复实例、在线但 held、
无关 TTS 离线不被机器人动作查询。原 Registry 过期测试迁移为原生离线事件测试；
进程退出后等待网络离线通知，不能把 terminate 返回当作通知已经送达。

完整 API 变化、兼容字段、命令及限制见 [api_simplification.md](api_simplification.md)。
WRS 仍只验证虚拟 Lite6 FK；未新增 pick/place 或实机能力。GLM 仅离线夹具，缓存调用
计数仍为 1→1→2→3；未访问真实模型/音频/硬件。控制 worker 排队问题和延迟测量留待后续。


## 分级同步教程与职责精简（2026-09-18）

实际命令：`./scripts/run.ps1 scripts/verify.py --wrs`。
146 单元 + 42 真实 Zenoh/Mock + 5 WRS 虚拟 = **193 passed**，0 failed/errors/skipped。
相对 186 增加 7 个用例；原非法工具参数测试移到最终决策校验边界，没有删去错误情形。
新增高频状态读取不消耗/挤掉授权、进度事件无凭证、真实网络查询后准入、非法 GLM
计划零动作，以及同步入口通过独立 Agent 复用计划并保持新 task/action ID。
全部分级示例、Ruff 和 doctor PASS，缓存示例计数仍为 1→1→2→3。
报告 reports/api_levels_summary.json、acceptance.json、unit.xml、zenoh.xml、wrs.xml。
示例新入口见 [examples/README.md](../examples/README.md)，旧平铺路径已迁移；
03 的 WRS 完成/取消均在新 tasks 路径通过。历史章节中的旧命令仅记录当时执行位置。
WorldSnapshot 不含 lease_id，ActionContext 含当前状态和执行凭证；TTS 的机器人可选字段为空，
并没有新增机器人控制接口。GLM 原生协议解析与 ModelPlanner 计划校验分开，权限/执行检查保持。
仍未验证真实 GLM/音频/实机/双机身份与端到端延迟，不把这些列为 PASS。


## 全库冗余清理（2026-09-18）

命令：`./scripts/run.ps1 scripts/verify.py --wrs`。
**151 单元 + 42 真实 Zenoh/Mock + 5 WRS 虚拟 = 198 passed**，0 failed/errors/skipped。
全部分级示例、WRS 完成/取消、Ruff、doctor PASS；CLI --help 通过。
新增 6 项显式绑定/CLI 门禁用例；删除 1 项专门验证已移除导入别名的测试，保留动作语义检查。
已有自定义节点名的集成测试增加「节点启动后不重读配置」断言。
初次子集的 3 项失败均为旧私有启动函数引用，迁移后完整回归通过。
证据 reports/library_cleanup_summary.json、acceptance.json 和 unit/zenoh/wrs.xml。
目录和低层 API 迁移见 [api_simplification.md](api_simplification.md) 文末。
普通 launch/action/start/goal/wait 不变；缓存模型调用仍为 1→1→2→3。
未验证真实 GLM、音频、硬件、跨机 ACL 和延迟；没有新增机器人能力或外部服务调用。


## 节点状态与技能注册（2026-09-18）

完整验收：`./scripts/run.ps1 scripts/verify.py --wrs`。
**162 unit + 43 Zenoh/Mock + 5 WRS virtual = 210 passed**；0 failed/errors/skipped。
所有分级示例、WRS 完成/取消、Ruff 和 doctor PASS；缓存调用仍为 1→1→2→3。
新增 7 项快照边界、4 项注册校验与执行、1 项真实 Zenoh 错误部署绑定测试。
既有 TTS 空机器人字段断言改为类型隔离检查，其去重、取消和恢复断言保留。
证据 reports/typed_nodes_summary.json、acceptance.json、unit/zenoh/wrs.xml；
切片证据 typed_state_boundary.xml（15 passed）、registered_skills.xml（36 passed）。
未新增源码模块或依赖；既有示例迁移到 snapshot().data。公开字段迁移见 api_simplification.md 文末。
真实 GLM、音频、实机、跨机 ACL 和延迟未验证；Voice 单 worker 等待 TTS 的限制保留。
WRS 仅虚拟 Lite6 FK，无新增抓取能力。没有 commit/push。


## 统一客户端与启动/连接（2026-09-18）

`./scripts/run.ps1 scripts/verify.py --wrs`：173 unit、47 Zenoh/Mock、全部示例、Ruff、doctor 通过；
首次 WRS 4 passed/1 failed。旧测试遍历全部连接，误把 Voice 当动作节点；保留原断言并限定 WRS/TTS。
`./scripts/run.ps1 -m pytest -q tests/integration -m wrs --junitxml=reports/wrs.xml`：复测 5 passed。
最终 **225 passed（173 + 47 + 5），0 failed/errors/skipped**；首次失败证据未删除。
新增 15 项回归，包含单独 TTS、另一 Python 程序同步连接、客户端异常退出仍继续动作、
重新连接查询原 action_id、配置节点晚到，以及部分连接失败清理和输入校验。
WRS/TTS 共用 ActionClient，TTS 仍无 hold/resume 服务；既有抢占、并发、迟到结果、去重回归保留。
证据 reports/launch_connect_summary.json、acceptance.json、unit/zenoh/wrs.xml、launch_wrs_initial_failure.txt。
模型为 Mock/HTTP 夹具，音频为回放/Mock TTS，WRS 为 Lite6 虚拟 FK；实机、GLM 真实服务、
真实音频、跨机 ACL 与延迟指标未验证；不新增相关能力或测试主张。


## 异步启动名称对齐（2026-09-18）

System.local 改为 System.launch，LocalStack 保留；无启动或控制行为修改。
本轮现有测试 197 passed（173 unit + 24 Zenoh/Mock），0 failed/errors/skipped；
入门动作、并行停止、WRS 虚拟三个示例 exit 0；Ruff 和 diff --check 通过。
命令与证据：reports/launch_name_summary.json、launch_name.xml、launch_name_examples.json。
没有重跑全部 WRS 测试，WRS 本轮证据为虚拟示例；上一轮完整回归 225 passed 保留为历史。
真实 GLM、音频、硬件、跨机和延迟未新增验证；没有 commit/push。

## 示例配置路径修复（2026-09-18）

两个示例以脚本位置定位仓库配置，保留 API/CLI 相对路径语义；路径在模块加载时解析，不放入异步函数。
既有进程/绑定/连接/router 回归 **39 passed，0 failed/errors/skipped**：
`./scripts/run.ps1 -m pytest -q tests/unit/test_processes.py tests/unit/test_registry.py tests/integration/test_connect.py tests/integration/test_router.py --junitxml=reports/example_paths.xml`。
两个示例分别从仓库根、各自脚本目录和仓库外临时目录启动，6 次全部 exit 0；
TTS 客户端完成动作后服务仍在线，启动器退出后自有进程已清理。
通过指定 Python -X utf8 -S scripts/run.py 启动，具体绝对路径命令、cwd 和依赖版本见
reports/example_paths_summary.json；Python 3.12.0、Zenoh 1.9.0、Pydantic 2.13.5、pytest 9.1.1、Ruff 0.16.8。
`./scripts/run.ps1 -m ruff check wrs_agent tests examples scripts` 和 `git diff --check` 通过。

补充 IDE 方式：从 examples/developer 将仓库根目录加入 PYTHONPATH，直接用指定解释器执行
01_zenoh_roundtrip.py，通过；共享环境 Zenoh 1.10.1、Pydantic 2.12.3，未修改共享环境。
普通终端不设置导入路径时直接执行会报 ModuleNotFoundError，使用项目启动器即可加载项目与锁定依赖。
首次 Ruff 的 ASYNC240 已通过将路径解析移到模块级修复，原输出保留 example_paths_lint_initial.txt。
原始配置失败、无导入路径的直接运行失败和 IDE 成功输出均在 reports/example_paths_*.txt。
本轮无剩余失败/阻塞，无新增依赖或协议改动；未运行完整回归、WRS 虚拟、真实模型/音频/硬件验证。
下一入口：在 IDE 重跑 developer/01_zenoh_roundtrip.py；普通终端从仓库根使用
`./scripts/run.ps1 examples/developer/01_zenoh_roundtrip.py`。


## 执行绑定、控制重试与任务句柄（2026-09-18）

已实现四项修复、客户端内部构造 ActionRequest、state_version / 协议 v2，以及 TaskHandle / GoalHandle。context 保留，自动接纳配置外节点或技能未实现。任务整体预检绑定参与实例与控制版本，独立分支保持隔离；Voice 同 ID 并发共享与重试不产生半成品成功；hold/replace 按参与者确认，连续替换仍追踪旧资源；Mock TTS 仅在历史全为已知终态时重新准入。

最终执行 `./scripts/run.ps1 scripts/verify.py --wrs`：**243 passed**（187 unit + 51 真实 Zenoh/Mock + 5 WRS 虚拟），0 failed/errors/skipped。标记选择的 deselected 属于分组运行，两个集成分组共同覆盖全部集成测试。所有验收示例（含新 task_handles）、Ruff、doctor 通过。`git diff --check` 单独通过；现有 WRS_AUDIT 换行提示不影响检查。

新增回归覆盖重启后旧任务拒绝、已提交不明动作 UNKNOWN、控制查询失败/回复丢失/并发重试、无关 TTS 离线、TTS 历史恢复、独立版本校验、连续替换、排队结果、4096 容量、重连与多客户端按 ID 查询、Runtime 重启旧 ID 不存在、等待超时/关闭观察/断开客户端不取消执行，以及后续规划失败不改写旧结果。

证据：`reports/revision_summary.json`、`reports/acceptance.json`、`reports/unit.xml`、`reports/zenoh.xml`、`reports/wrs.xml` 及对应 txt。切片证据为 revision_slice1.xml、revision_unit.xml、revision_zenoh.xml、revision_handles.xml、revision_observation.xml。迁移中出现过 4 项旧 Action 导入失败，已修复并完整复测；原失败记录 revision_slice2.xml 保留。首次 Ruff 报告的导入位置及长行已修复，最终输出 All checks passed。

依赖未变：Python 3.12.0，Zenoh 1.9.0（router 同版），Pydantic 2.13.5，pytest 9.1.1，pytest-asyncio 1.4.0，Ruff 0.16.8；WRS 固定 2bb014b747833c2fd9345115fbe26ffb11376f20。无新增包、锁文件或 submodule 变更。

未验证：实机、真实音频、付费模型、跨机认证/ACL、延迟指标及真实抓取；WRS 证据限 Lite6 虚拟 FK。任务/规划历史为 Runtime 会话内存，重启不恢复；动作日志仍保留。协议 v2 无旧字段别名，外部客户端与节点需一起升级。当前授权范围无剩余失败或阻塞；context 改造与动态接纳按用户要求留待讨论。没有 commit/push。

关键文件：wrs_agent/runtime.py、actions.py、nodes/actions.py、nodes/voice.py、nodes/agent.py、schemas.py、handles.py、system.py、sync.py、transport.py；对应 unit/integration 回归和示例一并迁移。完整调用链见 [task_handles.md](task_handles.md)，下一入口：`./scripts/run.ps1 examples/tasks/05_task_handles.py`。


## 结构化错误与技能合同版本（2026-09-19）

本轮只实施高收益的两项：统一 ErrorInfo / AgentError，以及节点声明 name -> version 并校验技能合同。参数 Schema 仍来自唯一的本地参数模型，Node Registry 不复制合同。Runtime 整体预检、节点准入、技能检索与计划缓存都检查版本；不兼容时任何分支都不会开始执行。普通 step()/action() 自动采用本地注册版本。

错误保留 code、stage 和已知的节点/任务/动作身份。提交前超时为 FAILED；已提交且无法查询确认的动作才为 execution_unknown / UNKNOWN。服务不返回任意异常或验证输入，规划缓存诊断也只保存错误码。资源冲突不停止其他客户端的动作，既有节点内最终准入继续生效。同步和异步共用实现；未改造 context、start/bindings 名称，未增加自动接纳、多实例路由或设备所有权服务。

`./scripts/run.ps1 scripts/verify.py --wrs` 全部通过：205 unit、53 Zenoh/Mock、5 WRS virtual，以及全部验收示例（含新增 errors_and_versions）、Ruff、doctor。末次审查将规划缓存诊断由异常原文改成错误码，新增一项回归后，`./scripts/run.ps1 -m pytest -q tests/unit --junitxml=reports/errors_final_unit.xml` 为 **206 passed**；规划、句柄、错误协议定向复测 **11 passed**，最终 Ruff 与 diff --check 通过。因此当前总数 **264 passed（206 + 53 + 5），0 failed/errors/skipped**，相对前轮新增 21 项测试；11 项复测不重复计数。各集成分组选中的 deselected 不算 skip。

新增证据覆盖：独立 TTS 进程重启后声明 speak@2，整个旧版本任务零执行；只用机器人的新任务不受影响；错误跨真实 Zenoh 和客户端重连保留身份；版本 2 匹配时可执行；旧计划缓存失效；离线/未就绪/歧义/未绑定/合同不兼容区分；提交前后故障、同资源冲突、原异常脱敏、规划结果不可被调用方修改。

证据汇总 `reports/errors_versions_summary.json`；完整验收 `reports/acceptance.json`、unit/zenoh/wrs.xml；最终复测 errors_final_unit.xml、errors_final_integration.xml、errors_final_checks.json。首次组合测试为 206 passed / 1 failed，旧 ActionHandle 测试夹具缺少 node_id/task_id，已补齐并加强身份断言；原始失败 errors_complete_targeted.xml 保留。自动审批曾拒绝整文件还原；改为精确匹配撤回本轮三处纯格式变化后通过，无待批准操作。

依赖无变化：Python 3.12.0、Zenoh/router 1.9.0、Pydantic 2.13.5、pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8，WRS commit 2bb014b747833c2fd9345115fbe26ffb11376f20。无新依赖、锁文件或 submodule 变更。未验证实机、真实音频、付费模型、跨机 ACL、性能基准和实际抓取；WRS 仅 Lite6 虚拟 FK。当前范围无失败或阻塞，没有 commit/push。

本轮消息合同不兼容：前缀为 wrs/v3/{site}/{target}，Envelope.schema_version=3，skills 从列表变为版本映射，RPC error 变为结构化对象；外部节点与客户端须一起升级。历史动作日志保留、不重播。主要修改 errors.py、schemas.py、transport.py、registry.py、skills/__init__.py、cache.py、actions.py、nodes/actions.py、runtime.py、system.py、handles.py 及对应测试。使用说明见 [errors_and_versions.md](errors_and_versions.md)。下一入口：`./scripts/run.ps1 examples/tasks/06_errors_and_versions.py`；context 和动态接纳继续留待单独讨论。


## 开发交接版：识别文本、节点/技能扩展与 GLM 执行链（2026-09-20）

本轮补齐识别文本 TextInput/TextReceipt、同步/异步 send_text、按规划 ID 重连、Runtime 当前任务中断入口。明确停止在本地控制路径执行，不等待 Planner；同时使待返回规划失效、清空追加队列，按参与资源停止。部分识别不占用去重编号，最终输入按稳定 ID 去重；重复停止不作用于后来的替换任务。Agent 不可达时尝试停止独立机器人动作，但仍报告 UNKNOWN，不能宣称整个任务已停止。

新增自定义 console TTS 节点和 greet 技能，客户端、Agent、节点共享同一合同，展示参数验证、静态绑定、直接 Action、Runtime 任务与取消。GLM 既有适配器增加规划错误保留；新端到端示例通过 HTTP 夹具执行到独立 Mock 节点，真实服务必须显式 --live-model。Mock 对不支持的输入改为 CLARIFY，不再默认生成 home 运动。WRS 复用现有真实 Lite6 FK 示例，不新增虚构抓取能力。

完整命令 `./scripts/run.ps1 scripts/verify.py --wrs`：**293 passed（226 unit + 62 真实 Zenoh/Mock + 5 WRS virtual），0 failed/errors/skipped**。集成标记的 deselected 属于两个分组选择，不是漏跑或 skip。全部验收示例（含 voice_control、custom_skill、glm_runtime）、Ruff 和 doctor PASS。相对前轮新增 29 项测试。

重点覆盖：最终文本输入与重连查询、同时识别/查询不打断、只取消 TTS、停止与显式替换、重复停止不重定向、模型挂起/迟到失效、控制端点鉴权、Agent 离线仍尝试机器人停止、仅 TTS 任务不依赖离线机器人、自定义节点从仓库外工作目录运行，以及 GLM HTTP 401 作为结构化规划错误返回且零动作执行。语音回归末次将停止确认断言改为有界等待，避免把 STOPPING 当 STOPPED；复测 `tests/integration/test_voice_text.py` **7 passed**，不重复计入总数。

交接示例另用两个独立 Python 客户端先后连接同一个自定义节点/Agent，各自完成直接调用、任务与取消；两次客户端退出后服务仍存活，启动器退出后全部自有进程已结束。证据 `reports/handoff_custom_connect.json`。没有创建新的插件平台或依赖注入容器。

本地证据：`reports/handoff_summary.json`、`reports/acceptance.json`、unit/zenoh/wrs.xml、`reports/handoff_voice_final.xml` 及各示例 txt；切片 handoff_text.xml（40）、handoff_extensions.xml（1）、handoff_glm.xml（40）。初次 Ruff 的两处格式问题已修复，最终 All checks passed；diff --check 通过。

依赖无变化：Python 3.12.0、Zenoh/router 1.9.0、Pydantic 2.13.5、pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8；WRS 仍固定 2bb014b747833c2fd9345115fbe26ffb11376f20，submodule 无修改。新增端点为 v3 的增量；此前 v3 技能版本/错误合同仍要求客户端和节点一起升级。

未验证/未实现：真实 ASR 与麦克风、有声 TTS、UI、真实 GLM 服务、WRS 抓放/碰撞、实机、跨机 ACL、性能指标和干净机器 WRS 全依赖恢复。context、动态接纳、设备所有权协调、持久任务恢复本轮不扩展。软件验收没有失败或阻塞，这些后续项目不记作已通过。语音输入是识别后的文本及保守词表，不是自然语言理解或硬件急停认证。

交接入口：[DEVELOPMENT.md](DEVELOPMENT.md)、[VOICE_INPUT.md](VOICE_INPUT.md)。后续开发者可分别接 WRS、TTS、ASR 和 UI，按文档合同交付各自的后端与实际证据。可运行命令：`./scripts/run.ps1 examples/tasks/07_voice_control.py`、`./scripts/run.ps1 examples/developer/05_custom_skill.py`、`./scripts/run.ps1 examples/developer/06_glm_runtime.py`。


## 删除任务替换与独立取消（2026-09-20，当前协议 v4）

删除 TaskHandle/Runtime 的 hold、replace，删除 supersedes、任务 HELD/RESUMING 和替换链。保留设备 hold；设备 resume 更名 allow_actions。任务通过 cancel → CANCELLING → CANCELLED/UNKNOWN 独立收尾；确认后 start 创建新任务。TaskCancelReceipt 的 STOPPING 仅表示受理；旧句柄、重复取消与重连始终绑定原任务。context 和自动接纳配置外节点/技能未改动。

最终 **303 passed（235 unit + 63 真实 Zenoh/Mock + 5 WRS 虚拟），0 failed/errors/skipped**。集成测试按标记分两组，deselected 不计为跳过。采用分组执行，已通过的组未重复运行；随后完成 verify.py 清单中的其余示例、Ruff 和 doctor 检查。

实际命令：

```powershell
./scripts/run.ps1 -m pytest -q tests/unit --junitxml=reports/cancel_unit.xml
./scripts/run.ps1 -m pytest -q tests/integration -m 'zenoh and not wrs' --junitxml=reports/cancel_zenoh_initial.xml
./scripts/run.ps1 -m pytest -q tests/integration -m wrs --junitxml=reports/cancel_wrs.xml
./scripts/run.ps1 -m ruff check wrs_agent tests examples scripts
./scripts/run.ps1 scripts/doctor.py --probe-wrs --output reports/cancel_doctor.json
```

其余检查由本地 reports/verify_cancel_remaining.py 顺序运行，17 项全部 exit 0（14 次示例运行，以及 WRS 测试、Ruff、doctor）。包含任务句柄、文本停止、自定义节点/技能、GLM 离线适配、WRS 完成/取消等全部验收示例；完整命令及输出文件列表见 reports/cancel_checks.json。

新增 10 项回归：取消空闲 TTS 时撤销在途旧动作；独立取消排队项；任务取消前/后另一次设备 hold 都不能被自动解除；取消中重启不控制新实例；原动作状态延迟、UNKNOWN、缺失、成功但未验证四种结果；真实 Zenoh 的取消回执丢失和并发重复不能误停新任务。既有资源隔离、旧任务跨重启、Voice 控制幂等、句柄重连与观察超时回归均迁移并保留。

开发者中断示例实际输出：持物停止后仍持 A、旧请求 stale_epoch、迟到模型失效；随后独立 start 的新 task_id 完成 A → C。普通任务例子使用识别后的文本，不使用旧的自动替换回放。旧的重复历史 API 说明已收敛为当前用法，Git 和本文件保留历史验收。

首轮 unit 为 225 passed / 1 failed：原测试在动作尚未 RUNNING 时就取消，却断言实际执行次数；恢复等待 RUNNING 的条件后，保留次数断言并通过最终 235 项。首轮 Ruff 格式与未使用导入已修复。初始 unit 证据 reports/cancel_initial_unit.xml 保留，未伪造为通过。

汇总与证据：reports/cancel_summary.json、cancel_checks.json、上述 XML、cancel_doctor.json 和各 cancel_*.txt。依赖未变：Python 3.12.0、Zenoh/router 1.9.0、Pydantic 2.13.5、pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8。WRS 固定 2bb014b747833c2fd9345115fbe26ffb11376f20，submodule 干净；没有新增依赖或锁文件变更。

协议使用 wrs/v4 与 Envelope.schema_version=4，外部客户端和节点需一起升级，无旧端点兼容层。真实硬件、GLM、ASR/有声 TTS、UI、跨机 ACL、性能与干净机器 WRS 依赖仍为 UNVERIFIED；不作延迟或安全认证声明。DimOS 仅核对参考源码，取舍见 [节点与消息](NODES_AND_MESSAGES.md)，未引入新 Module/Stream 框架或传输依赖。

下一开发入口：[开发交接](DEVELOPMENT.md) 与 examples/developer/05_custom_skill.py；分别推进 WRS、TTS、ASR、UI，任务控制统一使用 cancel/wait/start。


## 2026-09-20：全部示例重写

本次把所有示例改成独立场景，删除旧 developer 组合脚本及参数切换方式。现在有 33 个入口文件、1 个共享技能文件；最长 Python 教学文件 54 行。普通动作/任务使用直接的同步代码，常驻服务和底层通信使用单一 async main。地址、目标和配置在文件中明确给出。

- 23 个可自行结束的离线示例：基础 4、任务 6、Voice 文本 4、WRS 虚拟 3、模型离线 2、传输 4。
- 8 个配对入口：connect 的服务/客户端 2 个；nodes 的 Router/Speaker/Agent/直接调用/任务/取消 6 个。测试启动真实 Router 和独立节点，从其他工作目录运行客户端，确认客户端退出后服务继续运行，最后清理自身进程。
- 2 个真实 GLM 入口默认关闭，只测试未经代码 opt-in 时拒绝运行；没有付费或外网模型调用。
- `greet_skill.py` 是唯一共享合同与实现，不是另一份启动脚本。

节点运行逻辑从 CLI 提取到 `wrs_agent/nodes/serve.py`，通过明确关键字参数调用；节点创建函数只接收日志路径。CLI 和 Python 示例复用同一逻辑，没有新增插件、传输、调度器或协议。Skill.arguments 仍表示技能参数合同，和已删除的示例命令行参数解析无关。

实际运行：

```powershell
./scripts/run.ps1 scripts/verify.py --wrs
./scripts/run.ps1 -m pytest -q tests/integration/test_task_identity.py::test_cancel_while_holding_keeps_effects_and_rejects_late_work --junitxml=reports/examples_interrupt.xml
./scripts/run.ps1 -m ruff check wrs_agent tests examples scripts
git diff --check
```

第一条完整验收：243 unit、64 Zenoh/Mock、5 WRS virtual，共 312 passed；23 份有限时长示例逐份运行，退出码和关键结果全部符合预期，Ruff / doctor 通过。`reports/acceptance.json` 共 28 项 PASS、0 FAIL；7 项未验证范围独立保留，不计入通过。

最终审阅将旧中断大脚本中的组合断言迁入一条正式回归，并单独运行通过：持有 A 时取消，保持已发生的抓取效果；停止前的动作凭证被拒绝；迟到模型结果失效；新任务只执行放到 C 和验证。最终唯一测试合计 **313 passed = 243 unit + 65 Zenoh/Mock + 5 WRS virtual**，0 failures/errors/skipped。不是声称新增测试已包含在前一次完整命令中；增量证据为 `reports/examples_interrupt.xml`。新增回归后再次执行 Ruff 和 diff 检查通过。

测试开发过程中，配对测试最初误查不存在的 request/agent/status，出现 1 failed / 41 passed；修正为现有 request/task/status 后两项配对测试通过，并在完整验收中再次通过。初始静态检查提示异步函数使用阻塞进程创建，已改为 asyncio subprocess；没有放宽检查规则或制造 PASS。

证据保存在本地 `reports/unit.xml`、`zenoh.xml`、`wrs.xml`、`examples_interrupt.xml`、`example_*.txt`、`doctor.json`、`examples_rewrite_summary.json`。修改过的 Markdown 相对文件链接全部有效；examples 中不存在 argparse、sys.argv 或 parse_args。旧故障语义保留在正式测试，不再嵌入教学脚本。

验证环境：Python 3.12.0；eclipse-zenoh / zenohd 1.9.0；Pydantic 2.13.5；pytest 9.1.1；pytest-asyncio 1.4.0；Ruff 0.16.8。WRS gitlink 仍为 `2bb014b747833c2fd9345115fbe26ffb11376f20`，submodule 无改动；WRS 探测依赖 numpy 1.26.4、scipy 1.16.2、mujoco 3.5.0、wgpu 0.32.0、websockets 15.0.1。未变更依赖或锁文件。

本次软件和示例无剩余阻塞。WRS 抓放、硬件、真实 GLM、真实 ASR/音频、独立 Vision、双机保护配置和性能仍未验证；默认无录音和权重下载。context、Blueprint、动态接纳继续留待讨论。交接下一入口为 `examples/nodes/greet_skill.py`、`examples/nodes/01_start_speaker.py` 和 `docs/DEVELOPMENT.md`。沿用用户选择，只保留本地提交，不上传。


## 2026-09-20：state 字符串枚举

公开导出 ActionState、TaskState、GoalState（Python 标准库 StrEnum）。ActionStatus、TaskStatus、TaskCancelReceipt、GoalStatus 和 GoalResult 返回对应枚举；Runtime / ActionExecutor 使用枚举成员表达状态变化，Journal 重启恢复显式写入 ActionState.UNKNOWN。TaskCancelReceipt 仍只允许原有五个状态，不放宽为所有 TaskState。

保持原字符串值、协议 v4、JSON 和 SQLite 文本格式。状态边界只转换合法字符串，Boundary 的 strict=True 保留，其他字段不放宽。model_dump() 保留枚举，mode="json" / model_dump_json() 仍输出字符串；旧字符串比较、打印、哈希查找可继续使用。Runtime 总览与节点目录保持原始字典；IDLE、步骤结果、控制 phase 和设备 admission 不纳入这三个结果枚举。

实际执行 `./scripts/run.ps1 scripts/verify.py --wrs`：**390 passed = 320 unit + 65 Zenoh/Mock + 5 WRS virtual**，0 failures/errors/skipped；23 份离线示例逐份通过，配对服务/客户端测试通过，Ruff、doctor、git diff --check 和修改文档的本地链接检查通过。verify 报告为 28 项 PASS、0 FAIL、7 项 UNVERIFIED。

新增 77 项参数化兼容性检查，覆盖所有原状态值从 Python 字典/JSON 解码、枚举身份、嵌套回执、原字符串比较与序列化、精确 Schema 合法值、无效/跨域状态、取消回执子集、其他字段严格验证及旧 SQLite 日志恢复。既有真实 Zenoh 同步/异步、watch、重连、取消、规划、WRS 测试增加枚举身份断言，同时保留既有字符串断言验证兼容。

开发首轮直接将枚举类传入 BeforeValidator，触发 Pydantic validator-signature 导入错误；改为显式单参数转换后，合同/执行器重点组 31 passed，枚举/Runtime/取消重点组 98 passed，随后上述完整验收通过。没有调整全局 use_enum_values、关闭 strict 或跳过失败用例。

证据：本地 reports/state_enums_summary.json、acceptance.json、unit.xml、zenoh.xml、wrs.xml、example_*.txt 与 doctor.json。环境仍为 Python 3.12.0、Pydantic 2.13.5、Zenoh/router 1.9.0、pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8；依赖、锁文件、WRS gitlink 未变。真实硬件/GLM/ASR/音频没有调用，既有未验证范围未改变。

本次无剩余实现阻塞；用法见 [状态枚举](task_handles.md#state-的字符串枚举) 和两个更新的取消示例。WRS backend 接口的待澄清项不影响本改动，也未因此连接其他 backend。沿用用户选择，只本地提交，不上传。


## 2026-09-21：进程启动可移植性

最终定向回归 56 passed / 0 failed / 0 skipped，覆盖另建含空格/中文路径虚拟环境、普通 site-packages 子进程、文件锁跨进程互斥/释放、自定义 Router、版本/凭据检查、独立节点示例及 TTS 取消复测。本轮 Python 文件 Ruff、git diff --check、PowerShell PATH 解释器入口通过；bootstrap 仅语法解析，未重装依赖。

全量 verify.py --wrs 实际 exit 1：332 unit passed、Zenoh 67 passed / 1 failed、WRS 虚拟 5 passed。TTS 取消等待超时在后续定向组中通过，初始失败原因未确定，保留原证据。运行期间另一任务修改 WRS 技能、示例和验收目录；旧进程持有原目录，遇到序列输出改变、缓存/离线模型示例删除，以及新模型示例的一条 Ruff 行宽错误。因此不宣称本轮完整验收通过。

原节点端口 7448 已占用，没有终止该服务；测试复制脚本到临时目录，仅替换端口/env_id 后独立运行，保留相同合同与处理函数。其他 WRS/示例改动保留；本轮 WRS 文件只调整科学依赖路径。

证据及完整命令：reports/portable_processes_summary.json、portable_final_tests.xml/txt、portable_full_acceptance.json、portable_verify.txt、portable_doctor.json。Windows/Python 3.12.0；Zenoh/router 1.9.0，Pydantic 2.13.5，pytest 9.1.1，pytest-asyncio 1.4.0，Ruff 0.16.8。Linux/macOS、独立 wheel、真实模型/音频/硬件未验证。未提交或推送。下一入口：并行改动完成后重跑完整验收，再在 Linux/macOS 验证原生文件锁和启动/退出。


## 2026-09-21：在线模型示例与实际 WRS 节点验收

models 收敛为 01_plan.py（在线提出计划）和 02_execute.py（在线规划后 WRS 执行），移除代码关闭开关与离线执行入口。固定 GLM 协议样本移至 models/fixtures，所有测试引用已迁移；旧 developer 缓存空目录删除。凭据只从环境读取，没有写入代码；GLM_API_KEY 与 GLM_MODEL 当前未配置，在线账号调用记为 UNVERIFIED。

所有机器人教学脚本显式使用 wrs_virtual；原 Mock 抓放示例改为命名姿态、相对位移、观察，Mock 缓存演示删除，缓存回归测试保留。方向移动由固定 WRS Lite6 实际 IK/FK 完成；世界坐标系每次非零总位移最多 5 cm，保持目标朝向、限制关节与近邻解，末端结果实测验证。控制循环不等待运动锁；停止期间迟到的 IK 不会执行；不可达明确 FAILED 并保留原状态。

新增独立 WRS 服务、方向控制客户端与只读 viewer；viewer 使用 WRS 原生页面，不另建前端框架。页面/场景推送在回环，停止观察不取消任务。节点按原规则带历史日志重启后 HELD，控制客户端先查询停止确认，再显式 allow_actions 允许新任务；UNKNOWN 不自动解除。集成测试使用临时端口、命名空间和示例副本，保留用户正在运行的服务与日志。

最终分组证据：**419 passed = 344 unit + 68 Zenoh + 7 WRS**，0 failures/errors/skipped；**21 份有限机器人示例**逐份通过，Ruff、doctor、git diff --check 与更新文档的本地链接检查通过。独立 WRS 节点/客户端/viewer、重启与退出清理包含在 7 项 WRS 中。额外捕获真实 scene_init 和 14 条 scene_update，任务 SUCCEEDED，关闭 viewer 后仍可查询节点。没有实际检查浏览器 GPU 画面，此项仍 UNVERIFIED。

原全量 `scripts/verify.py --wrs` exit 1，真实历史报告保留：unit 的 15 个失败来自一处以多个路径片段构造的旧 fixture 路径；WRS 的 1 个失败来自示例固定日志重启后仍处于 HELD。修复后分别运行 `-m pytest -q tests/unit --junitxml=reports/wrs_examples_unit.xml`（344 passed）及 `-m pytest -q tests/integration/test_wrs.py tests/integration/test_wrs_examples.py --junitxml=reports/wrs_examples_final.xml`（7 passed）。68 Zenoh、全部 21 示例和静态检查在全量中通过，未把原失败结果改写成成功；上面的最终数量由最终各组证据汇总。

所有 Python 命令使用 `D:\code\venv312\.venv\Scripts\python.exe -X utf8 -S scripts/run.py`。证据及完整命令见 reports/online_wrs_summary.json、wrs_examples_unit.xml、zenoh.xml、wrs_examples_final.xml、wrs_scene_evidence.json 与 example_*.txt。环境：Python 3.12.0、Zenoh/router 1.9.0、Pydantic 2.13.5、pytest 9.1.1、Ruff 0.16.8；WRS 科学依赖 numpy 1.26.4、scipy 1.16.2、mujoco 3.5.0、wgpu 0.32.0、websockets 15.0.1。固定 WRS gitlink 与依赖锁未变，未安装新依赖或连接硬件。实际抓放、碰撞规划、ASR/音频、跨机与性能仍未验收。

工作区另一路可移植性修改已保留，本轮未提交或上传。下一入口：配置真实 GLM 账号后先运行仅规划示例，再运行执行示例；浏览器打开 WRS viewer 检查 WebGPU 画面。

## 2026-09-21：GLM 环境配置错误提示

- [x] 复现只有 Key/地址、GLM_MODEL 为空时的 Pydantic string_too_short；原输出保留 reports/glm_model_config_before.txt。
- [x] GLMConfig.from_env 将缺失/空白模型、非法模型名/地址转成明确的 GLMError；GLM_API_KEY、GLM_BASE_URL 不代替模型选择，不猜测账号可用模型。
- [x] 01_plan/02_execute 在创建 HTTP 客户端或启动节点前，用对应环境变量的提示退出；不回显输入值。配置模板与示例文档补充 IDE 加载 .env 和 GLM_MODEL 的要求。
- [x] 指定解释器完整单元测试 **356 passed，0 failed/errors/skipped**；Ruff、git diff --check 通过。两个真实示例子进程在缺失模型的夹具环境下按预期 exit 1，仅打印清楚的配置提示，无 traceback。

命令前缀为 D:\code\venv312\.venv\Scripts\python.exe -X utf8 -S scripts/run.py；
单元命令尾部为 -m pytest -q tests/unit --junitxml=reports/glm_model_config_unit.xml；
Ruff 为 -m ruff check wrs_agent tests examples scripts。
全部命令、cwd、依赖版本和证据见 reports/glm_model_config_summary.json。
本轮没有读取或修改用户 .env/凭据，没有发送付费请求或运行 WRS/硬件；在线成功仍未验证。
下一入口：用户在 01_plan/02_execute 对应运行环境配置 GLM_MODEL 为账号实际可用的模型 ID，再运行在线示例。


## 2026-09-21：WRS 后端名称与可读 viewer 示例

用户确认保留真实 WRS 接入，将 wrs_virtual 统一为 wrs。CLI、LocalStack、serve_node、能力声明、当前示例和测试同步迁移；旧值在三处明确拒绝，不保留别名。仿真与实机区别仍通过 hardware=false、FK 验证与停止范围说明，不把真实 WRS 模型等同真实设备。

07_viewer.py 显式创建 World/Lite6/坐标轴，读取节点初始状态，在定时回调中检查启动实例和有效状态后更新 FK，最后关闭显示。show_wrs/create_viewer 包装已删除；viewer_hub 仅管理页面服务生命周期。并行任务新增的示例口令读取逻辑保留。

最终相关回归 85 passed（78 unit + 7 WRS），0 failures/errors/skipped；另在并行凭据改动到达后复测 viewer/独立节点/客户端组 1 passed，不重复计入总数。5 份代表性有限示例通过：beginner/01_action、tasks/01_sequence、voice/01_stop_task、transport/03_submit、wrs/04_move_relative。相关文件 Ruff、git diff --check 和修改文档链接检查通过。未重跑完整仓库测试；全仓库 Ruff 期间观察到另一组正在编辑的示例口令文件格式问题，未覆盖其工作。

首轮单元组 76 passed / 2 failed：并行 GLM 默认模型变更与缺失模型测试冲突；另一任务恢复显式模型配置后同一组重跑 78 passed，本轮未更改 GLM 行为。证据：reports/wrs_backend_rename_summary.json、wrs_backend_unit.xml（首次）、wrs_backend_unit_final.xml、wrs_backend_integration.xml、viewer_explicit_final.xml、wrs_backend_examples.json。命令使用固定 Python 经 scripts/run.py 执行，完整参数见汇总。依赖和 WRS gitlink 未改变，无硬件/在线模型调用；浏览器 GPU 画面仍未人工检查。未提交、未上传。

## 2026-09-21：本机示例自动共享口令验收

三组分进程示例（connect/nodes/wrs）已接入 examples/_session.py：首次由服务随机生成，原子写入 Git 忽略的 .local/example_tokens；配套客户端从同一仓库加载，设置当前进程环境。显式 WRS_AGENT_TOKEN 优先且不写文件；空值使用自动配置，非法值/损坏文件报错；不显示或记录口令。客户端首次先启动时，提示准确服务入口和显式配置的处理方式。模板/示例文档补充手动生成命令、启动顺序、重置步骤与本机范围。

**375 单元 + 9 集成 = 384 passed，0 failed/errors/skipped**（最终分组结果）。
单元命令：指定解释器 -X utf8 -S scripts/run.py -m pytest -q tests/unit --junitxml=reports/example_tokens_unit.xml。
集成组：tests/integration/test_example_token_connect.py、test_developer_examples.py、test_connect.py、test_wrs_examples.py，首轮 8 passed；自动口令测试随后扩展为 TTS/WRS 两个参数化场景，复测 2 passed，替换原 1 项自动口令场景后共 9 项，不重复计数。最终自动口令用例验证服务与客户端环境均没有 WRS_AGENT_TOKEN、实际动作成功、错误口令仍被拒绝、日志不含口令、客户端退出服务仍在运行。测试使用临时口令目录和独立端口/namespace，不修改用户 .env 或正在运行的服务。
新增 helper 单元检查 16 项，包含四个真实并发子进程首次生成时共享同一完整口令；最终针对 helper/自动连接的复测为 17 passed，WRS 参数化后自动连接组另有 2 passed。

Ruff 全仓库与 git diff --check 通过。测试开发中先修复 unit/integration 同名导致的收集冲突，再修正错误口令断言应匹配 AgentError（实际拒绝行为正常）；首次证据保留。命令、依赖版本、输出见 reports/example_tokens_summary.json、example_tokens_*.xml/txt。
当前 Windows 上验证；POSIX 权限断言仅在相应平台执行，Linux/macOS 未验证。核心 connect/Transport 权限规则未改，自动配置只用于受信任本机示例；未运行完整网络回归、真实模型/音频/硬件或浏览器 GPU 验证。下一入口：先运行 examples/wrs/05_start_node.py，再运行 06_control_arm.py 或 07_viewer.py；已有非空手动口令时仍需各进程一致。

## 2026-09-21：GLM 网络诊断与显式代理配置

新增脱敏 DNS/TLS/证书/代理/连接/协议错误码；通过异常类型和有限 cause/context 链分类，不记录异常原文、Key、响应体或代理凭据。01_plan 在请求失败时打印明确说明并正常清理节点/HTTP 客户端。原 Runtime 的结构化错误会保留新代码和静态说明。
新增可选 GLM_TRUST_ENV（默认 0）：1/true 使用 HTTPX 的系统/环境代理与 CA 配置；默认直连不读取代理或 CA 环境。核实 HTTPX 0.28.1 中显式 transport 会关闭环境代理发现，因此 opt-in 使用标准 transport；离线 MockTransport 始终禁用环境代理。未修改用户 .env、系统代理/DNS，也未关闭证书验证或切换模型端点。
scripts/check_glm_connection.py 使用无 Authorization 的 GET，只检查网络，不读取 Key、不调用模型、不启动机器人。--trust-env 用于单独比较代理路径；401/404 等也可表明已收到 HTTP 响应。

实测当前环境直连 cause=socket.gaierror、Windows 11001；系统代理 127.0.0.1:10808，首次探测为 SSLEOFError，后续诊断为 glm_timeout。两条路径均未收到 HTTP 响应，因此当前网络仍未连通，账号/模型调用未验证。没有修改或隐去这些失败结果。
最终相关回归 **400 passed，0 failures/errors/skipped**：指定解释器 -X utf8 -S scripts/run.py -m pytest -q tests/unit tests/integration/test_glm_runtime.py --junitxml=reports/glm_network_regression.xml。覆盖错误链分类、敏感输入不回显、异常后清理、显式代理选择、离线夹具隔离、无认证诊断及既有 GLM/Zenoh 软件行为。Ruff 全仓库与 git diff --check 通过；首次行宽问题仅格式修正。
证据：reports/glm_network_summary.json、glm_network_regression.xml/txt、glm_network_lint.txt、glm_network_probe.json、glm_dns_proxy_probe.json、glm_connection_direct.txt、glm_connection_system_proxy.txt。
下一入口：先让 check_glm_connection.py（直连或 --trust-env）取得 HTTP 响应，再在模型进程设置对应 GLM_TRUST_ENV 并重跑 01_plan。未发送真实模型请求、付费调用或硬件动作；不把离线回归通过视为外部网络已修复。


## 2026-09-22：Qwen 本地中文 ASR / TTS 与 WRS 语音例子

新增 qwen-asr/qwen-tts 互斥 extras 和真实 uv.lock，两个环境由固定 Python 3.12.0 创建到 .local/venvs；uv pip check 均通过（ASR 97 包，TTS 91 包）。核心不安装语音依赖。模型固定官方提交、Apache-2.0 声明与全部文件内容哈希，已显式下载并校验；没有 API Key、录音上传、硬件控制或默认声卡调用。

接入沿用 Voice 文本输入、Task 取消与 speak 动作合同。模型/设备调用在线程中，停止生成后丢弃迟到音频，播放 abort/close 无法确认时 UNKNOWN。固定中文指令精确匹配，有词汇提示但没有模糊运动授权；识别期间实例或 control_epoch 变化会拒绝旧输入。WRS 08 服务、09 ASR 客户端与 07 viewer 展示完整链条，播报与运动并行；固定播报启动前预合成。

核心最终全量单元 **429 passed**（reports/qwen_core_unit_final.xml）；原有 Zenoh/WRS 集成 **77 passed**，新增生产语音适配 + 真 WRS/Voice/Zenoh 的取消集成 **1 passed**，后者声音设备使用替身，不算真实声卡验证。另执行 voice/01_stop_task、wrs/04_move_relative 两个有限真实 WRS 例子通过。末尾补充查询显示后再对语音相关组复测，见最终汇总。Ruff、uv lock --check、默认 core requirements 导出与示例目录检查通过。

真实模型证据：Qwen TTS 加载 8.35 秒，两个样本生成 4.64 / 2.55 秒；生成中发出停止后 0.302 秒返回无音频结果。WRS/Voice/TTS 加载并预合成七句约 44.27 秒；同时 ASR 推理时整张 GPU nvidia-smi 采样峰值 5457 MiB（含桌面其他占用）。中文 ASR 加词汇提示后，两条合成样本“向上”“停止”均精确匹配，0.132 / 0.129 秒；不含收音与截句时间，不是统计性能承诺。初次无词汇提示的“向上”识别为“想上”，未把这一失败隐藏为成功，也没有将误识别映射为运动。

真实收音、声卡播放/取消、回声、方言/噪声准确率、现场端到端停止延迟仍 **UNVERIFIED**。该限制不通过 mock 结果替代。具体安装/启动命令见 QWEN_SPEECH.md；原始证据 reports/qwen_*.txt/json/xml 与 .local/speech-check。默认脚本只观察/检查，不自动开麦或播放。保留原有用户及并行更改，未提交或上传。


## 2026-09-22：WRS TCP 字段与并行感知设计

完成状态命名与语义迁移：kinematics.joints → qs，tip_position → tcp_pos；新增 tcp_name 与 tcp_rotmat。固定 WRS 的 Lite6 注册 flange TCP，适配器现在读取该具名 TCP，IK 和到位检查使用同一对象；修复原实现依赖最后连杆与零工具偏移重合的假设。所有当前示例、viewer、测试和 doctor probe 同步迁移，RobotData.pose 仍为命名姿态标签。消息封装/Zenoh v4 路径未变，但运动学字段不向后兼容，服务与客户端需同步更新重启。

真实命令（cwd 为仓库根目录）：
```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit tests/integration/test_wrs.py tests/integration/test_wrs_examples.py tests/integration/test_wrs_tcp.py tests/integration/test_example_token_connect.py --junitxml=reports/wrs_tcp_regression.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py examples/wrs/01_move.py
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py examples/wrs/04_move_relative.py
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py examples/tasks/01_sequence.py
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py scripts/doctor.py --probe-wrs --output reports/wrs_tcp_doctor.json
git diff --check
```

结果：**433 passed = 423 unit + 10 integration，0 failures/errors/skipped，63.70 s**。新增真实 WRS 工具偏移与旋转测试：TCP 不等于末连杆，TCP 相对移动/朝向保持正确；改变工具偏移后原到位目标不能继续通过验证。既有 IK 迟到停止、取消、任务、独立节点/客户端/viewer、自动口令配对通过。3 份有限示例的预期输出通过，Ruff、doctor、git diff --check 与修改文档本地文件链接检查通过；没有失败被改写或跳过。

环境：Python 3.12.0、Zenoh 1.9.0、Pydantic 2.13.5、pytest 9.1.1、Ruff 0.16.8；WRS 固定提交 2bb014b747833c2fd9345115fbe26ffb11376f20 未修改。科学依赖版本由 reports/wrs_tcp_doctor.json 记录。证据：reports/wrs_tcp_regression.xml/txt、wrs_tcp_lint.txt、wrs_tcp_example_*.txt、wrs_tcp_doctor.json/txt、wrs_tcp_summary.json（实际示例命令和输出检查）。

[状态与并行感知设计](SCENE_AND_PERCEPTION.md) 区分必要的坐标归属与暂不增加的 scene_revision，说明 Node 常驻/Skill 按需、相机独立发布与多个消费者并行订阅。DimOS 参考本地固定提交 29dfda595892dffb91c79f379eb44d1c737f9caf 的 CameraModule、Detection2DModule、backpressure 和 ObserveSkill，没有引入其依赖或复制实现。

SceneData、真实相机、视觉/抓取候选节点仍为设计，未运行模型/麦克风/硬件或真实抓取；浏览器 GPU 画面未人工检查。未重跑全部非 WRS 网络集成。保留其他任务的 Qwen/语音与依赖修改，不提交、不上传。本轮已授权的命名修正及调查完成；下一入口是按设计文档交付有界相机数据流，再扩展具体场景与抓取技能。


## 2026-09-22：启动播报、独立语音入口与示例整理

WRS 01–07 保留机器人/显示；原语音文件移动到 Voice 05/06，01–04 明确文字入口；新增在线 GLM 服务 07、循环短句收音 08 和完整 viewer 09。新增 TTS 01 独立启动/预合成/启动播报、02 提交、03 取消。各目录 README 提供无需模式参数的运行顺序。同步 launch 补齐 tts_backend/tts_python/tts_prepared_texts；核心库不会自动发声。

Voice 本来就是独立 Zenoh 节点；ASR 是独立输入进程，不新增另一套节点注册。普通目标要求“机器人”前缀，停止免唤醒；观察模型/任务的协程不阻塞收音。识别期间 boot_id/control_epoch 变化会拒绝迟到目标。仍为短句分段 ASR，有收音间隙，没有新聊天记忆/流式协议。

真实命令（固定解释器，仓库根目录）：
```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit --junitxml=reports/voice_examples_unit.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_qwen_voice_wrs.py tests/integration/test_voice_text.py tests/integration/test_wrs_examples.py tests/integration/test_example_token_connect.py --junitxml=reports/voice_examples_integration.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_voice_examples.py tests/integration/test_qwen_voice_wrs.py::test_continuous_voice_stop_invalidates_pending_plan_without_waiting_for_model --junitxml=reports/voice_examples_final.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_qwen_voice_wrs.py::test_continuous_voice_stop_invalidates_pending_plan_without_waiting_for_model --junitxml=reports/voice_examples_stop_final.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts
```

全量单元 462 passed；后续增量 32 unit passed，其中新增 1 项同步参数透传。集成首跑 11 passed/1 failed；新增测试断言先误用当前规划 ID，后未等模型实际进入。修正测试为等待 planner_calls==1，最终复测 1 passed，未改变控制实现。12 个相关集成已分别通过；不隐藏早期失败报告，不把增量重复计入总数。

8 份有限例子通过：Voice 01–04 与 WRS 01–04，真实命令、输出和预期核对见 reports/voice_examples_scripts.json。Ruff、本地文档链接、示例目录和 git diff --check 通过。测试不调用扬声器、麦克风或付费模型。

**UNVERIFIED**：实际启动音播放、声卡停止、循环收音/回声/现场延迟、在线 GLM 语音全链。自动审批拒绝了真实播放探针，理由为此次音频设备副作用缺少明确授权，未执行该命令。软件测试使用音频替身不能代替现场验收；模型安装与既有合成/ASR 数据见 [Qwen 指南](QWEN_SPEECH.md)。完整结果与依赖版本见 reports/voice_examples_summary.json。本轮无新增依赖/协议、不提交或推送。


## 2026-09-22：ASR 的 IDE 工作目录与解释器

修复下载/加载默认目录随 cwd 改变的问题：二者共用项目源码根目录下的绝对 model_root，显式目录与 WRS_AGENT_MODELS 可覆盖，保留版本及文件校验。选错解释器时先提示已准备的 qwen-asr 环境，不先加载 Torch 再报模块缺失。更新 Voice/Qwen 指南；未自动重下权重或改公共 venv。

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_speech.py tests/unit/test_voice_examples.py --junitxml=reports/asr_path_unit.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent/speech scripts/download_speech_models.py tests/unit/test_speech.py
# 下条实际工作目录为 examples/voice，仅加载模型和识别既有测试音频：
& 'D:\code\ch\agentOS\.local\venvs\qwen-asr\Scripts\python.exe' -X utf8 'D:\code\ch\agentOS\reports\asr_path_probe.py'
```

验证结果：64 项相关单元测试通过（speech + voice examples），Ruff 与 git diff --check 通过；首次 Ruff 仅多余空行问题，已修正。实际从 examples/voice 工作目录，用项目内 ASR 解释器完成离线模型加载 11.92 秒、预热 1.10 秒；两条既有合成样本“向上”“停止”均正确识别，推理 0.168/0.147 秒。ASR 与 TTS 资源均定位到项目根目录下既有模型，未重新下载，未绕过校验。公共解释器实际触发新的明确依赖提示。没有开麦、播放、云端请求、节点连接或硬件动作。
证据：reports/asr_path_before.json、asr_path_unit.xml/txt、asr_path_probe.py/json/txt、asr_path_base.json、asr_path_lint.txt。依赖 qwen-asr 0.0.6 / transformers 4.57.6 / torch 2.9.0+cu128，未安装/更新依赖。此修复完成；用户将 IDE 中 Voice/06、08 的解释器切换为 .local/venvs/qwen-asr/Scripts/python.exe 即可使用现有安装。持续收音/现场声音仍不属于本次验证。


## 2026-09-22：按用户要求将 ASR 安装到 venv312

用户明确要求使用已有 D:\code\venv312\.venv，覆盖此前不修改公共环境的默认选择。以 uv pip install 增量安装 qwen-asr==0.0.6、torchaudio==2.9.0+cu128、sounddevice==0.5.6。安装前记录全部可见包版本并作为约束（include-system-site-packages=true，应按 importlib.metadata.version 的实际可见优先级，不能用重复 distribution 的最后一项）；只放开 Transformers 必需的 huggingface-hub<1。最终新增 29 包，仅已有 huggingface-hub 1.3.2 -> 0.36.2；Torch 2.9.0+cu128、Torchvision 0.24.0+cu128、NumPy 1.26.4、SciPy 1.16.2、Pydantic/Zenoh 等原版本保留。没有 sync 清理环境、没有改 Python 基础环境或重新下载模型。TTS 继续使用既有独立环境，两个 SDK 的 Transformers 精确依赖仍不可合并。

实际命令（仓库根目录）：
```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.local/uv-cache'
& '.local/tools/bin/uv.exe' pip install --python 'D:\code\venv312\.venv\Scripts\python.exe' --torch-backend cu128 -c reports/venv312_asr_constraints.txt 'qwen-asr==0.0.6' 'torchaudio==2.9.0+cu128' 'sounddevice==0.5.6'
& '.local/tools/bin/uv.exe' pip check --python 'D:\code\venv312\.venv\Scripts\python.exe'
# 模型验证的实际 cwd 为 examples/voice；探针像 IDE 一样加入项目源码根目录：
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 'D:\code\ch\agentOS\reports\venv312_asr_probe.py'
```

模型验证通过：使用公共解释器离线加载 19.80 秒、预热 1.86 秒；已有合成样本“向上”“停止”正确识别，推理 0.151/0.160 秒。只识别本地样本，不开启音频设备、联网模型或运动节点；不是麦克风延迟实测。独立 WRS Lite6 加载/FK 检查通过。uv pip check 安装前后均只有两项原有缺失（joycon-robotics/hid、scipy-stubs/optype），没有新增冲突，不宣称整个共享环境完全健康。git diff --check 通过。

中间失败：离线 dry-run 因部分缓存缺失而无法解析，联网重试；首次快照重复包读取到系统 Torch 2.10，改为实际可见版本 2.9 后预检查通过；初次裸脚本模型探针没有 IDE 的源码路径，补充同等源码路径后成功；FK 探针首次漏传 tcp 名称，修正为 flange。均为探针/解析阶段问题，未以失败方案修改模型或底层接口。
证据 reports/venv312_asr_before.json、venv312_asr_constraints.txt、venv312_asr_dry_run*.txt、venv312_asr_install.txt、venv312_asr_check.txt、venv312_asr_probe_final.txt/json、venv312_asr_packages.json。Voice/Qwen 运行文档同步更新，不再要求本机用户切换 ASR 解释器。下一入口：IDE 按原命令运行 Voice/06；终端从仓库根目录用同一 Python 加 -m examples.voice.06_push_to_talk。此安装请求完成，无代码提交或上传。

## 2026-09-22：SceneData 快照、静态场景与 viewer

机器人节点的 NodeSnapshot.data 统一返回 SceneData：robot 保存 RobotData，objects 是有界的 ObjectData 映射，frame_id=world，单位 m；保留外层 boot_id/control_epoch/state_version 与执行准入。无重复 scene_revision。Mock 符号位置移至 objects[id].location，缓存/恢复检查同步迁移；SpeechData 不变。旧数据字段无别名，服务与客户端需一起更新重启。

WRS 接受显式 scene=本地 TOML，工作线程建立真实 Scene/SceneObject 并读回物体位姿；首个几何为 box。未知朝向/几何不补成已知，来源为 configuration 的静态物体不冒充传感器观测；物体上限 32，文件与快照受既有消息预算约束。同步/异步 launch、LocalStack、CLI 与直接 serve_node 透传配置。错误配置在启动前拒绝。05 加载 scene.toml 的工作台和方块，07 与 Voice/09 viewer 同步场景，新增 08_scene.py 可独立运行。未加入视觉节点/在线更新协议或碰撞规划；WRS pick/place 仍未实现。

真实命令（固定解释器，仓库根目录，完整参数和示例工作目录另见 reports/scene_summary.json）：
~~~powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit --junitxml=reports/scene_unit_initial.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_scene.py tests/unit/test_cache.py tests/unit/test_node_state.py tests/unit/test_speech.py::test_cancel_during_synthesis_does_not_play_late_audio tests/integration/test_wrs_scene.py tests/integration/test_wrs_examples.py tests/integration/test_example_token_connect.py --junitxml=reports/scene_focused.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit tests/integration --junitxml=reports/scene_regression.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_scene.py tests/integration/test_wrs_scene.py --junitxml=reports/scene_final.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py scripts/doctor.py --probe-wrs --output reports/scene_doctor.json
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py examples/wrs/08_scene.py
git diff --check
~~~

全量回归：**569 passed、1 failed、0 errors/skipped，316.90 s**（488/489 unit 通过，81/81 integration 通过）。唯一失败为现有 tests/unit/test_speech.py::test_cancel_during_synthesis_does_not_play_late_audio：停止已受理后，测试的假播放函数仍被工作线程调用。代码检查表明 asyncio stop 到线程 Event 的异步转发存在调度窗口；本轮未更改语音实现或掩盖失败，不声称该取消语义已通过。它在 focused 组复查通过，随后全量再次失败，按竞态记录，留给语音任务处理。

最后对 WRS 读回校验与没有 WRS 节点的错误场景配置补测：**21 passed（20 unit + 1 真实 WRS integration）**，是全量中的增量复测，不重复计入通过总数。此前 focused 组为 48 passed / 2 errors：同一个过大输入参数的 pytest 名称被写入 Windows PYTEST_CURRENT_TEST，造成 setup/teardown 环境变量过长；已改为短 ids，保留原失败 XML，后续全量/最终场景测试均通过该项。初始单元 468 passed / 1 speech failed 也保留原始报告。

5 个有限示例通过：beginner/01_action、transport/01_query、wrs/08_scene、wrs/04_move_relative、tasks/01_sequence。08 实际从 examples/wrs 工作目录启动，机器人与 table/A 坐标读回成功，无相对路径错误；命令、输出与预期核对保存在 reports/scene_examples.json 和 scene_example_*.txt。真实 WRS 测试覆盖 Scene 成员/旋转/位姿、未知几何不伪造、独立 viewer 的创建/更新/删除且不影响后端；独立 05/06/07 服务生命周期、动作/任务、缓存/恢复和 TCP 偏移回归通过。

Ruff、doctor WRS probe、git diff --check、示例目录与 123 条本地文档文件链接检查通过。Python 3.12.0、Zenoh 1.9.0、Pydantic 2.13.5、pytest 9.1.1、Ruff 0.16.8；WRS 固定提交 2bb014b747833c2fd9345115fbe26ffb11376f20 未修改，科学包版本见 reports/scene_doctor.json。无新增依赖。

未验证：真实相机/视觉/GraspNet、在线物体更新、实机、碰撞规划、浏览器 GPU 人工画面、真实音频设备、付费 GLM；本轮未调用这些能力。保留其他任务的语音/依赖修改，不提交或推送。后续入口：按 SCENE_AND_PERCEPTION.md 的并行订阅方案，完成有界相机流与可信观测接入，而非把图像塞入 SceneData。


## 2026-09-22：上传前完整验证与 TTS 取消竞态修复

上传整理保留既有四个本地提交；当前工作区的 WRS/SceneData、Qwen、GLM、示例与文档一起作为完整版本检查。发现前述 SceneData 验收留下的 TTS 取消竞态，先通过刻意延迟 stop relay 的确定性测试复现（1 failed，保留 publish_race_before.xml），再修复：合成与播放之间回到控制事件循环检查停止信号，已取消合成的结果不会因线程 Event 尚未同步而开始播放；取消期间仍等待工作线程退出，不把取消协程当作停止确认。没有新增消息协议或节点控制接口。

验证命令（仓库根目录；固定 Python 加 -X utf8 -S scripts/run.py）：
~~~text
-m pytest -q tests/unit --junitxml=reports/publish_unit.xml
-m pytest -q tests/integration -m "not live_model and not audio_live and not hardware" --junitxml=reports/publish_integration.xml
-m pytest -q tests/unit tests/integration/test_qwen_voice_wrs.py --junitxml=reports/publish_final_regression.xml
-m ruff check wrs_agent tests examples scripts
~~~

初次全量单元 489 passed；全部集成 81 passed。修复取消竞态后重跑全部单元及相关 WRS/Voice/TTS 集成，492 passed = 490 unit + 2 integration，0 failed/errors/skipped；两项集成已包含在前述 81 项，不重复计数。全部 22 个有限教学示例通过，逐条真实命令与输出核对见 reports/publish_examples.json。没有自动运行麦克风、扬声器、在线 GLM 或实机例子。

Ruff、uv lock --check --offline、当前本地文档链接、示例目录和暂存区 diff --check 通过；清理 viewer.py 末尾空白。扫描候选文件及既有未推送历史，未发现私钥/常见令牌模式；.env、本机口令、虚拟环境、模型权重、录音和 reports 保持忽略。第三方 WRS submodule 保持原提交且干净。模型缓存、ASR 环境安装与音频验证限制仍按各专题记录，不把软件回归替代真实设备验收。

证据 reports/publish_unit.xml、publish_integration.xml、publish_final_regression.xml、publish_race_before.xml、publish_examples.json、publish_lint_final.txt、publish_lock.txt、publish_scan.json；不上传本机生成的 reports。


## 2026-09-23：Planner 模型按线协议通用化

GLM 专用适配（`providers/glm.py`、`GLM_*`、`glm_*` 错误码、`--model-provider glm`、extra `glm`）替换为按线协议选择的通用适配：`LLM_PROTOCOL` ∈ openai_chat / openai_responses / anthropic_messages，其余为 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 与可选的 `LLM_REASONING_EFFORT`、`LLM_MAX_TOKENS`、`LLM_TIMEOUT_S`、`LLM_EXTRA_BODY`、`LLM_EXTRA_HEADERS`、`LLM_PROXY`。设计依据与拒绝项见 SOURCES.md S24，模块分层与扩展方式见 DEVELOPMENT.md。原 GLM 路径的安全性质逐项保留并对三种协议都有测试：显式 live opt-in、只读环境凭据、不跟随重定向、不重试已发送请求、整请求超时与取消、64 KiB 回复上限、错误不回显正文/URL/凭据、环境代理不生效、最多一个提案、散文只作回答、未完成/拒答不成为决定。

与原行为的有意变化：端点不再限定为两个智谱地址，改为必须显式设置并满足 https（明文仅限回环）、无账号/查询/片段；回环本地服务可不带 key；新增显式 `LLM_PROXY`；推理深度原样写入各协议字段，GLM 不再额外发送 `thinking.type=enabled`（厂商文档称 GLM-5.x 默认开启思考），原 `GLM_THINKING=off` 改为 `LLM_EXTRA_BODY={"thinking":{"type":"disabled"}}`；模型 ID 允许 `: / @ +`。

验证命令（仓库根目录，`WRS_AGENT_PYTHON=D:\code\venv312\.venv\Scripts\python.exe`）：
~~~text
./scripts/run.ps1 scripts/verify.py
.local/tools/bin/uv.exe lock --check --offline
git diff --check
~~~

verify：unit PASS（628 passed，0 failed/errors/skipped）、zenoh PASS（73 passed，12 WRS 项未选）、lint PASS、doctor PASS；uv lock --check 通过（130 包，差异仅 extra 名），diff --check 通过。环境：Python 3.12.0、Zenoh 1.9.0、Pydantic 2.13.5、httpx 0.28.1、pytest 9.1.1、Ruff 0.16.8。

发现并记录：`tests/integration/test_glm_runtime.py::test_glm_plan_runs_on_remote_mock_nodes` 在本轮改动前已失败——其断言要求所有技能说明都属于 robot 包，而提交 739c47c 起 `speak` 属于 speech 包；已改为按技能核对所属包，保留"每个技能携带自己的包说明"的原意。直接用 venv 解释器（不经 `-S scripts/run.py`）运行时，`tests/unit/test_example_tokens.py::test_concurrent_starters_publish_one_complete_credential` 在改动前后都稳定失败，经正式入口运行通过，与本轮改动无关，未处理。

未验证：三种协议只经按厂商文档手写的离线夹具测试，没有对 GLM、OpenAI、Claude 或任何兼容服务联网调用；GLM 的 Claude 兼容端点是否接受 `x-api-key`、各厂商对 `LLM_REASONING_EFFORT` 各取值的实际接受情况、`LLM_PROXY` 的真实代理连通均为 UNVERIFIED。未运行 WRS 集成组、在线/语音/实机示例。


## 2026-09-24：Qwen 工具调用兼容

现象一：DashScope `qwen3.5-27b` 返回的 `plan` 是 JSON 字符串，PlanDecision 拒绝。原因是 `tool_schema()` 直接发送 Pydantic schema，`plan` 为 `anyOf[$ref, null]`、没有顶层 `type`；Qwen 系服务端按每个参数声明的 `type` 还原值，缺失时按字符串保留（vLLM issue #46924、PR #36032/#38973 同类），GLM 的解析器则对非 string 参数尝试 JSON 解码，所以 GLM 不受影响。修复：`tool_schema()` 内联全部 `$ref`，`plan` 改为可省略的 `object`；PlanDecision 与本地校验不变。

现象二：`qwen3.8-flash` 在 `tool_choice=auto` 下有时把计划写进正文，按"正文只作回答"规则判定为 ANSWER、不执行。新增 `LLM_TOOL_CHOICE=auto|required`（默认 auto；Chat/Responses 发送同名值，Claude 映射为 `any`）。另在系统提示加入"step 只含 schema 字段、不复制技能 resources"，边界仍以 `extra="forbid"` 拒绝多余字段。

在线对照（qwen3.8-flash，思考关闭，目标"机械臂向前移动。"，每组 12 次，临时脚本不入库）：现行提示 + auto 4 次正文、1 次多余字段校验失败；追加"不要在正文写 JSON"的提示 + auto 7 次正文（未采用）；强制指定 propose_plan 12/12 工具调用；required 12/12 工具调用。qwen3.5-27b 修复后的在线复测由用户运行，错误从字符串 `plan` 变为 step 多余字段 `resources_note`，说明 schema 修复生效。

验证：`pytest tests/unit/test_llm.py tests/unit/test_processes.py tests/integration/test_llm_runtime.py` 248 passed；相关文件 Ruff check 通过，`git diff --check` 通过。未运行完整 verify、WRS 集成组和语音 viewer；`LLM_TOOL_CHOICE=required` 在 GLM、OpenAI、Claude 上未联网验证（UNVERIFIED）。

### 节点发现首次等待

现象：07 已就绪，09 连接后立即 `nodes()` 报 asr/voice 未就绪。`System.connect` 按后缀依次打开 4 个 Zenoh 会话，实测各会话的存活令牌在 connect 返回后陆续到达，最晚（asr）约 0.5–0.7 秒；`NodeRegistry.refresh` 首次只等 0.3 秒，之后按"无令牌"判为 offline。对运行中的 07 连续 3 次新建连接，首次 `nodes()` 每次都漏掉 voice/asr，1 秒后全部就绪。修复：首次等待上限改为 2.0 秒，节点在线时收到令牌即返回；此后仍由原生存活事件驱动。复测 5 次首次查询全部 5 节点就绪，耗时 0.39–0.64 秒。只有首次查询时节点确实不在线才会等满上限。

### move_relative 上限 5 cm → 10 cm

`RelativeMoveArgs` 每轴 ±0.1 m、合成位移非零且 ≤ 0.1 m，错误码改为 `displacement_must_be_nonzero_and_at_most_10cm`；技能描述、robot `SKILL.md`、examples/README、DEVELOPMENT 同步。技能版本仍为 1，节点与 Agent 必须使用同一份代码（`actions.py` 核对 parameters schema）。上文与 WRS_AUDIT.md 中的 5 cm 为当时记录，不改写。

运动学核对（真实 WRS UR7E `begin_relative` + FK，本地不连接硬件）：home/B/C 各 ±X/±Y/±Z 共 18 次 10 cm 目标全部有解，末端误差 < 0.001 mm，单关节最大变化 0.33 rad（执行层上限 1 rad）；5 cm 对照为 0.15 rad。仅覆盖命名姿态出发的单轴移动，不代表工作空间任意位置、斜向组合或连续多步都可达；仍无碰撞检查。

验证：`./scripts/run.ps1 -m pytest -q tests/unit` 639 passed；`tests/integration/test_wrs.py -k relative` 1 passed；相关文件 Ruff check 通过。未运行完整 Zenoh/WRS 集成组（registry 改动只经单元测试和上述在线复测）。

### 规划耗时与 token 数显示

`Runtime.snapshot()` 新增 `last_planning`：`total_s`（规划前快照加模型调用，Runtime 测量）、`model_s`（`planner.plan` 耗时，缓存命中为 None）以及服务商回报的 `input_tokens`/`output_tokens`/`reasoning_tokens`（`planner.token_counts` 统一 Chat 与 Responses/Claude 的名称，非整数记为 None）。每次规划开始时清空；被替换目标的迟到回复不写入。仅供显示，不参与超时或安全判断。09 viewer 新增“耗时”（识别＝松手到拿到结果、规划、执行＝观察到任务开始到终态，本地轮询计时，误差约一帧）和“模型输出”两行。

验证：单元测试 640 passed；`tests/integration/test_llm_runtime.py tests/integration/test_cache_runtime.py tests/integration/test_task_identity.py` 13 passed，`test_llm_runtime.py test_voice_text.py test_sync_api.py` 21 passed；Ruff check 通过；09 仅 `py_compile`，未在运行中的 07 上复测（UNVERIFIED）。

### CLARIFY / ANSWER 精简

此前只有 execute 要求 `text` 留空、`speak` 要求约 20 字以内，澄清和回答没有长度约束（仅 `PlanDecision.text` 2048 字符硬上限，超出即校验失败而非截断）。系统提示新增：回答与澄清用用户的语言、一到两句短句，澄清只问一个能消除歧义的问题。只是提示，不是硬性保证。验证：`tests/unit/test_llm.py` 166 passed，Ruff 通过；在线效果未测（UNVERIFIED）。

### 09 新目标先停下上一条

此前上一条未结束时 09 直接拒绝新目标，要求先说“停止”。现在 09 自动经 Voice 控制通道发“停止”，确认上一条停下（任务终态、规划不在 WAITING）后提交新目标；5 秒内未停下或变为 UNKNOWN 则放弃新目标。只保留最新一句，说“停止”或按“立即停止”会丢弃等待中的目标。Runtime 仍不做任务替换，策略留在客户端。

Runtime 改动：`interrupt()` 遇到 WAITING 规划时取消该规划协程。WAITING 表示这次调用尚未启动任务，只丢弃模型请求（其回复本来也会被判过期）；此前协程要等模型返回，期间 `goal()` 一直报 `planner_unavailable_or_busy`。新增集成测试 `test_stop_drops_pending_model_call_so_the_next_goal_is_accepted_at_once`，去掉该改动时以 `planner_unavailable_or_busy` 失败；`test_interrupt_planning_without_task_rejects_late_result` 改为断言规划协程已取消。

验证：单元测试 640 passed；`./scripts/run.ps1 -m pytest tests/integration/test_system.py test_task_identity.py test_voice_text.py test_qwen_voice_wrs.py` 22 passed；Ruff 通过。09 仅 `py_compile`，未在运行中的 07 上手动复测（UNVERIFIED）。


## 2026-09-25：Mock 示例清理

保留机器人、TTS、ASR 和模型四类替身，用于离线调度、取消、资源隔离、故障和迟到响应回归。
本轮不修改运行时后端、默认启动值或通信 API；WRS 虚拟 IK/FK 仍保留。

删除重复示例 `examples/beginner/03_cancel_action.py`、`examples/tasks/05_goal.py`、
`examples/voice/04_text_goal.py`。原 `examples/connect` 两份脚本移到 `tests/fixtures/connect/`，
仍验证独立进程连接、共享口令与客户端退出后服务存活；原 `examples/models/fixtures` 的三份
协议响应和说明移到 `tests/fixtures/models/`，内容逐份与 HEAD 核对一致。
保留并注明 Mock 播报的 tasks/02、voice/02，更新示例目录、测试引用和当前文档。
用户原有 sync.py/system.py 修改以 SHA-256 核对未变。

实际运行命令（仓库根目录）：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py scripts/verify.py --wrs
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/mock_cleanup_lint.py
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 reports/mock_cleanup_review.py
git diff --check
```

结果：单元 639 passed；Zenoh 74 passed / 13 deselected；WRS 13 passed / 74 deselected。
两次集成分组互补，合计 726 passed、0 failed/errors/skipped；deselected 不是跳过或通过。
19 个有限 WRS 示例全部符合预期输出，doctor/WRS 模型探针通过；示例目录校验通过，
本次更新文档的 124 个本地链接有效，git diff --check 通过。

完整 verify 退出码为 1：全仓库 Ruff 存在三条原有告警（不是全绿）。
`examples/models/01_plan.py:4` 未使用 time（F401）；`wrs_agent/env/ur_rtde.py:214`
超长行（E501）；`wrs_agent/nodes/actions.py:82` async 函数 timeout 参数（ASYNC109）。
这三份文件均与 HEAD 一致，并用 `git show HEAD:<path>` 的原始内容逐个经
`ruff check --stdin-filename <path> -` 复现。当前变更及新增的 15 份 Python 文件静态检查通过；
为遵循最小修改原则，本轮不混入这些既有告警的修复。

环境：Python 3.12.0、eclipse-zenoh/zenohd 1.9.0、Pydantic 2.13.5、pytest 9.1.1、
pytest-asyncio 1.4.0、Ruff 0.16.8、httpx 0.28.1；WRS 固定提交
`7815e6f110fd161fe3c3b7e6f978e5393c3cf502`，子模块工作区干净。
未启动真实音频设备、付费模型或物理硬件；这些能力仍为 UNVERIFIED。
证据：`reports/mock_cleanup_summary.json`、`mock_cleanup_lint.json`、`mock_cleanup_review.json`，
以及 `unit.xml`、`zenoh.xml`、`wrs.xml`、`acceptance.json` 和对应输出。
下一阶段入口仍为启动配置设计；本轮未实现 Blueprint 或流式模型执行。

## 2026-09-25：wrs_agent 职责整理

内置技能合同迁入 `skills/contracts.py`，唯一注册表与检索/校验在 `skills/catalog.py`；
`skills/__init__.py` 只导出公共名称。内置条目不绑定后端 handler，执行节点显式绑定；
未绑定条目在打开日志前被拒绝。参数模型、合同描述、版本与缓存指纹输入经 AST 对照保持一致。

机器人 Mock 状态、动作、模拟延迟和故障在 `env/mock.py`；Mock TTS 与脚本式捕获统一在
`speech/mock.py`，播报状态在 `speech/state.py`。共用执行器 `actions.py` 改名
`executor.py`，不再承担模拟延迟/故障；`nodes/actions.py` 改名 `nodes/action_rpc.py`，
`nodes/tts.py` 移除。新增 `plan_validation.py` 与 `runtime_state.py`；Runtime 仍拥有
全部调度/取消控制流程，执行和规划状态归拢为普通数据对象。源码职责与内部导入迁移见
[开发交接](DEVELOPMENT.md#源码职责与阅读入口)。

保留工作区原有示例清理及用户代码：sync.py 与基线逐字节相同，system.py 仅更换动作
客户端导入路径。未修改消息 schemas.py、依赖清单、锁文件或 WRS gitlink。
修复三条原有 Ruff 告警：未用 time 导入、UR 驱动超长表达式和 RPC timeout 局部抑制；
没有更改硬件驱动行为。内部 ActionExecutor 的 duration/fault 参数已移除，后端负责模拟。

实际命令（仓库根目录，固定 Python 3.12.0）：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py scripts/verify.py --wrs
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit --junitxml=reports/structure_unit.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/structure_review.py
git -c core.safecrlf=false diff --check
```

最终分项结果：单元 **641 passed**；Zenoh **74 passed / 13 deselected**；
WRS 虚拟环境 **13 passed / 74 deselected**；合计 **728 passed、0 failed/errors/skipped**。
deselected 是互补测试分组，不计为跳过。19 个有限示例全部符合预期；
Ruff、doctor/WRS 探针及结构审查通过。

首轮定向检查 138 passed / 2 failed：停止故障用例只等 RUNNING，而 handler 尚未进入；
改为等待 Mock started，保留 UNKNOWN 和拒绝后续任务的断言，并新增“日志等待中取消，
handler 不执行且可确认停止”的回归。4 项停止专项随后通过。
完整 verify 首轮为 640 passed / 1 failed（版本漂移夹具丢失 handler），因此该命令退出 1；
修正夹具后全部单元重跑得到上述 641 passed。其余集成、WRS、示例沿用同轮真实通过结果，
未修改它们已验收的实现，也未把原失败报告覆盖成成功。新增另一项回归验证未绑定合同拒绝。

最终汇总为 `reports/structure_verification.json`；原完整检查为
`reports/structure_verify_initial.json`；初始失败输出在 `structure_focused_initial.txt/xml`
和 `structure_unit_initial.txt/xml`，修复后单元在 `structure_unit.txt/xml`。
结构审查脚本与结果为 `structure_review.py/json`，修改前快照为 `structure_before/`。

依赖：eclipse-zenoh/zenohd 1.9.0、Pydantic 2.13.5、pytest 9.1.1、
pytest-asyncio 1.4.0、Ruff 0.16.8、httpx 0.28.1。
WRS 提交 `7815e6f110fd161fe3c3b7e6f978e5393c3cf502`，子模块工作区干净。
付费模型、真实麦克风/播放、物理硬件、双机和性能基准均未验证；本轮没有相应调用，
没有阻塞的软件验收项。未 commit/push。下一阶段入口仍为 [节点启动配置](NODE_LAUNCH.md)。

## 2026-09-25：技能按能力归属

机器人参数与七项合同归 `skills/robot/definitions.py`，播报参数与合同归
`skills/speech/definitions.py`；各自引用相邻 SKILL.md。`contracts.py` 只含
SkillSpec/Skill，`catalog.py` 显式汇总、检索、校验。后端直接导入本域合同并绑定
handler，具体参数不再从 skills 根包导出。参考固定 RPent、DimOS、HoloAgent 源码，
证据和取舍见 SOURCES 的 S25；维护方法见技能 README 和 DEVELOPMENT。

8 项合同的完整序列化数据及 Schema 与本轮基线完全一致；参数类、共用类型、
检索/校验逻辑经 AST 对比一致，后端仅改导入。版本漂移夹具
`tests/fixtures/versioned_tts.py` 改为修改语音所属合同，使重启节点确实声明版本 2；
原“版本漂移后拒绝计划并保留可查询错误”的断言不变。

**本轮验证范围是稳定副本。** 工作期间另一个 Node 生命周期改动修改了入口与导入链，
一度引用尚未存在的 instance_lock.py。主工作区首轮单元 641 passed；
集成 17 passed / 70 failed（包含上述版本夹具问题），原始报告保留，
不将这份混合版本结果记作通过，也不覆盖并行修改。

以 `reports/skill_boundary_before` 为基线，在 `reports/skill_boundary_validation`
叠加本轮技能和夹具改动；快照清单在 `skill_boundary_isolation.json`。
依赖与 WRS 使用同一本地固定版本，未复制凭据、未安装参考框架。
稳定副本中的真实命令如下（固定解释器，工作目录为该副本）：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit tests/integration -m 'not live_model and not audio_live and not hardware' --junitxml=reports/skill_boundary_isolated_tests.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_developer_examples.py::test_custom_node_and_skill_run_from_another_working_directory tests/integration/test_error_protocol.py::test_incompatible_restarted_node_blocks_plan_and_retains_error_for_reconnect tests/integration/test_wrs.py::test_real_wrs_progress_query_completion_and_unsupported --junitxml=reports/skill_boundary_recheck.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -m build --wheel --no-isolation --outdir reports/wheel
```

稳定副本首轮为 725 passed / 3 failed：隔离副本漏拷 router.json5、版本夹具引用旧目录，
以及把单元/集成放在一个 pytest 进程，导致单元测试导入的 WRS 影响集成模块隔离断言。
补齐副本资源、更新夹具后，在新 pytest 进程定向重跑这三项，结果 **3 passed**。
未修改测试断言。合并去重后的分项结果为 **641 单元 + 87 集成 = 728 passed，
0 failed/errors/skipped**；这是初轮与定向复测的汇总，不是首轮命令全绿。

技能目录 `examples/beginner/02_skills.py` 与 WRS/播报并行
`examples/tasks/02_parallel.py` 两个示例的预期输出均通过；
`reports/skill_boundary_examples.py` 记录实际命令、工作目录与输出。
Ruff 通过；wheel 成功构建，直接从 wheel 导入的两份说明和全部合同与源码一致。
30 项合同/结构/导入/链接审查通过，包含副本技能代码与主工作区逐字节相同。
根目录复现审计：固定 Python 以 `-X utf8 -S
reports/skill_boundary_validation/scripts/run.py reports/skill_boundary_review.py` 运行。

汇总 `reports/skill_boundary_verification.json`；原始报告分别为
`skill_boundary_unit.*`、`skill_boundary_integration.*`、
`skill_boundary_isolated_tests.*` 和 `skill_boundary_recheck.*`。
打包与依赖证据在 `skill_boundary_package.json`、`skill_boundary_environment.json`。
Python 3.12.0、eclipse-zenoh/zenohd 1.9.0、Pydantic 2.13.5、pytest 9.1.1、
pytest-asyncio 1.4.0、Ruff 0.16.8、httpx 0.28.1；
WRS `7815e6f110fd161fe3c3b7e6f978e5393c3cf502`，子模块干净。

未运行付费模型、真实音频或物理硬件；没有这些项目的通过声明。
并行 Node 生命周期修改后的整仓验收仍由该改动完成，本记录不代表其已通过。
下一步是合并当前 Node 工作区后的整体验证；本轮技能调整不再有待实现项。未 commit/push。

## 2026-09-25：统一 Node 生命周期

在当前共享工作区引入一层 Node：五类内置节点直接继承，后端/Runtime/Planner 通过组合使用。
Node 统一 setup/teardown、连接复用、动作注册、后台任务与逆序清理；构造只校验配置。
各节点独立 options，经相同合同在进程两侧校验；LocalStack 同时启动已启用节点并分别等待初始化。
ready 继续表达动作准入，HELD 不会因启动成功被自动恢复。
自定义 speaker 使用 custom；两个 custom 节点可在同一后缀下独立查询、发现和退出。

高层 launch/connect/step 和 CLI 常用参数保留。Python serve_node 的后端参数改放 options，
action_factory 改为 Node.setup 内调用 self.actions；节点退出路径统一为
`request/node/<node_id>/shutdown`。动作、任务、停止、epoch 和 UNKNOWN 语义不变。
实现入口与迁移例子见 [Node 指南](NODE_LAUNCH.md)。

真实命令（仓库根目录，固定解释器）：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py scripts/verify.py --wrs
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit --junitxml=reports/node_unit_final.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration -m wrs --junitxml=reports/node_wrs_final.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 reports/node_review.py
git -c core.safecrlf=false diff --check
```

最终分项结果为 **652 单元 + 75 Zenoh + 13 WRS = 740 passed，0 failed/errors/skipped**；
分组中的 deselected 是互补选择，不计为跳过。19 个有限示例全部符合预期；
Ruff、doctor/WRS 虚拟探针和补丁格式检查通过。新增回归覆盖构造无副作用、初始化失败、
取消、清理异常、后台任务失败、启动并发/失败清理、HELD、custom 身份与独立退出。
27 项结构与文档审查通过，核实 Runtime/执行器/动作 RPC/Transport/同步包装业务 AST
相对本轮基线未变，实例锁仅迁移，消息 Schema 只增加 custom。

**首轮 verify 命令退出 1，以上是复测后的汇总。** 首轮单元为 651 passed / 1 failed：
同步入口仍匹配旧 unsupported_backend 文案，改为检查配置字段 backend 的校验错误。
首轮 WRS 为 12 passed / 1 failed：旧测试等 Agent ready 就查询 WRS；
改为分别确认 Agent 与 WRS 初始化，允许重启 WRS 保持 HELD，并继续验证显式恢复。
随后完整重跑单元、WRS 分组和 Ruff，得到上述结果；Zenoh、doctor 和示例沿用同轮通过证据。
未修改业务实现来隐藏失败，未把首轮报告改成通过。

汇总为 `reports/node_verification.json`。首轮输出保留在
`node_initial_acceptance.json`、`node_initial_unit.txt/xml`、
`node_initial_wrs_runtime.txt` 和 `node_initial_wrs.xml`；
最终单元/WRS 报告为 `node_unit_final.txt/xml`、`node_wrs_final.txt/xml`。
首轮通过的 Zenoh、doctor、示例分别为 `zenoh.txt/xml`、`doctor.json` 和 `example_*.txt`。
审查脚本和报告为 `node_review.py/json`；本轮开始快照为 `node_before/`。
该验收直接针对共享工作区，包含并行完成的技能领域整理，不使用隔离副本代替最终验证。

环境：Python 3.12.0、eclipse-zenoh/zenohd 1.9.0、Pydantic 2.13.5、
pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8；
WRS `7815e6f110fd161fe3c3b7e6f978e5393c3cf502`，子模块工作区干净。
没有新增依赖或修改 lockfile。付费模型、真实麦克风/播放、物理硬件、双机和性能
仍为 UNVERIFIED；本轮没有相应调用，没有阻塞的软件验收项。未 commit/push。
后续入口是 Node 指南与 speaker 示例；模型流式执行不在本次 Node 重构范围。

## 2026-09-25：功能命名统一为 features

按用户选择，把 Node 的 capabilities 统一改为 features，同时迁移 NodeInfo.features、
SkillSpec.required_features、features()、FeatureSnapshot、features_extra 与 request/features。
节点、目录、Runtime、技能校验、缓存、后端、示例和测试使用同一名称。
功能标签、技能版本和动作参数不变；执行准入、控制、取消与 UNKNOWN 判断不变。
Python 旧名、节点目录/技能 spec 旧字段及旧查询路径不兼容，客户端与节点同批更新；
高层 launch/connect/step 保留。对照表见 [Node 指南](NODE_LAUNCH.md#功能名称迁移)。

真实命令：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py scripts/verify.py --wrs
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 reports/features_review.py
git -c core.safecrlf=false diff --check
```

完整 verify 本轮退出 **0**，**652 单元 + 75 Zenoh + 13 WRS = 740 passed，
0 failed/errors/skipped**；19 个有限示例通过，Ruff 与 doctor/WRS 虚拟探针通过。
deselected 为互补测试分组，不是跳过。没有新增仅验证命名的测试，沿用已有实际行为回归。
44 项审查通过：41 个 Python 文件的 AST 只含约定的名称替换与导入排序变化，
运行代码/测试/示例无旧名残留，相关文档链接及补丁格式有效。
最初静态检查发现三处类型改名引起的导入排序问题，自动修正后完整验收通过。

汇总 `reports/features_verification.json`；本轮原始测试、示例、环境和完整验收报告
独立保存在 `reports/features_evidence/`。审查为 `features_review.py/json`，
修改前文件为 `features_before/`，之前验收输出留在 `features_previous/`。
外部来源 URL 和历史验收名称保留原文；当前迁移文档明确列出旧名与新名。

Python 3.12.0、eclipse-zenoh/zenohd 1.9.0、Pydantic 2.13.5、
pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8；
WRS `7815e6f110fd161fe3c3b7e6f978e5393c3cf502`，子模块工作区干净。
没有新增依赖；付费模型、真实音频、物理硬件、双机与性能仍未验证，本轮没有相关调用。
没有阻塞的软件验收项，未 commit/push。开发入口为 Node 指南中的 features/skills/options。

## 2026-09-25：节点按职责归档，ASR/Voice 状态归属子类

五类内置节点统一为 `nodes/<role>/node.py`；具体配置归各包 options.py，
公共 options.py 只保留 Texts/Duration。launch_options 在具体 Node 声明，
builtin.node_options 统一转换现有高层参数，CLI/launch/options JSON 保持不变。
ASR 的采集、识别、Mock 分开；TTS 的动作后端、合成、播放、Mock、状态分开。
模型清单分别随节点打包，共用 nodes/model_assets.py 的校验与离线规则。
旧顶层 speech 包及 register_asr/register_voice 已移除，导入迁移见 [Node 指南](NODE_LAUNCH.md)。

AsrNode 自己持有按键会话、锁、结果及请求方法，teardown 等采集线程收尾；
VoiceNode 持有自己的去重记录和任务。Node 基类的生命周期足以支持这些行为。
Agent 的状态仍在 Runtime，纯消息适配放 agent/rpc.py；公共 Action 协议继续复用。
本轮只整理归属和方法绑定，不改变功能标签、消息字段/路径、授权、幂等、取消与 UNKNOWN。
WRS 参考文件与取舍见 SOURCES S27，未修改或复制 WRS 非平凡代码。

真实命令（仓库根目录）：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_asr_node.py tests/unit/test_speech.py tests/unit/test_node_lifecycle.py tests/unit/test_processes.py tests/unit/test_revision_regressions.py --junitxml=reports/nodes_layout_focused.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_asr_node.py tests/unit/test_node_lifecycle.py --junitxml=reports/nodes_layout_lifecycle.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py scripts/verify.py --wrs
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 reports/nodes_layout_review.py
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -m build --wheel --no-isolation --outdir reports/nodes_layout_wheel
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/nodes_layout_package.py
git -c core.safecrlf=false diff --check
```

定向检查先得到 **144 passed**；新增 ASR 在关闭/取消时等待真实工作线程退出、
以及多个实例不共享按键/文本的 3 项回归后，生命周期专项 **22 passed**。
完整 verify 本轮退出 **0**，**655 单元 + 75 Zenoh + 13 WRS = 743 passed，
0 failed/errors/skipped**；19 个有限示例通过，Ruff、doctor/WRS 虚拟探针通过。
deselected 是互补分组，不计为跳过。初始导入排序和行宽问题在完整验收前修正。

59 项审查通过：13 项后端/适配声明与 4 个配置模型的 AST 不变，
ASR/Voice 原闭包函数与新方法对比只有 self 绑定及 nonlocal 去除；
协议、Runtime、执行器、Transport、Environment 和技能合同逐字节不变。
模型文件清单、默认模型目录与语音解释器目录保持原样，无旧运行入口残留，文档链接有效。

wheel 构建成功，直接从 wheel 导入节点与 Qwen 类型通过；
两份清单与原 ASR 9 项/TTS 12 项文件元数据逐项一致，wheel 不包含旧 speech 包，
两份 Skill 说明齐全。导入未加载 Torch、sounddevice 或 Qwen SDK。
现有构建环境报告 setuptools/wheel 兼容性警告但退出 0，没有安装或升级构建依赖；
该验证覆盖包源码与资源，不代表完整安装部署或真实模型已经验证。

汇总 `reports/nodes_layout_verification.json`，本轮完整原始报告在
`reports/nodes_layout_evidence/`；结构审查 `nodes_layout_review.py/json`，
包资源审查 `nodes_layout_package.py/json`，修改前文件 `nodes_layout_before/`。
构建 wheel 在 `nodes_layout_wheel/`，中间构建产物已归档 `nodes_layout_build/`，
没有将构建目录留在源码根目录。

Python 3.12.0、eclipse-zenoh/zenohd 1.9.0、Pydantic 2.13.5、
pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8；
WRS `7815e6f110fd161fe3c3b7e6f978e5393c3cf502`，子模块干净。
pyproject 仅更改资源打包路径，uv.lock 和运行依赖不变。
未下载/加载真实模型，未录音/播放，未调用付费模型或物理硬件；
这些以及双机/性能仍为 UNVERIFIED。没有阻塞的软件验收项，未 commit/push。
后续从 Node 指南及各节点 node.py/options.py 进入；替换后端保留既有会话和控制语义。

## 2026-09-25：节点配置同文件与单元测试去重

ASR、TTS、Agent、WRS 的 Options 类已移到各自 node.py 的 Node 类前；
四个局部 options.py 删除，共用 nodes/options.py 只保留 Texts/Duration 约束。
字段、默认值、校验、启动映射和业务行为保持不变。NODE_LAUNCH 与 DEVELOPMENT 同步更新。

测试精简按覆盖关系判断，不按 Mock 名字判断：

| 删除的重复用例 | 保留覆盖与处理 |
|---|---|
| test_skills.py::test_unbound_catalog_contract_cannot_open_an_executor | 并入 test_invalid_registration_rejected_before_opening_journal 的 handler 分支，直接使用目录原项，仍验证拒绝且不创建日志 |
| test_asr_node.py::test_close_ends_capture_without_dispatching_it | 并入 test_node_lifecycle.py 的 ASR 退出用例；正常关闭和取消两分支均验证线程退出后才关闭连接，且不向 Voice 发请求 |
| test_processes.py::test_removed_wrs_backend_name_fails_before_process_start | 同步 launch 的非法 backend 用例已覆盖 LocalStack 验证；CLI 和 serve_node 继续保留 wrs_virtual 拒绝用例 |

使用 Mock 验证停止确认、UNKNOWN、幂等、迟到模型/音频结果、故障恢复的测试继续保留。

本轮实际运行：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py scripts/verify.py --wrs
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 reports/node_simplify_review.py
git diff --check
```

完整 verify 退出 0，**652 单元 + 75 Zenoh + 13 WRS = 740 passed**，
**0 failed/errors/skipped**；19 个有限示例、Ruff、doctor/WRS 虚拟探针通过。
deselected 为互补分组，不计为跳过。导入合并后的 4 处排序问题经 Ruff 修正，完整验收无失败。
23 项结构审查确认 Options 字段/校验与 Node 业务 AST 不变、旧配置导入全部迁移；
用例清单从 655 变为 652，差集恰好是上述三个重复用例。
git diff --check 退出 0，仅报告仓库既有 CRLF 转换提示。

独立证据 reports/node_simplify_evidence/，汇总 node_simplify_verification.json，
删除依据与审查 node_simplify_review.json，修改前快照 node_simplify_before/。
本轮未改依赖：Python 3.12.0、eclipse-zenoh/zenohd 1.9.0、Pydantic 2.13.5、
pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8；
WRS 固定提交 7815e6f110fd161fe3c3b7e6f978e5393c3cf502。
付费模型、真实音频、物理机器人、双机和性能仍 UNVERIFIED。
没有阻塞的软件验收项。未 commit/push；下一开发入口为 NODE_LAUNCH.md 和各节点 node.py。


## 2026-09-25：移除 Agent MockClient

AgentNode 只在 live_model=True 时创建 LLMClient/ModelPlanner；默认仍执行显式计划，
自然语言目标返回 planner_unavailable_or_busy。同步 launch、System.launch、LocalStack 与
CLI --live-model 使用同一个开关。provider/model_provider、deferred/deferred_planner 及测试释放 RPC 已删除。
模型单元测试使用 LLMClient + HTTP 响应夹具，跨进程测试连接测试自有回环 HTTP 服务。

删除只验证 Mock 关键词规则、替身客户端切换及重复非法提案的用例；缺失 plan 的校验
并入三个协议的现有测试，取消、UNKNOWN、幂等、迟到输出、缓存和身份回归保留。
改动涉及 nodes/agent/node.py、processes.py、__main__.py、system.py、sync.py，
删除 planner/providers/mock.py；测试夹具在 tests/llm_fixtures.py，在线语音示例和启动指南同步修改。

实际命令（固定解释器 D:\code\venv312\.venv\Scripts\python.exe）：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py scripts/verify.py --wrs
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_asr.py --junitxml=reports/agent_llm_asr_recheck.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m ruff check tests/integration/test_asr.py
```

完整命令首轮退出 1：647 单元、13 WRS 虚拟、19 个有限示例、Ruff、doctor 均通过；
Zenoh 75 passed/1 failed。遗漏的是 ASR 识别文本提交规划的测试，添加显式回环模型配置后，
该文件补跑 2 passed，补改后的 Ruff 也通过。原始失败不抹去，按测试标识合并最新结果为
736 passed、0 failed/errors/skipped，无软件阻塞。相关定向单元另有 305 passed。

Python 3.12.0；httpx 0.28.1、eclipse-zenoh 1.9.0、Pydantic 2.13.5、pytest 9.1.1、
pytest-asyncio 1.4.0、Ruff 0.16.8。默认 AgentNode 导入不加载 httpx，保留 core 可独立运行。
本轮不改依赖锁或 WRS 提交。未验证云端推理、真实音频、实机、双机或性能。

独立汇总 reports/agent_llm_verification.json，原始命令输出/XML 在 reports/agent_llm_evidence/，
删除依据 reports/agent_llm_review.json。原有修改及并行语音文件合并保持，无 commit/push。
下一入口为 [节点启动指南](NODE_LAUNCH.md)：配置 LLM_*，显式启用 live_model 即接入真实服务。

## 2026-09-25：ASR/TTS 小文件合并与 Voice 职责说明

VoiceNode 是识别/键入文本的意图分流与控制入口，补充其职责和线程/接口说明；
普通目标送 Agent，明确停止走独立控制通道，仅停止播报直接取消 TTS，重复输入保持原目标。
ASR 普通结果交还调用方，明确停止才直接转发 Voice。

| 合并前 | 合并后 | 当前行数 |
|---|---|---|
| asr/capture.py + mock.py | asr/capture.py：麦克风录音、脚本文字采集 | 92 |
| tts/backend.py + state.py + playback.py + mock.py | tts/backend.py：状态、声卡输出、合成/播放协调、离线播报 | 185 |

ASR/TTS 各保留三个实现文件（node、capture/backend、qwen），包入口与 assets.json 原样保留。
两目录普通文件从 14 减至 10。配置、Node 生命周期、模型适配保持独立；
没有新增继承、抽象层、依赖或兼容空壳。旧模块导入迁移到 capture/backend，同步示例和开发指南。
本轮只迁移测试导入，未增删用例；测试总量包含工作区同时进行的 Agent 模型清理。

实际运行命令：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_speech.py tests/unit/test_asr_node.py tests/unit/test_node_lifecycle.py tests/unit/test_node_state.py --junitxml=reports/speech_compact_focused.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/speech_compact_verify.py --wrs
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/speech_compact_rerun_zenoh.py
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/speech_compact_review.py
git -c core.safecrlf=false diff --check
```

完整验收脚本复制自 scripts/verify.py，只改报告输出路径，检查内容未改。
首轮总体退出 1，唯一失败为 ASR 普通目标提交得到 planner_unavailable_or_busy：
工作区删除了默认 MockClient，该集成用例尚未配置 Planner。
另一组改动已为该用例补齐现有 llm_server 与 live_model=True，原断言不变。
通信分组重新运行退出 0，最终检查状态见 acceptance_final.json；
acceptance.json 和 zenoh_initial_failed.txt/xml 保留首轮失败，zenoh_rerun.json 记录重跑。
相关 **64 passed**，完整 **647 单元 + 76 Zenoh + 13 WRS = 736 passed**，**0 failed/errors/skipped**；
**19 个有限示例、Ruff、doctor/WRS 虚拟探针通过**。deselected 是互补分组。
33 项审查通过：移动前后声明的行为 AST 不变，包入口/清单逐字节不变，旧导入无残留，
导入语音模块不加载 numpy、sounddevice、Torch 或 Qwen SDK。没有调用真实音频/模型。

本轮原始证据 reports/speech_compact_evidence/，汇总 speech_compact_verification.json；
结构审查 speech_compact_review.py/json，修改前文件 speech_compact_before/。
实际环境：Python 3.12.0、eclipse-zenoh/zenohd 1.9.0、
Pydantic 2.13.5、pytest 9.1.1、
pytest-asyncio 1.4.0、Ruff 0.16.8；
WRS 7815e6f110fd161fe3c3b7e6f978e5393c3cf502。依赖与锁文件未改。
真实音频、付费模型、物理硬件、双机及性能仍 UNVERIFIED；无阻塞的软件验收项。
未 commit/push；后续从 NODE_LAUNCH.md 的职责/目录说明和各节点 node.py 进入。

## 2026-09-25：Runtime 状态合并与小模块审查

TaskExecution、ExecutionState、PlanningState 移入 runtime.py 的 Runtime 类前；
runtime_state.py 删除，原字段、创建/复制方法及独立 dataclass 保留。
包含技能发现新增的 bindings/specs/boots。开发目录说明同步更新。
本轮不修改其他候选、不增删测试；62 个 Python 模块的合并取舍见 DEVELOPMENT.md。

合并前后四个类 AST 相同；首次记录 runtime.py 从 906 到 957 行。
随后并行技能发现工作更新 _fence, enqueue, goal, start，
本轮保留这些后续改动，未将它们归作文件合并。合并时证据与当前审查分别保留。

实际命令：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_runtime.py tests/unit/test_task_cancel.py tests/unit/test_revision_regressions.py --junitxml=reports/runtime_merge_focused_latest.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/runtime_merge_verify.py --wrs
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/runtime_merge_lint_recheck.py
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 reports/runtime_merge_review.py
git -c core.safecrlf=false diff --check
```

runtime_merge_verify.py 与 scripts/verify.py 仅报告路径不同，避免覆盖其他工作的证据。
**完整验收退出 1，未全绿**：单元 **650 passed**，
Zenoh **74 passed / 3 failed**，
WRS 虚拟 **13 passed**，各组 **0 errors/skipped**；
19 个有限示例及 doctor/WRS 探针通过。deselected 为互补分组。
Runtime 专项初次和当前复验均 **34 passed**，本轮 Runtime 静态检查通过。
首次整仓 Ruff 的 17 项导入/行宽问题来自正在迁移的技能发现文件；并行工作更新后，
**整仓 Ruff 复验通过**，首轮失败未覆盖或改写。

剩余失败均在 tests/integration/test_system.py：

| 用例 | 本轮观察到的失败 |
|---|---|
| test_runtime_cancel_has_no_normal_feature_query | 在 start 前禁用 features，当前发现阶段缺失合同，返回 provider_not_found，尚未进入取消 |
| test_unready_provider_prevents_partial_task_effects | start 已返回 provider_not_found；旧用例期望先取得任务句柄再得到 FAILED |
| test_configuration_cannot_grant_an_unregistered_node_skill | 旧用例假定默认配置存在 pick = wrs；当前默认路由已改为发现 |

这些是并行技能发现改造尚待处理的全仓验收项。本轮不回退其接口、不删除用例，
也不把单纯文件合并描述为全仓行为已通过。后续在该改造中修复并重跑相关集成。
合并本身与小模块审查已完成；完整集成验证尚有上述失败。

汇总 reports/runtime_merge_verification.json，原始报告 runtime_merge_evidence/；
runtime_merge_review_at_merge.json 保存四类初始 AST 一致证据，
runtime_merge_review.json 记录当前状态类与后续方法差异，runtime_merge_inventory.json 保存盘点。
Runtime 修改前文件在 runtime_merge_before/。

实际环境：Python 3.12.0、Zenoh/zenohd 1.9.0、
Pydantic 2.13.5、pytest 9.1.1、
pytest-asyncio 1.4.0、Ruff 0.16.8；
WRS 7815e6f110fd161fe3c3b7e6f978e5393c3cf502。本轮依赖、锁文件及硬件边界未改。
付费模型、真实音频、物理设备、跨机与性能仍 UNVERIFIED。未 commit/push。


## 2026-09-25：技能单入口、发现与显式动态追加

后端绑定表是执行与发现的唯一来源；删除跨进程 register_greet/中央 SKILLS。
节点部署 TOML 保留，普通配置不再列出逐技能路由。新增 Node.add_skills(*skills)，
与构造时的追加逻辑统一；批次原子、同名拒绝、元数据复制、线程与关闭边界已验证。
不实现替换、卸载、自动安装或代码热加载。

完整合同通过既有 request/features 发布，skill_revision 使追加后目录缓存失效。
独立 Agent/客户端无需导入具体技能；超过八个技能仍完整列出。显式/发现版本统一解析，
同名提供者报歧义，指定提供者离线不转移旧任务。暂停节点可查询合同但不能执行。
纯批量参数预检覆盖 JSON Schema 无法表达的 Python 跨字段校验，任一步不合法则零动作。
动态追加期间已开始的任务仍使用原节点和合同；原取消、UNKNOWN、幂等和重启回归保留。

最终：**651 单元 + 77 Zenoh + 13 WRS = 741 passed**，**0 未解决 failed/errors/skipped**；
**19 个有限示例、Ruff、doctor/WRS 虚拟探针、diff --check 通过**。
完整运行使用固定解释器，经 scripts/run.py 执行 reports/skill_discovery_verify.py --wrs；
该脚本与 scripts/verify.py 检查内容相同，只改变报告路径。
首轮整体退出 1，仅新导入测试使用不支持的 run.py -c；修正测试入口后完整单元复跑 651 passed。
原始失败不覆盖，最终验收见 reports/skill_discovery_evidence/acceptance_final.json。
精确命令、依赖版本、修改文件与中间失败记录见 reports/skill_discovery_verification.json。

Python 3.12.0、Zenoh 1.9.0、Pydantic 2.13.5、pytest 9.1.1、pytest-asyncio 1.4.0、
Ruff 0.16.8；WRS 7815e6f110fd161fe3c3b7e6f978e5393c3cf502。
未运行付费模型、真实音频、实机、跨机或性能；无软件阻塞，未 commit/push。
下一开发入口：[技能库](../wrs_agent/skills/README.md) 与 examples/nodes/。

## 2026-09-25：内置节点入口合并

nodes/builtin.py 已删除；列表 NODES、参数映射 node_options 与 serve_node 集中到
nodes/serve.py（38 行）。CLI 与 LocalStack 更新导入，DEVELOPMENT/NODE_LAUNCH 更新说明。
三项定义的 AST 与合并前一致，两个调用方除导入外 AST 不变；未增删测试。

实际命令（PowerShell，以下各项均通过）：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit tests/integration/test_custom_nodes.py tests/integration/test_nodes.py tests/integration/test_developer_examples.py tests/integration/test_connect.py tests/integration/test_sync_api.py --junitxml=reports/node_entry_merge_tests.xml
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py -m wrs_agent --help
```

651 个单元测试、20 个相关集成测试通过，0 failed/errors/skipped，耗时 48.38 秒；
集成项包含独立进程节点/客户端示例与自定义节点示例。Ruff 和 CLI 帮助入口通过。
环境：Python 3.12.0、eclipse-zenoh 1.9.0、
Pydantic 2.13.5、pytest 9.1.1、
pytest-asyncio 1.4.0、Ruff 0.16.8；依赖未修改。
证据 reports/node_entry_merge_tests.xml/.txt、node_entry_merge_lint.txt、
node_entry_merge_cli.txt；汇总 node_entry_merge_verification.json，
结构核对 node_entry_merge_structure.json，修改前备份 node_entry_merge_before/。
本轮未重跑全量集成或 WRS 专项，不覆盖前一轮其余集成失败的状态；
未调用付费模型、真实音频或物理设备。其他小模块候选仍仅保留在 DEVELOPMENT.md 中。


## 2026-09-25：部署清单与动态节点发现分离（完成）

launch/LocalStack 读取部署 TOML、传入各节点自身参数，并只清理自己启动的进程。
Node/Agent 不再持有 definitions，System.connect 默认不读部署文件。
显式 bindings 入口仅做兼容转换，不是发现白名单；自定义节点可以晚加入。
同一 site/env 的节点自行发布地址与启动身份，Registry 是消费者内部目录，不新增中心进程。
目录核验实例和描述，离线/重启及时失效，重复实例和动作地址冲突拒绝调度。
Runtime 固定每个任务的提供者、客户端、合同与 boot_id，旧任务不会换到重启实例。
取消走已绑定控制通道，不等待普通目录查询；目录超时不阻断已绑定任务取消。
Voice/ASR 等待依赖可取消，启动期限统一由 launch 掌握；Qwen 全栈预算 300 秒，普通栈 10 秒。

实际命令：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/dynamic_nodes_complete_verify.py --wrs
git -c core.safecrlf=false diff --check
```

验收脚本与 scripts/verify.py 的检查内容相同，只改独立报告路径和父目录创建。
**675 单元 + 90 Zenoh + 13 WRS 虚拟 = 778 passed；0 failed/errors/skipped。**
**20 个有限示例、Ruff、doctor/WRS 虚拟探针和 diff --check 均通过。**
覆盖新节点晚加入、退出/重启、旧任务实例隔离、重复身份/动作地址冲突、通信域隔离、
鉴权错误保持、关闭与刷新交错、目录失败后的取消，以及依赖晚于原 10 秒上线。
慢启动测试使用 Mock TTS 和真实 Zenoh，未加载 Qwen 模型。
新增可运行示例 examples/nodes/06_late_node.py，展示 Agent 启动后发现部署文件外的节点。

迁移期间的静态占位假设、鉴权错误误报及首次描述超时已修复；原始失败证据保留。
另一次合并 unit/integration 进程的试跑为 770 passed / 1 failed，原因是 unit 导入 WRS
污染客户端未导入 WRS 的 sys.modules 断言；最终采用仓库标准分进程验证，该断言保留并通过。
最终证据 reports/dynamic_nodes_evidence/complete/；
精确命令、环境、修改范围和历史失败见 reports/dynamic_nodes_verification.json。

Python 3.12.0、Zenoh 1.9.0、
Pydantic 2.13.5、pytest 9.1.1、
pytest-asyncio 1.4.0、Ruff 0.16.8；
WRS 7815e6f110fd161fe3c3b7e6f978e5393c3cf502，工作树干净。本轮未改依赖/锁文件。
付费模型、真实音频、实机、跨机和性能仍 UNVERIFIED；无软件阻塞，未 commit/push。
下一入口 docs/NODE_LAUNCH.md 与 examples/nodes/06_late_node.py。


## 2026-09-28：节点与技能重构提交前验收

审查并收齐 Node 生命周期、launch/Registry 分离、Runtime 任务状态整理、技能单入口、
MockClient 移除及相应调用方、测试、示例和包资源迁移。模型测试使用真实 LLMClient，
通过隔离的 HTTP 响应夹具验证，不产生付费模型请求。

Skill 直接接收参数模型与可选 handler，自动生成 SkillSpec；多后端共享合同保留 bind。
删除 required_features 与未实现的 interrupt_mode；前置条件和 verification 字符串仅为说明。
Registry 回收离线描述并使能力缓存失效；超额声明仍参与地址冲突检查，容量拒绝不影响
无冲突的已接纳节点。已绑定控制连接独立于描述缓存；Voice 在各依赖就绪时立即保存连接。
旧任务保留原客户端、合同和启动身份，目录更新不会把旧动作转交给新实例。

实际运行命令：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py reports/commit_review_verify.py --wrs
git -c core.safecrlf=false diff --cached --check
```

本地验收脚本与 scripts/verify.py 的检查内容一致，仅将报告写入独立目录并创建父目录。
在其他 checkout 可用 `python -X utf8 -S scripts/run.py scripts/verify.py --wrs` 复现。
**684 单元 + 91 Zenoh + 13 WRS 虚拟 = 788 passed，0 failed/errors/skipped。**
**20 个有限示例、Ruff、doctor/WRS 虚拟探针与暂存差异检查均通过。**
测试期间源码未变化；验收后只补充本记录。完整命令、输出、版本与审查清单分别保存在
reports/commit_review_evidence/ 和 reports/commit_review_inventory.json，本地生成报告不提交。

上一轮技能精简验收曾发现占位描述、容量错误分类、离线能力缓存和 Voice 启动竞态问题，
这些问题已修复；首轮失败输出保留在 reports/skill_simplify_evidence/complete/。
随后一次验收中断，仅有单元结果；本次重新完成全部分组，未放宽测试期限或删除失败断言。

Python 3.12.0、Zenoh/zenohd 1.9.0、Pydantic 2.13.5、pytest 9.1.1、
pytest-asyncio 1.4.0、Ruff 0.16.8；WRS 7815e6f110fd161fe3c3b7e6f978e5393c3cf502，
submodule 工作树干净。pyproject 只迁移包资源声明，依赖、锁文件及 WRS 指针未变。
付费模型、真实音频、物理设备、跨机和性能仍 UNVERIFIED。
目录描述、在线声明、保留连接和技能所有者的上限分别为 64、1024、128、4096。
节点与客户端需同步更新以使用调整后的接口与线协议。
下一入口：[技能编写](../wrs_agent/skills/README.md) 与 examples/nodes/06_late_node.py。

## 2026-10-13：Camera → YOLO → Runtime → TTS 与 WRS 相机面板

实现与入口见 [CAMERA.md](CAMERA.md)。新增 `drivers/cameras/`、`nodes/camera/`、
`nodes/vision/`、`skills/vision/` 和五份 `examples/camera/` 示例。
核心增加独立有界二进制查询、成功 ActionStatus.output、受限 Step.inputs；原 WRS
viewer 通过 adapter 的 HTTP 扩展加入相机面板，未修改 WRS submodule。

实际命令（固定解释器，离线模型/音频 fixture，真实 Zenoh 与 WRS 虚拟进程）：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py scripts/verify.py --wrs
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit -q --tb=short --junitxml=reports/camera_final_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts --output-format concise
& '.local/tools/bin/uv.exe' --cache-dir .local/uv-cache lock --check --offline --python 'D:/code/venv312/.venv/Scripts/python.exe'
git diff --ignore-submodules --check
```

统一验收退出 0，初次单元分组 704 passed。最终审查补上一例“页面端口占用时关闭预览
连接”，并重跑单元得到 **705 passed**；该修改后的页面/WRS 检查也在统一验收后续阶段
通过。最终覆盖 **705 单元 + 96 Zenoh + 14 WRS = 815 passed，0 failed/errors/skipped**。
按 marker 分组的 deselected 不计为 skip 或通过。**20 个有限示例、Ruff、doctor/WRS
虚拟探针、uv lock --check、git diff --check 均通过**。新增 camera 示例实际多进程运行
由 test_camera_examples 覆盖；03_start_yolo 仅执行 --help，模型适配器使用 fixture 验证。

浏览器本机检查实际命令为：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py .local/camera_visual_check.py
```

本机检查脚本使用独立临时 Chrome 配置、虚拟 UR7E 场景和合成相机；WebGPU 初始化成功，
RGB/Depth/可拖动点云均显示，断连后清除旧数据，捕获的页面 JavaScript 异常数 0。
检查并保留 `reports/camera-ui-{offline,rgb,depth,points,disconnected}.png` 和
`reports/camera-ui.json`。自动重连另由 test_camera_viewer 的独立子进程验证。
原始测试输出及 XML 在 reports/；`reports/camera_acceptance.json` 从上述真实报告汇总。
这些本机脚本/报告不提交；可复现的软件验收入口仍为 `scripts/verify.py --wrs`。

本轮首次检查发现并修正：发现协议遗漏 camera 类型、示例误用任务句柄 API、测试加载 WRS
污染父进程、Zenoh 1.9 废弃回复 QoS 参数和页面启动失败时连接清理。沙箱内 Python 3.12
socketpair 初始化受阻，离线网络测试改在获准的沙箱外执行；不以超时或跳过冒充通过。

实际相机测试依赖：Python 3.12.0，Zenoh/zenohd 1.9.0，Pydantic 2.13.5，NumPy 2.5.3，
Pillow 12.3.0，pyrealsense2 2.56.5.9235，pytest 9.1.1，pytest-asyncio 1.4.0，Ruff 0.16.8。
独立 WRS doctor 探针沿用现有科学环境 NumPy 1.26.4/SciPy 1.16.2/WGPU 0.32.0。
WRS 指针为 7815e6f110fd161fe3c3b7e6f978e5393c3cf502，工作树干净。
uv 工具实际解析并锁定 145 个包；YOLO 可选依赖已锁定，未安装/运行真实模型权重。

**UNVERIFIED**：D405 实机及拔插、真实 YOLO 推理/精度、有声 TTS、在线 Planner、实机运动、
跨机及性能基准。软件链路使用明确标识的合成帧/检测器 fixture/离线模型响应/Mock TTS。
未实现 SAM3、GraspNet 节点、掩码服务及相机到机器人 world 的标定融合。
下一阶段由操作者按 CAMERA.md 提供设备序列号、已核对版本/许可/SHA256 的本地权重及
音频环境，分别开展实机验证；不得将本次软件通过解释为物理抓取或实时性认证。

## 2026-10-13 后续：SAM3 / GraspNet 节点与 WRS 虚拟抓取

本节更新上节“未实现 SAM3/GraspNet”的历史状态。当前合同与限制见
[VISUAL_GRASP.md](VISUAL_GRASP.md)。用户确认 D405 固定桌边、暂无标定/模型环境/权重，
先完成节点与虚拟闭环。完整物理抓取目标仍未完成。

新增 nodes/segmentation、nodes/grasp、有限任务数据缓存、skills/vision/grasping，
以及 env/fixed_camera.py、env/wrs_grasp.py。WrsNode 显式选择 wrs-grasp 后端；
普通 WRS/UR 后端未改变。核心仅补充已接受动作身份读取，沿用现有 Step.inputs。

真实运行命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py scripts/verify.py --wrs
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_grasp_perception.py tests/unit/test_fixed_camera.py tests/integration/test_grasp_pipeline.py tests/integration/test_visual_grasp.py -q --tb=short --junitxml=reports/grasp_final.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py examples/grasping/00_virtual_grasp.py
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py examples/grasping/01_start_sam3.py --help
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py examples/grasping/02_start_graspnet.py --help
git -c core.safecrlf=false diff --ignore-submodules --check
```

最终统一验收退出 0：**720 单元 + 97 Zenoh + 17 WRS = 834 passed**，
0 failed/errors/skipped；**21 个有限例子、Ruff、doctor/WRS 探针通过**。
19 项针对性回归通过，但属于上述测试子集，不重复计数。
两份真实模型启动脚本只测试 --help，没有运行真实 SAM3/GraspNet 权重。

覆盖同帧/全场景数据保留、任务隔离、过期/取消/重启拒绝、歧义、输入矩阵、
ASR 协议→Voice→真实 Planner 协议→SAM/Grasp fixture→WRS 虚拟抓取→Mock TTS，
以及语音停止后保留夹持、模型状态冻结和禁止后续播报。WRS 隔离探针确认 88 个路径点、
抬升及真实建模障碍物拒绝；失败规划恢复原模型状态。

首次失败包括重名测试模块、夹具遗漏 open、WRS viewer 无碰撞体、挂载夹爪结构忽略
遗漏及 3 cm 虚拟目标与网格冲突。已修复并收紧接近/闭合碰撞检查，最终例子使用
1 cm 配置方块。碰撞通过仅对应当前虚拟模型，不能推断真实夹爪可抓尺寸或接触性能。

证据：reports/acceptance.json、unit.xml、zenoh.xml、wrs.xml、grasp_final.xml，
以及统一脚本生成的逐项输出。实际版本 Python 3.12.0、Zenoh/zenohd 1.9.0、
Pydantic 2.13.5、pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8。
WRS doctor 科学环境 NumPy 1.26.4、SciPy 1.16.2、MuJoCo 3.5.0、WGPU 0.32.0。
WRS 提交 7815e6f110fd161fe3c3b7e6f978e5393c3cf502，工作树干净。
本阶段未新增依赖/改锁文件，保留上一阶段及用户未提交修改；未 commit/push。

**UNVERIFIED / 未完成**：SAM3/GraspNet 实际环境与权重、Graspness 适配、D405 实机、
实测外参/工具变换、真实场景和接触/掉落验证、有声输入输出、在线模型、实机运动与停止。
虚拟后端只确认已知方块的几何捕获和刚性连接；真实硬件能力没有被 Mock 成成功。
下一入口是 VISUAL_GRASP.md 的真实模型环境与固定相机标定条件。

### 后续 SAM3 SDK 环境切片（2026-10-13）

SAM3 模型适配改用 Transformers 图像 API，节点/技能/数据合同不变。新增 sam3 extra、
独立环境安装脚本、固定配置清单、显式下载与离线检查入口。uv 真正解析 147 包；
`.local/venvs/sam3` 安装 43 包，pip check 全部兼容。模型文件没有下载。

实际运行命令：

```powershell
$env:WRS_AGENT_PYTHON = 'D:/code/venv312/.venv/Scripts/python.exe'
./scripts/setup_sam3.ps1
& '.local/venvs/sam3/Scripts/python.exe' scripts/check_sam3.py --report reports/sam3_sdk.json
& '.local/venvs/sam3/Scripts/python.exe' scripts/check_sam3.py --device cpu --report reports/sam3_sdk_cpu.json
& '.local/tools/bin/uv.exe' --cache-dir .local/uv-cache pip check --python '.local/venvs/sam3/Scripts/python.exe'
& '.local/tools/bin/uv.exe' --cache-dir .local/uv-cache lock --check --offline --python 'D:/code/venv312/.venv/Scripts/python.exe'
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest tests/unit/test_sam3_assets.py tests/unit/test_grasp_perception.py tests/unit/test_fixed_camera.py tests/integration/test_grasp_pipeline.py tests/integration/test_visual_grasp.py -q --tb=short --junitxml=reports/sam3_grasp_regression.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts --output-format concise
git -c core.safecrlf=false diff --ignore-submodules --check
```

**27 passed，0 failed/errors/skipped**（含新增 8 个资产/下载前置检查；其余与上次验收重叠，
不加到 834 作为全仓重跑结果）。CUDA 与 CPU 实际 SDK 后处理通过：输出大小、左右目标
选择与 SDK 完整结果一致；默认歧义拒绝，NaN 不得使查询索引错位。Ruff、锁文件、
依赖一致性、下载脚本 --help、节点模块启动 --help 均通过。

SDK 实测版本：Torch 2.9.0+cu128、Torchvision 0.24.0+cu128、Transformers 5.15.1；
GPU 为 RTX 5060 Laptop（8151 MiB）。这些测试使用 fixture logits，不实例化训练权重，
trained_inference 明确为 unverified。此前沙箱内 6 个文件用例因临时路径权限失败，
已在获准沙箱外重跑通过；超长行静态检查失败亦已修复。

官方 SAM3 revision 固定 3c879f39826c281e95690f02c7821c4de09afae7，公开元数据隐藏
权重 SHA256。下载入口要求环境变量 HF_TOKEN 和 --download；先取得上游可信校验值，
缺授权/哈希时不下载；需要操作者取得访问权并确认。本次只验证其 --help 与拒绝路径，
未执行真实下载。GraspNet CUDA 扩展、两模型真实推理、显存峰值、标定及实机仍未验证。

### FastSAM-x 权重下载与真实 GPU 推理（2026-10-13）

用户明确允许选择 SAM3 或 FastSAM 下载。实际选择 FastSAM-x，官方 Ultralytics assets
v8.4.0，asset 340059253，144,972,346 字节；按上游发布 API 的 SHA256 校验成功：
752cadc2828edb1cd4bc4f9eb587100631af06ea2108f4c9ed56df4755701e76。
清单保存来源/许可；权重存入 `.local/models/fastsam/FastSAM-x.pt`，git check-ignore 确认忽略。

实际运行并通过：

```powershell
$env:WRS_AGENT_PYTHON = 'D:/code/venv312/.venv/Scripts/python.exe'
./scripts/setup_vision.ps1
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/download_fastsam.py --download
& '.local/venvs/vision/Scripts/python.exe' -X utf8 scripts/check_fastsam.py
& '.local/tools/bin/uv.exe' --cache-dir .local/uv-cache pip check --python '.local/venvs/vision/Scripts/python.exe'
& '.local/tools/bin/uv.exe' --cache-dir .local/uv-cache lock --check --offline --python 'D:/code/venv312/.venv/Scripts/python.exe'
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check scripts/download_fastsam.py scripts/check_fastsam.py --output-format concise
```

SDK 独立环境 46 包全部兼容；没有变更运行依赖版本。实际 Torch 2.9.0+cu128、
Ultralytics 8.4.175、RTX 5060 Laptop。SDK 自带 bus.jpg（1080×810）输出 70 个掩码，
框提示复用结果选中 1 个，掩码维度正确且数值有限。最终报告
reports/fastsam_inference.json 的 trained_inference=passed；这是实际权重推理，
不是前面的 fixture logits 测试。第二次命令启动的首次预测耗时 1.81 秒，PyTorch
峰值 allocated 858.55 MiB，仅属该样例，不能当作稳态时延或完整进程显存上限。

重复下载命令复核本地文件成功，无重复下载。首次 SDK 设置目录回退到临时目录，已
提前创建项目配置目录并重跑，最终路径为 `.local/ultralytics/Ultralytics/settings.json`。
Ruff、锁文件与 diff 检查通过。未重跑全仓测试：本次只增加权重准备/独立检查脚本，
没有改 Runtime 或节点执行行为。没有相机/机器人连接、图片上传或付费调用。

**尚未实现/验证**：FastSAM 的统一分割节点接入、文字目标定位/CLIP、桌面分割质量、
GraspNet 真实推理及物理抓取。SAM3 权重未下载，不能宣称 SAM3 推理通过。

### FastSAM 可选后端与 CLIP 实测（2026-10-13，接续上节）

本轮完成统一 SegmentationNode、FastSAM 模型适配、来源字段、独立启动与图片检查
入口；SAM3 默认值和已有启动名称兼容。CLIP ViT-B/32 复用本机缓存并核对官方哈希，
没有新增权重下载；固定 SDK 提交及许可记录在 clip_assets.json。uv 实际生成 150 包
锁文件，vision 环境实际 52 包依赖一致；基础解释器仍为项目指定 Python 3.12.0。

实际命令：

```powershell
& '.local/venvs/vision/Scripts/python.exe' -X utf8 scripts/check_fastsam_text.py
& '.local/venvs/vision/Scripts/python.exe' -X utf8 scripts/check_fastsam_text.py --image .local/venvs/vision/Lib/site-packages/ultralytics/assets/bus.jpg --target bus --target elephant --target person --report reports/fastsam_text_bus.json
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_fastsam.py tests/unit/test_grasp_perception.py tests/unit/test_sam3_assets.py --junitxml=reports/fastsam_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_grasp_pipeline.py tests/integration/test_visual_grasp.py tests/integration/test_vision_speech.py --junitxml=reports/fastsam_pipeline.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py examples/grasping/00_virtual_grasp.py
& '.local/venvs/sam3/Scripts/python.exe' -X utf8 scripts/check_sam3.py --report reports/sam3_sdk_after_fastsam.json
& '.local/tools/bin/uv.exe' lock --check --offline --cache-dir .local/uv-cache --python 'D:/code/venv312/.venv/Scripts/python.exe'
& '.local/tools/bin/uv.exe' pip check --python .local/venvs/vision/Scripts/python.exe --cache-dir .local/uv-cache
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m ruff check wrs_agent tests examples scripts
git -c core.safecrlf=false diff --ignore-submodules --check
```

**软件回归 PASS**：30 单元 + 6 集成，0 failed/errors/skipped；这是本轮针对性回归，
没有重新声称全仓 834 项通过。覆盖两个后端配置下的同帧/任务来源、取消/跨任务拒绝、
分数含义及文本权重来源、缺权重/校验失败前置拒绝、歧义与低匹配度。真实 Zenoh/WRS
集成中的感知模型是显式 fixture。有限例子输出“虚拟抓取结果： SUCCEEDED”和虚拟抬升
文本；新常驻入口只校验导入/配置/目录清单，未启动。SAM3 实际 SDK 张量后处理通过，
仍未加载 SAM3 训练权重。Ruff、lock、pip check、diff 检查通过。

**真实模型运行完成，但文字定位质量 FAIL**：Torch 2.9.0+cu128、Ultralytics 8.4.175，
RTX 5060 Laptop 上加载 FastSAM-x 和 CLIP。zidane.jpg 产生 19 个候选，tie 分数 .2956
被选择，elephant 被拒绝；人工查看保存掩码与原图，发现 tie 实际选中衬衫，领带被排除。
bus.jpg 70 个候选，bus 被判歧义，存在的 person 被判未找到。不能用这些脚本退出 0
声称定位正确。证据：fastsam_text.json、fastsam_text_bus.json、fastsam_text_0_mask.png、
fastsam_mask_review.json。未对桌面数据或完整显存并发进行基准验收。

首次沙箱模型探针在 Path.resolve 得到 WinError 5；沙箱单元运行出现失败后停滞，
只清理已核对命令行的本轮 pytest 进程，在获准沙箱外重跑获得上述真实通过结果。
本轮初始超长行检查失败，最终 Ruff 已重跑通过。

**UNVERIFIED / 后续入口**：推荐下一步验证 YOLO 定位框→同帧 FastSAM 框提示，当前
仍未接通；或取得 SAM3 访问条件后验证其原生文本定位。FastSAM/CLIP 后端保留为显式
实验入口。GraspNet 编译环境/权重、D405、实测标定、语音真实输入输出和实机抓取仍未
验证。用户现有 UR 文件没有在本轮修改；未提交、发布或启动真实设备。

### YOLO-World 定位 → 同帧 FastSAM 框提示（2026-10-13）

接续上节的 CLIP 误选证据，实现独立 locate_object 和 segment_region 技能。YOLO
先取得完整观测，再定位并保留任务内数据；FastSAM 通过 location_id 取同一份 RGB、
Depth、原生 XYZ。新 LocationInfo/SegmentInfo 校验观测身份、目标、任务和权重来源。
取消发生在定位或分割中时，迟到结果都不能进入后续抓姿步骤。普通 YOLO 精确类别匹配，
YOLO-World 接收完整短语；连续播报与按任务定位共用同一线程，并在异常后恢复原词表。

实际命令：

```powershell
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/unit/test_locations.py tests/unit/test_fastsam.py tests/unit/test_grasp_perception.py tests/unit/test_sam3_assets.py tests/unit/test_vision_results.py --junitxml=reports/located_grasp_unit.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_grasp_pipeline.py tests/integration/test_vision_speech.py --junitxml=reports/located_grasp_pipeline.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_visual_grasp.py --junitxml=reports/located_grasp_voice.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py -m pytest -q tests/integration/test_grasp_pipeline.py -k box --junitxml=reports/located_grasp_cancel.xml
& 'D:/code/venv312/.venv/Scripts/python.exe' -X utf8 -S scripts/run.py examples/grasping/00_virtual_grasp.py
& '.local/venvs/vision/Scripts/python.exe' -X utf8 scripts/check_box_segmentation.py
& '.local/venvs/vision/Scripts/python.exe' -X utf8 scripts/check_box_segmentation.py --image D:/code/test_ultralytics/images/f80a59131c9db080322649c050170f1b.jpg --target 'green pepper' --target tomato --target onion --report reports/box_segmentation_table_oriented.json
```

**PASS：46 单元 + 4 数据链/播报 + 5 语音协议/WRS = 55 项，0 failed/errors/skipped**。
补加分割期间取消后，单独重跑 1 项通过、2 deselected，属于上述测试子集。有限例子
已改为定位→框提示→抓姿→虚拟夹持抬升→Mock TTS，实际输出 SUCCEEDED 和虚拟抬升文本。
语音测试覆盖原 SAM3 路径和新定位路径，使用离线 ASR/模型协议夹具；WRS 碰撞/抬升为
真实虚拟模型运算，没有麦克风、扬声器、云模型或真实设备调用。
收尾按当前相机的 color_grid 合同补充定位数据的两种 alignment / frame_id 往返校验，
再运行上述单元命令取得 46 passed；消费者继续使用观测声明的网格，不改相机配置。

**真实模型图片检查完成、质量仍有局限**：复用并验证本机 YOLOv8m-Worldv2（57,233,192
字节）、CLIP、FastSAM-x，没有新增权重下载。Ultralytics 8.4.175 / Torch 2.9.0+cu128，
RTX 5060 Laptop。bus 图唯一目标框置信度 .958，掩码/框 IoU .631；对照原图确认掩码
覆盖可见车体、排除前景行人。elephant 拒绝、person 多目标拒绝；恢复词表后正常检测
输出 7 项。桌面照片按 EXIF 方向解码后，green pepper / tomato 区域匹配，IoU 约
.641 / .780；实际存在的 onion 漏检。此前原方向的 tomato 歧义和 onion 漏检报告
保留。证据为 reports/box_segmentation.json、box_segmentation_table.json、
box_segmentation_table_oriented.json、box_segmentation_review.json 及相应掩码。

人工核对是两张图片的定性检查，没有像素标注基准；照片没有真实深度，因此不能用于
声称 GraspNet 或物理抓取通过。真实模型探针与 fixture 集成结果分别记录，不混计。
框提示的 IoU/分差是初始筛选参数，不代表抓取成功概率。

依赖未增加，lock --check 150 包、vision pip check 52 包通过。新常驻入口
03_start_fastsam.py / 04_start_locator.py 为代码内配置，实际没有代用户启动；原相机
示例中的用户硬件选择保持原样。未修改用户 UR 驱动、提交或发布代码。
全仓 Ruff、Git diff --check、示例配置导入和目录清单通过。框提示路径去掉了仅适用于
CLIP 裁剪的小掩码过滤后，实际重跑 scripts/check_box_segmentation.py --report
reports/box_segmentation_final.json，bus 掩码/框 IoU 仍为 .631，缺目标和歧义仍拒绝；
SDK 对本地 settings.json 发出格式更新提示，模型版本及离线配置未变。

**未完成**：GraspNet 真实 SDK/CUDA 扩展及 checkpoint；新的 vswhere 查询发现本机已有
VS 2022 Community C++ 组件，当前 PATH 无 nvcc/cl、标准 CUDA Toolkit 目录不存在。
还需验证兼容编译环境。D405 深度链、实测外参/工具变换、真实夹持/抬升/掉落和停止验收
仍缺证据，完整目标未完成。SAM3 训练权重仍未下载，Graspness 尚未适配。


## 2026-10-13：筛选提交与 GitHub 上传完成

用户确认只排除密钥、账号、私有 API 地址和本机配置，保留通用接口代码。
提交 cbd6fb9e2d7df072b5b99907475ab9dcf978cbc2 已推送 origin/main，远程哈希核对一致。
120 文件；只纳入相机/抓取功能使用文档，ACCEPTANCE、DEPENDENCIES 的本地增量未提交。
发现同时整理的抓取夹具引用未同步，已保留新目录并补齐引用；测试增加 Agent 发现等待。
初次 WRS 发现失败已修复；另有虚拟抓取取消与相机首帧超时，单独复测通过，原失败保留。
最终 785 单元、98 Zenoh、20 WRS 用例各有通过证据，20 有限例子、Ruff、doctor、
uv lock --check --offline（152 包）、暂存扫描和 diff --check 通过；不宣称无时序波动。
具体逐轮结果和依赖版本：reports/publish_summary.json；命令脚本：reports/publish_virtual_checks.py。
Python 3.12.0；未运行实机、在线模型、真实音频或模型下载。所有本机设置保留。
后续入口 examples/grasping/README.md；真实相机联合运行、实测外参与物理抓取仍需单独验收。
