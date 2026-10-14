# 示例：一份脚本，一件事

每份脚本直接写出配置、调用和结果。基础例子的目标、文本和地址直接写在代码中；
相机例子的设备、权重与硬件开关也写在文件顶部，不需要命令行参数。
先读最简单的一份，再按需要看其他文件。

相机与视觉播报见 [运行说明](../docs/CAMERA.md)。当前 camera/01、02、06 已配置为
语音环境 `7451 / voice-goal / voice`：先运行 `voice/07_start_llm_voice.py`，再运行
相机 01、按需运行 YOLO 02，最后运行 `voice/09_viewer.py`；06 是可选独立预览。
07 已有 Router、Agent 和 TTS，不额外运行相机的 00、03、04。

如要使用独立相机模式，先将 01、02、06 的 ENDPOINT/ENV_ID/SESSION 一起改回
`tcp/127.0.0.1:7449` / `wrs-demo` / `wrs`，再分别运行
[`camera/00_start_router.py`](camera/00_start_router.py) →
[`01_start_camera.py`](camera/01_start_camera.py) → [`02_start_yolo.py`](camera/02_start_yolo.py)。
00 启动本组 Router 并准备共享口令；01 使用顶部 BACKEND 选择相机，
02 加载本地检测模型。需要播报时，继续依次运行
[`03_start_tts.py`](camera/03_start_tts.py) → [`04_start_agent.py`](camera/04_start_agent.py) →
[`05_speak_objects.py`](camera/05_speak_objects.py)。03 提供 TTS（按 BACKEND 选择），
04 启动调度固定任务的 Agent（默认不调用大模型），05 把检测描述交给 TTS 播报一次。
最后可运行 [`06_read_frame.py`](camera/06_read_frame.py)，同时显示 RGB、接收 FPS、
右侧推理原图上的 YOLO 框/类别/置信度和对应观测编号（Q/Esc 退出）。
02 顶部 CONFIDENCE 调节检测阈值，默认 0.35；更新后重启 02 和 06。
00 至 04 保持运行；06 只读取数据，
不依赖播报任务，也不影响 YOLO；未运行 02 时仍可看 RGB。
YOLO 可以先于相机启动；无新鲜帧时不产生有效的新检测，断开后清除当前结果并重试。
如果已用 `wrs/05_start_node.py` 启动了 Router 和 Agent，就不用本组的 00、04。
使用 D405 时，在 01 顶部设置 `BACKEND = "realsense"`，选择后运行即打开真实相机；
`SERIAL = ""` 使用默认相机，多台设备时可以填写指定序列号。
YOLO 只需修改 `WEIGHTS`（本地文件指纹自动计算），真实 TTS 则修改 `BACKEND = "qwen"`。
现有 `wrs/07_viewer.py` 和 `voice/09_viewer.py` 都包含相机面板和未连接提示。

从仓库根目录运行：

```powershell
./scripts/run.ps1 examples/beginner/01_action.py
```

`run.ps1` 只负责使用项目指定的 Python 和依赖。示例文件名后不需要附加参数。新机器先完成 [依赖准备](../docs/DEPENDENCIES.md)。机器人示例显式使用 `backend="wrs"`，需要真实 WRS 科学依赖。旧的 `wrs_virtual` 选项已删除，不作为别名接受。普通例子使用同步 API；常驻服务和底层通信使用 async。

## 基础动作

| 文件 | 只展示什么 | 预期结果 |
|---|---|---|
| [01_action.py](beginner/01_action.py) | 提交动作并等待 | SUCCEEDED，姿态 B |
| [02_skills.py](beginner/02_skills.py) | 查看可用技能和合同版本 | 名称、版本、描述 |
| [04_invalid_input.py](beginner/04_invalid_input.py) | 读取参数错误 | invalid_arguments |

## 任务

| 文件 | 只展示什么 | 预期结果 |
|---|---|---|
| [01_sequence.py](tasks/01_sequence.py) | 到 B → 向上 2 cm → 观察 | 实际 TCP 位置 |
| [02_parallel.py](tasks/02_parallel.py) | 运动与播报同时执行 | 同时出现 2 个活动动作 |
| [03_cancel.py](tasks/03_cancel.py) | 取消结束后开始新任务 | 旧 CANCELLED，新 SUCCEEDED |
| [04_watch.py](tasks/04_watch.py) | 观察任务进度 | 状态变化到 SUCCEEDED |

