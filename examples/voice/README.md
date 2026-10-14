# Voice：从文字入口到常驻中文语音

Voice 已是独立 Zenoh 节点，处理已识别的文字、停止意图和控制路由。麦克风/ASR 使用独立输入进程；它离线或重启时，UI 文字入口、Agent 和其他动作节点仍可运行。收音既可以留在客户端脚本里（06、08 自己开麦克风），也可以放进可选的 `asr` 节点、由界面按键远程驱动（09）；两种方式下 Voice 都只消费已识别的文字。`asr` 不是必需节点，默认部署不启用。

## 先理解入口：不用麦克风

| 文件 | 内容 |
|---|---|
| [01_text_stop_task.py](01_text_stop_task.py) | 文字“停止”取消正在执行的 WRS 任务 |
| [02_text_stop_speech.py](02_text_stop_speech.py) | 文字“别说了”只停止模拟 TTS，WRS 继续 |
| [03_text_query.py](03_text_query.py) | 文字查询任务进度 |

这些是控制语义例子，不录音；02 的模拟 TTS 不出声。真实播放看 [TTS 例子](../tts/README.md)。

## 本地短指令：不需要云端

[05_start_wrs_voice.py](05_start_wrs_voice.py) 启动 WRS、Qwen TTS、Agent、Voice，播报启动提示；[06_push_to_talk.py](06_push_to_talk.py) 按回车收音，将“向上/下/左/右/前/后”等完整短词映射成确定步骤，运动与预合成播报并行。

```powershell
# 终端一；与 wrs/05 二选一
./scripts/run.ps1 examples/voice/05_start_wrs_voice.py
# 终端二：浏览器 http://127.0.0.1:8000
./scripts/run.ps1 examples/wrs/07_viewer.py
# 终端三：使用 ASR 环境；-m 仅用于 Python 定位模块，不是例子的模式参数
.local/venvs/qwen-asr/Scripts/python.exe -m examples.voice.06_push_to_talk
```

本机 `D:\code\venv312\.venv` 已安装 ASR，IDE 直接运行 06 或 08 时可继续使用它；原 `.local/venvs/qwen-asr` 也可用。终端从仓库根目录用该解释器加 `-m examples.voice.06_push_to_talk`（或 08 模块）启动。默认模型路径固定为项目根目录下 `.local/models`，不需要因工作目录不同重新下载。

地址 `7449 / wrs-demo`。完整词表在 [commands.py](commands.py)；未知词不交给 Mock Planner。“停止”直接控制任务；`s` 也可发送停止，`q` 退出观察。节点重启/中断时，旧录音识别出的运动指令会被拒绝。

[10_remote_mic.py](10_remote_mic.py) 让麦克风留在打开页面的设备上，识别和 UR7e 仍在运行 05 的机器上。用 ASR 环境启动。本机或 `ssh -L 8010:127.0.0.1:8010` 打开 http://127.0.0.1:8010 。同一局域网的其他设备打开打印出的 `https://<地址>:8443`，并信任 `.local/remote-mic` 里的一次性证书。浏览器只在本机页面或已信任的 HTTPS 页面里提供麦克风。Zenoh 仍只监听回环地址。

## 自然语言目标交给在线模型：按住说话或循环收音