任务结果可用 `TaskState.SUCCEEDED` 等枚举成员判断，动作和规划结果分别使用 `ActionState`、`GoalState`；打印仍显示原字符串。取消示例演示这种写法，完整约定见 [状态枚举](../docs/task_handles.md#state-的字符串枚举)。

`after` 表示步骤依赖；没有依赖、使用不同节点的步骤可以并行。机器人动作由实际 WRS 模型执行。自然语言规划使用 [在线模型示例](models/01_plan.py)，执行见 [02_execute.py](models/02_execute.py)。这一组控制语义例子中的 TTS 为 Mock，不产生声音；真实 Qwen 播报见 [tts](tts/README.md)。

## 语音入口

| 文件 | 只展示什么 |
|---|---|
| [01_text_stop_task.py](voice/01_text_stop_task.py) | 任务运行时收到“停止” |
| [02_text_stop_speech.py](voice/02_text_stop_speech.py) | “别说了”只停止播报，机器人继续 |
| [03_text_query.py](voice/03_text_query.py) | “做到哪一步了”查询进度 |

这些例子直接传入已识别文本，不采集麦克风。ASR 或 UI 接入后调用同一个 `send_text()`。明确停止走控制路径，不等待模型。受理不等于停止完成，最终结果用任务或动作句柄查询。

## WRS 虚拟机器人

| 文件 | 只展示什么 |
|---|---|
| [01_move.py](wrs/01_move.py) | UR7E 虚拟运动到 B，读取关节值 qs 和 TCP 位置 |
| [02_cancel.py](wrs/02_cancel.py) | 取消虚拟运动并查询停止确认 |
| [03_new_action_after_cancel.py](wrs/03_new_action_after_cancel.py) | 确认取消后开放新动作准入，再回 home |
| [04_move_relative.py](wrs/04_move_relative.py) | 用真实 IK/FK 上、下、左、右各走 2 cm |
| [05_start_node.py](wrs/05_start_node.py) | 常驻 WRS 节点与任务服务，回环 7449 |
| [06_control_arm.py](wrs/06_control_arm.py) | 另一进程提交方向移动任务 |
| [07_viewer.py](wrs/07_viewer.py) | 只读显示节点模型，浏览器查看 |

需要已准备好的 WRS 科学依赖。使用真实 WRS UR7E 模型的 IK/FK 运动，不连接硬件；没有验证真实抓放或碰撞规划。普通任务取消会收尾，直接机器人动作取消则需要显式 `allow_actions()`；它只允许新动作，不续跑旧动作。

本机直接依次运行下面三个示例。服务会自动生成本组随机口令，客户端和 viewer 从同一仓库读取，不需要手动配置 `WRS_AGENT_TOKEN`；详见[示例口令](#example-token)。

```powershell
# 终端一：保持 WRS 节点运行
./scripts/run.ps1 examples/wrs/05_start_node.py
# 终端二：显示节点状态，然后在浏览器打开 http://127.0.0.1:8000
./scripts/run.ps1 examples/wrs/07_viewer.py
# 终端三：让机械臂移动
./scripts/run.ps1 examples/wrs/06_control_arm.py
```

`move_relative(dx=..., dy=..., dz=...)` 使用世界坐标系，单位米；上/下是 ±Z，左/右是 ±Y，前/后是 ±X，和浏览器视角无关。每次最多 10 cm、不可为零。TCP 目标朝向保持不变，运动路径是关节插值，不是碰撞规划或笛卡尔直线规划。不可达时动作 FAILED，原因是 `relative_target_unreachable`，不会伪造成功。

节点保留去重日志；重启后可能显示 HELD。方向控制客户端先查询停止确认，再显式调用 `allow_actions()` 开放新动作准入；UNKNOWN 不会被自动解除，旧任务不会恢复。

`07_viewer.py` 直接展示 WRS World、UR7E、坐标轴的创建，以及 `snapshot()` → `robot.fk()` → 定时刷新的完整过程。页面服务的生命周期由 `viewer_hub` 管理；场景和刷新代码就在示例内。它读取执行节点的真实关节状态，不执行运动。退出 viewer/控制客户端不取消任务，也不关闭节点。viewer 退出会关闭自己拥有的回环页面服务；运行中的其他 8000 端口服务不会被接管。浏览器需支持 WebGPU。节点重启后需重新连接 viewer，避免把旧会话显示成仍然在线。

## connect

`launch()` 拥有节点，`connect()` 只拥有客户端连接；客户端退出不会关闭服务。
直接参考 [WRS 服务](wrs/05_start_node.py) 与 [控制客户端](wrs/06_control_arm.py)，
或 [真实 TTS 服务与客户端](tts/README.md)。Mock 服务脚本只作为跨进程测试夹具保留在
`tests/fixtures/connect/`，不再作为播报示例。

## nodes

这一组交给开发新节点与技能的同学。`speaker` 是继承 Node 的独立 custom 节点，只提供 `greet`，逐字打印问候，不播放声音。Agent 是另一个进程，使用既有 Runtime 调度。Node 管理连接与退出，子类在 setup 中绑定执行器，见 [节点实现与启动](../docs/NODE_LAUNCH.md)。

| 文件 | 职责 |
|---|---|
| [greet_skill.py](nodes/greet_skill.py) | 唯一参数合同、技能描述和可取消处理函数 |
| [bindings.toml](nodes/bindings.toml) | 节点实例与端点；技能自动发现 |
| [router.json5](nodes/router.json5) | 本组 Router 配置，回环端口 7448 |
| [00_start_router.py](nodes/00_start_router.py) | 终端一：Router |
| [01_start_speaker.py](nodes/01_start_speaker.py) | 终端二：技能执行节点 |
| [02_start_agent.py](nodes/02_start_agent.py) | 终端三：任务调度节点 |
| [03_call_skill.py](nodes/03_call_skill.py) | 终端四：调用一次 greet |
| [04_task.py](nodes/04_task.py) | 终端四：通过任务调用 greet |
| [05_cancel.py](nodes/05_cancel.py) | 终端四：取消正在打印的问候 |
| [06_late_node.py](nodes/06_late_node.py) | 单独运行：Agent 先启动，未预登记的节点随后加入，直接调用与任务调用 |

`06_late_node.py` 自行启动并清理本机环境，无需先运行其他文件。其余文件演示分别启动与连接；部署文件仅作启动便利，不限制运行中的节点发现。

本机 Speaker 首次启动时自动准备本组口令，Agent 和客户端复用；不需要手动设置。若自行配置 `WRS_AGENT_TOKEN`，各程序必须使用同值，自动配置不会覆盖它：

```powershell
$env:WRS_AGENT_TOKEN = "<与其他节点相同的会话凭据>"
```

默认按下面的启动顺序直接运行即可。手动配置时，`$env:` 赋值只影响当前终端及之后启动的子进程，不会传给另一个已打开的终端或 IDE。

IDE 默认也使用同一仓库的自动口令。只有选择手动配置时，才需要在各运行配置中添加同值环境变量或选择同一 `.env`；只在 IDE 内置终端赋值不等于修改运行配置。

再依次启动：

```powershell
# 终端一
./scripts/run.ps1 examples/nodes/00_start_router.py
# 终端二
./scripts/run.ps1 examples/nodes/01_start_speaker.py
# 终端三
./scripts/run.ps1 examples/nodes/02_start_agent.py
```

看到两个节点的 ready 后，在终端四分别运行：

```powershell
./scripts/run.ps1 examples/nodes/03_call_skill.py
./scripts/run.ps1 examples/nodes/04_task.py
./scripts/run.ps1 examples/nodes/05_cancel.py
```

预期依次为 SUCCEEDED、SUCCEEDED、CANCELLED。先退出 Agent 和 Speaker，最后退出 Router，均使用 Ctrl+C。技能日志保存在 `.local/state/example-speaker.sqlite3`，重启保留去重记录；不要在节点运行时删除日志。

`03_call_skill.py` 至少需要 Router 和 Speaker 已就绪；任务和取消示例还需要 Agent。首次先运行客户端时，会提示应启动哪个服务；已有口令文件不表示服务仍在运行。

往已有节点新增技能只改包内定义与后端实现/绑定；Agent、客户端和普通 TOML 无需修改。详细边界见 [开发交接](../docs/DEVELOPMENT.md)。

## 模型

| 文件 | 只展示什么 |
|---|---|
| [01_plan.py](models/01_plan.py) | 读取 WRS 当前状态和技能，在线模型提出计划，不执行 |
| [02_execute.py](models/02_execute.py) | 在线模型 → Planner → Runtime → 独立 WRS 节点执行 |

所有厂商共用一组变量：`LLM_PROTOCOL` 选线协议（`openai_chat`/`openai_responses`/`anthropic_messages`），`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` 选端点、模型和凭据。GLM、DeepSeek、Qwen、Gemini、vLLM、Ollama 等 OpenAI 兼容服务用 `openai_chat`，OpenAI 官方用 `openai_responses`，Claude 用 `anthropic_messages`；各厂商地址见 [.env.example](../.env.example)。key 仅从环境读取，不要写进代码或提交；`.env` 不自动加载。两份脚本直接在线执行，可能产生服务费用，没有离线回退或额外代码开关。

`LLM_MODEL` 与 `LLM_BASE_URL` 必填：API Key 只做身份验证，不会选择模型，本项目也不假定默认厂商。请在 `.env` 中填写后，在 PyCharm 的 `01_plan`、`02_execute` 运行配置中选择该 `.env` 文件。配置缺失或非法时，示例在启动节点和创建 HTTP 客户端之前明确提示对应变量。旧版 `GLM_API_KEY`/`GLM_MODEL`/`GLM_BASE_URL` 分别改名为 `LLM_API_KEY`/`LLM_MODEL`/`LLM_BASE_URL`，再加 `LLM_PROTOCOL=openai_chat`。

```powershell
./scripts/run.ps1 examples/models/01_plan.py
./scripts/run.ps1 examples/models/02_execute.py
```

缺少凭据时在启动节点前报错。协议回归样本位于 `tests/fixtures/models/`，示例不读取。自动验收不会执行在线调用；运行结果可能是计划、澄清或结构化失败，不能保证模型总能给出可执行计划。

## 底层通信

| 文件 | 只展示什么 |
|---|---|
| [01_query.py](transport/01_query.py) | 向动作节点查询快照 |
| [02_events.py](transport/02_events.py) | 订阅一条动作进度事件，再查询终态 |
| [03_submit.py](transport/03_submit.py) | 显式获取 context，由客户端构造请求 |
| [04_timeout.py](transport/04_timeout.py) | 查询超时后仍可继续查询 |

通常只需前面的 `system.action()`；这组用于接手通信代码。事件不保证每条都被观察到，终态通过句柄查询。测试断言、故障注入和多场景汇总留在 `tests/`；`scripts/verify.py --wrs` 检查所有有限 WRS 示例输出，成组服务由集成测试验证。

<a id="example-token"></a>

## 示例口令

`WRS_AGENT_TOKEN` 是本项目对服务请求做校验的共享口令。客户端发送请求时带上它，服务端核对是否匹配；它与 WRS 库和模型的 `LLM_API_KEY` 无关。`"wrs"` 只是 05/06/07 这组示例的口令文件分组名。这里保留简单的请求鉴权，示例自动处理口令的生成和读取。

启动脚本在 `if __name__ == "__main__"` 中普通调用 `use_local_token(...)`，随后才 `asyncio.run(main())`。这项一次性的启动配置不需要线程切换，也不放进异步业务主流程；直接导入示例不会生成文件。

单脚本 `launch()` 已自动生成随机口令并传给自己的子进程。分开运行的 nodes、wrs、tts、voice 示例通过 [本机配置函数](_session.py) 共享口令：首次启动服务生成，保存到仓库的 `.local/example_tokens/<组名>.token`；同一仓库的配套客户端自动读取。四组互相隔离，重启沿用同一配置。口令不打印、不写日志，`.local/` 已被 Git 忽略；不修改你的 `.env`。这些文件是本机配置，不要提交或分享。

这个帮助函数只在受信任的回环示例中调用，不是跨机或实机凭据管理；Runtime/Transport 的鉴权仍保留，普通 `connect()` 仍要求显式环境凭据。POSIX 文件权限为 0600；Windows 继承工作区目录 ACL，不声称隔离其他有权读取此工作区的用户。

已有非空 `WRS_AGENT_TOKEN` 时优先使用它，且不会把它写入本机配置文件。此时服务、客户端和 viewer 都应配置相同值；不要将手动口令与自动配置混用。变量为空则使用自动配置；非法变量或损坏文件会明确报错，不能悄悄替换运行中服务的口令。

如需手动口令，使用 Python 生成一次：
```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
将输出填入各进程相同的 `WRS_AGENT_TOKEN`，或写入不提交的 `.env` 并让各 IDE 运行配置加载该文件。它与模型的 `LLM_API_KEY`、`LLM_MODEL` 无关。

更换自动口令时，先关闭该组全部服务/客户端，删除对应 `.local/example_tokens/<组名>.token`，再启动服务；运行中的进程不会自动重载口令。首次先启动客户端会提示对应服务入口；缓存文件已存在但服务没启动时，仍需先启动服务。

## 模型网络排查

`LLM_*` 配置正确后仍可能遇到网络错误。示例会区分 DNS、TCP 连接、代理、TLS/证书和超时；只输出静态说明，不回显异常原文、请求头或代理凭据。

先运行不读取 Key、不提交模型请求、不启动机器人的连通性检查（读取 `LLM_BASE_URL` 和 `LLM_PROXY`）：
```powershell
./scripts/run.ps1 scripts/check_llm_connection.py
```
收到 401/404 等 HTTP 状态也说明该次网络连接成功，不代表 Key/模型权限已验证。TLS 握手失败或 DNS 错误发生在收到 HTTP 响应之前，不能据此判断 Key 是否正确。

模型客户端默认直连：不读取 `HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`、`SSL_CERT_FILE` 等环境变量，环境里配置的代理不会静默承载机器人规划请求。需要代理访问海外服务时，显式设置 `LLM_PROXY=http://127.0.0.1:端口`。本项目不会自动改变系统 DNS/代理、关闭 TLS 校验或切换计费端点。

`LLM_REASONING_EFFORT` 可选，取值 `none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`，留空则不发送、由服务端默认。取值原样写进各协议自己的字段：Chat 的 `reasoning_effort`、Responses 的 `reasoning.effort`、Claude 的 `output_config.effort`（Claude 没有 `none` 档，`none` 改为发送 `thinking.type=disabled`）。思考越少等待越短、推理也越少；Runtime 仍会校验每个计划，退化的是计划质量而非它的边界。

各档位被哪些模型接受由服务端决定，本项目不按模型名改写取值；遇到 HTTP 400 先清空该项。厂商私有开关放进 `LLM_EXTRA_BODY`（JSON 对象，只能新增字段，不能覆盖适配器生成的字段）。旧版 `GLM_THINKING=low/high/max` 改为 `LLM_REASONING_EFFORT` 同值；`GLM_THINKING=off` 改为 `LLM_EXTRA_BODY={"thinking":{"type":"disabled"}}`。厂商文档称 GLM-5.3 系列不接受关闭思考（HTTP 400、错误码 1210），但本仓库在 Coding Plan 端点上实测 `glm-5.3-flash` 接受了关闭且明显更快，因此以实测为准、遇到 400 再改 `LLM_REASONING_EFFORT=low`。Coding Plan 端点对各档位的映射与标准 API 文档不同，换模型后需实测确认。

`LLM_TOOL_CHOICE` 可选 `auto`（默认）或 `required`。`auto` 下部分模型会把计划 JSON 写进回复正文；正文永远只按回答处理，所以规划结果显示 ANSWER、不会执行。`required` 强制每次调用 `propose_plan`，回答和澄清仍以 `kind=answer`/`clarify` 返回，不丢功能。实测 DashScope 的 `qwen3.8-flash`（思考关闭）在 `auto` 下 12 次有 4 次写成正文，`required` 下 12 次全部是工具调用。智谱文档称 GLM 只支持 `auto`；Claude 开启思考时不接受强制工具，这两种情况保持默认。

## 中文语音与独立播报

视觉抓取从 [RealSense 示例 00–06](grasping/README.md) 开始，顶部修改配置后直接运行，
无需脚本参数。D405 → YOLO-World → FastSAM → GraspNet 输出同帧抓取候选；不包含
合成相机或模型替身，不执行机械臂动作。真实执行接口和实测外参尚未完成。
已有 camera 的 Router、RealSense 和 Agent 可复用，模型环境/启动顺序见该目录说明。
固定标定合同、环境与权重、许可及验证范围见 [VISUAL_GRASP.md](../docs/VISUAL_GRASP.md)。

| 目录 | 从哪里开始 |
|---|---|
| [wrs](wrs/README.md) | 运动、停止、场景快照和 WRS 显示，01–08 |
| [voice](voice/README.md) | 文字控制 01–03；本地按键语音 05–06；在线模型循环语音 07–09 |
| [tts](tts/README.md) | 01 启动并播报预热；02 说话；03 取消合成/播放 |
| [grasping](grasping/README.md) | RealSense 采集、目标定位/分割与抓姿候选，00–06 |

原 wrs/08、09 移至 [voice/05](voice/05_start_wrs_voice.py)、[voice/06](voice/06_push_to_talk.py)，配合 [wrs/07_viewer.py](wrs/07_viewer.py)。在线语音采用另一地址与自己的 viewer，避免误连本地规划模板。安装和下载见 [Qwen 指南](../docs/QWEN_SPEECH.md)。自动验收不会启动麦克风、扬声器或付费模型。