[07_start_llm_voice.py](07_start_llm_voice.py) 使用真正的在线模型 Planner，不做离线回退。启动它前配置 `LLM_PROTOCOL`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`（GLM、OpenAI、Claude 等写法见 [.env.example](../../.env.example)），安装 `llm` 依赖；凭据不写代码，`.env` 不自动加载。执行语音目标可能产生模型费用。它同时启动 `asr` 节点，首次加载 TTS 与 ASR 模型需要数十秒。

在 PyCharm 的 `07_start_llm_voice` 运行配置中，将 `Paths to .env files` 指向仓库根目录
的 `.env`（本机为 `D:\code\ch\agentOS\.env`）。只填写文件或在 IDE 终端设置变量，
不会让这份运行配置自动读取它；`01_plan` 等其他脚本的设置也不会自动共享给 07。
若文件里已填写模型却仍报 `LLM_MODEL` 缺失，先检查这一项。启动时的“已配置本机示例
共享口令”只表示 `WRS_AGENT_TOKEN` 就绪，模型仍需要单独的 `LLM_*` 配置。
从终端运行下面命令时，也要先在该终端设置这些环境变量。

```powershell
# 终端一：不要同时保留另一套 Qwen 语音服务，避免额外占用显存
./scripts/run.ps1 examples/voice/07_start_llm_voice.py
# 终端二：浏览器 http://127.0.0.1:8001，按住面板上的“按住说话”按钮
./scripts/run.ps1 examples/voice/09_viewer.py
# 终端三（与 09 的按住说话共用麦克风，二选一）：启动即循环收音
.local/venvs/qwen-asr/Scripts/python.exe -m examples.voice.08_listen_goals
```

这组使用独立地址 `7451 / voice-goal` 和口令分组，避免误连到确定性规划例子。

### 在语音页面看相机、通过语音调用 YOLO

当前 [01_start_camera.py](../camera/01_start_camera.py)、
[06_read_frame.py](../camera/06_read_frame.py) 和
[02_start_yolo.py](../camera/02_start_yolo.py) 已统一配置为语音环境，可直接运行：

```python
ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
```

保持现有相机 BACKEND、设备和 YOLO 权重配置。然后分别运行：

1. `voice/07_start_llm_voice.py`，等待就绪；已包含 Router、Agent、TTS、Voice、ASR。
2. `camera/01_start_camera.py`，提供 RGB、深度和点云。
3. `camera/02_start_yolo.py`，需要检测/描述物体时启动，持续读取 01 的新帧。
4. `voice/09_viewer.py`，打开 `http://127.0.0.1:8001`。

这套流程不需要 `camera/00_start_router.py`、03 或 04；另开的 7449 Router 不会把
相机接到 7451。06 是可选 OpenCV 联合预览，已使用上面三项配置，左侧实时 RGB，
右侧在推理时原图上绘制 YOLO 框、类别和置信度；YOLO 未启动时仍能看 RGB。
更新后重启 02 和 06。02 的 CONFIDENCE 默认 0.35，修改并重启节点后生效。
只看 RGB/深度/点云不需要 YOLO；想说“识别画面中的物体并播报”则需要 02，Agent
在同一环境动态发现 `describe_objects` 技能，执行取决于模型提案和 Runtime 校验。

要播报可直接问“桌子上有哪些物体？”，或说“识别画面中的物体并播报”。模型应通过
工具 `propose_plan`（默认工具模式）或显式 JSON 输出模式提交一个计划：
`describe_objects → speak`；后者用 `inputs.text`
引用前者的 `text` 输出，并声明 `depends_on`。相机状态中的 count 不能代替描述，模型
也不能自己编物体名称。普通 `ANSWER` 只显示文字，不自动触发扬声器。

当前 03 的 `TABLE_ROI=None` 表示全画面，播报会说“画面中识别到…”；需要限定桌面时，
按实际画面设置该文件的归一化 `TABLE_ROI` 后重启 03。这只限制二维检测区域，不确认
物体与桌面的三维接触关系。未检出时说“未识别到物体”，不把它解释为桌上确实没有物体。

规划失败且任务为“无”时，新的 09 会显示独立的规划故障：`llm_unexpected_tool` 表示
模型直接调用了 describe_objects 等未提供的工具；`llm_plan_json_invalid` 表示计划
不是完整 JSON；`llm_plan_invalid` 表示 JSON 不符合计划结构。这些错误发生在
执行前，不是相机掉线的证据，也不会触发自动付费重试。Chat 协议的
`LLM_TOOL_CHOICE=required` 现在显式选择 propose_plan，提示附带完整两步计划示例。
更新后重启 07、09；重启 YOLO 02 可同时刷新其技能说明。
07 的 Agent 子进程不会热加载代码，浏览器刷新或仅重启 09 不会更新模型提示。
若新版 09 提示“Agent 未提供规划错误详情”，需停止并重新运行 07，等待它重新就绪。

本机已验证并切换的配置为（保留自己的地址和密钥）：

```dotenv
LLM_PROTOCOL=openai_chat
LLM_MODEL=qwen3.8-flash
LLM_OUTPUT_MODE=json_schema
LLM_REASONING_EFFORT=
LLM_EXTRA_BODY={"enable_thinking":false}
```

依据 [Qwen 结构化输出文档](https://help.aliyun.com/en/model-studio/qwen-structured-output)，
qwen3.8-flash 支持严格 JSON Schema；本账号已实际验证。每次请求从 PlanDecision 和
当前技能参数生成 Schema，将 args/inputs 展开为技能声明的字段，避免服务端拒绝
任意键名字典。请求使用 `response_format.type=json_schema` 和 `strict=true`；
不发送 tools/tool_choice。普通回答和澄清也返回同一 JSON decision。
LLM_TOOL_CHOICE 在两种 JSON 模式下无效；`reasoning_effort=none` 不能替代明确的
`enable_thinking=false`。依赖、结果绑定、资源和执行权限仍由本地 Runtime 校验；
坏 JSON、截断、错误工具调用照常拒绝，不补括号、不自动切换模型或重试。
默认 tools 模式保持正文只作 ANSWER，json_object 仅约束语法，json_schema 约束结构。

此前 qwen-flash 的工具模式曾返回坏 JSON，json_object 又把顶层 text 放进 plan；
这说明只调 tool_choice 或增加格式提示不足以解决故障。最终配置下，9 种视觉问法、
问候/澄清/不播报 3 个分支均通过；三句原始目标另在真实模型→隔离 Zenoh Runtime→
合成检测→Mock TTS 中完成 SUCCEEDED。真实返回的 12 份决策也回放通过；307 项离线
回归通过。证据见 docs/ACCEPTANCE.md 和 reports/voice_strict_*.json。
这些结果验证规划及模拟执行，不代替本机相机检测质量或扬声器播放验收。

修改环境变量后重启 07 才会生效。IDE 若另设同名变量，应同步修改，避免覆盖 `.env`。
启动行会输出模型、协议与 `output_mode`（工具模式还显示 `tool_choice`），不打印凭据或
API 地址。应看到 `qwen3.8-flash｜openai_chat｜output_mode=json_schema`。
确认 01 相机和 02 YOLO 显示 ready 后，在 09 说“看看画面里有什么。”即可再次验证。
面板“模型输出”显示的是 token 统计；规划失败时“无”不代表模型没有回复。

无需把 `LLM_API_KEY` 配给相机或 YOLO。若手动设置了 `WRS_AGENT_TOKEN`，它优先于
自动口令，各进程必须同值；只改 SESSION 不会覆盖已设置的环境变量。

viewer 可以先于相机或 YOLO 启动，节点加入后会刷新；重启 09 可加载页面代码修复。
相机面板离线时会显示查询地址、环境和节点，先对照这三项。启动时的零字节 TCP
探测不代表相机故障；带相机面板的 viewer 现已过滤这种空握手日志，非空无效请求
和真正的 WebSocket 错误仍会记录。

[09_viewer.py](09_viewer.py) 显示关节状态，并用 `asr` 节点收音：按住面板上的“按住说话”按钮开始录音，松开结束。麦克风和 Torch 都在节点进程里，viewer 只发按下/松开/取结果三个短请求，渲染不会被识别阻塞；浏览器失焦或断线会自动松手。文字回到 viewer 后按与 08 相同的准入检查提交目标——状态未确认或收音期间控制权限变化都会拒绝。上一条还在规划或执行时，viewer 先经 Voice 控制通道发出停止（取消任务，并作废还在等模型的规划），确认停下后再提交新目标；5 秒内没停下或结果变成 UNKNOWN，新目标就放弃，不在未确认的状态上叠加运动。不同的是按住说话不要求唤醒前缀：按住按钮本身已经声明这一句是对机器人说的。“停止”是例外：由节点直接送进 Voice 控制通道，不等 viewer 转发，面板上的“立即停止”按钮则完全不经过识别。

面板另外每 0.3 秒刷新「规划」和「任务」：规划显示 `WAITING`/`DONE`/`CLARIFY：原因` 等 GoalState，任务显示 `RUNNING｜正在执行的步骤` 或终态。两者读的是 Runtime 总览，所以别的客户端提交的任务也会出现在这里。Runtime 的总览只带已出结果的步骤，拿不到计划总步数，因此报的是当前步骤或已完成步数，不是百分比。

循环收音（08）的麦克风一直开着，所以普通目标必须以“机器人”开头：说“机器人，移动到 B”或“机器人，向上移动两厘米”。按住说话（09）直接说“移动到 B”。两者的“停止”“停止播报”“状态”都无需唤醒，直接走已有 Voice 控制/查询入口。说“移动后播报”时，是否加入 speak 由 Planner 提议，再由 Runtime 校验。澄清、拒绝、任务结果显示在终端。

收音循环只等待目标受理；规划与任务结果由另一个有界观察协程显示，所以云端模型慢时仍能收音并发送停止。同时只处理一项规划/任务，新的目标不积压；要改目标，先停止，确认结束后说新目标。收音过程中实例或控制 epoch 改变，会丢弃这次迟到的运动目标。

这是“语音下达任务”，没有新增多轮聊天历史或通用聊天机器人；这些以后应放在 Agent 的对话层，Voice 保持输入和控制职责。

## 在主场景查看点云和抓取候选

升级后重启 `09_viewer.py`、刷新 8001 页面。点“点云”后，XYZ 直接画在 WRS 主场景中；
面板只保留类型切换和说明，没有第二个点云绘制窗口。“定位点云”可将观察视角移到
相机展示区。相机尚无外参，这个区域只是显示布局，没有与机器人配准。

点云内的“抓取候选”读取同环境的 `graspnet` 节点。需要重启更新后的
`grasping/04_start_graspnet.py`，它新增只读的有效结果发现接口；仅查看不会触发推理。
完成一次 `infer_grasps` 后显示推理时同帧点云和最多 8 个夹爪示意，绿色为最高模型分数，
橙色为其他候选。结果最多保留源观测起的 30 秒，失效/断开时清除；切回实时点云恢复新帧。
这里显示的是未做碰撞检查的候选，不会发送机械臂动作。

现有 grasping 示例默认 7449 / wrs-demo / wrs，与 09 的语音环境不同。要共用 09，先把
`grasping/02_start_locator.py`、`03_start_fastsam.py`、`04_start_graspnet.py` 和
`06_propose_grasps.py` 顶部的三项改为：

```python
ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
```

沿用已运行的 voice/07 和 camera/01；不要另启 grasping 的 00、01、05。按原 grasping
README 选择对应解释器启动 02、03、04，再运行 06 生成候选。若 camera/02 的 `yolo`
已经运行，先关闭它再使用 grasping/02，避免同名节点冲突。06 的 TARGET 指定实际物体，
它提交固定感知计划，不需要在线模型规划。共享会话显式设置 WRS_AGENT_TOKEN 时各进程
仍须一致；不要把口令写入代码。09 顶部 GRASP_NODE 可改节点名称，默认 graspnet。

## 当前收音边界

Qwen ASR 0.6B 常驻，中文，句尾默认 250 ms 低能量截句。循环收音每句最多 4 秒；按住说话由松手决定断句，封顶 15 秒。超时或过长整句丢弃，不截断。语音超出范围时缩短指令，不能把截断文本当作完整授权。识别/换句期间有短暂间隙，当前不是无缝流式或全双工通话。

“机器人”是识别后的文字前缀，不是新的声学唤醒模型，只有常开麦克风的循环收音要求它；它同时挡住被麦克风收回来的自己的播报，所以建议使用耳机防止扬声器回声。按住说话没有这道前缀，靠按住按钮界定一句话。未实现回声消除、声纹鉴权和远场抗噪；低能量分句不等于停止意图。录音只在内存，不默认保存或上传；提交的目标文本会发给所配置的在线模型。Ctrl+C 结束收音/观察，不隐式取消远端执行；先说“停止”并确认，再关闭输入进程。

安装、依赖隔离、模型大小和已有实测见 [Qwen 语音指南](../../docs/QWEN_SPEECH.md)。
