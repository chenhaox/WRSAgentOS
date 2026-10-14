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

若本机其他 Python 环境已安装 ASR，IDE 直接运行 06 或 08 时可继续使用它；原 `.local/venvs/qwen-asr` 也可用。终端从仓库根目录用该解释器加 `-m examples.voice.06_push_to_talk`（或 08 模块）启动。默认模型路径固定为项目根目录下 `.local/models`，不需要因工作目录不同重新下载。

地址 `7449 / wrs-demo`。完整词表在 [commands.py](commands.py)；未知词不交给 Mock Planner。“停止”直接控制任务；`s` 也可发送停止，`q` 退出观察。节点重启/中断时，旧录音识别出的运动指令会被拒绝。

[10_remote_mic.py](10_remote_mic.py) 让麦克风留在打开页面的设备上，识别和 UR7e 仍在运行 05 的机器上。用 ASR 环境启动。本机或 `ssh -L 8010:127.0.0.1:8010` 打开 http://127.0.0.1:8010 。同一局域网的其他设备打开打印出的 `https://<地址>:8443`，并信任 `.local/remote-mic` 里的一次性证书。浏览器只在本机页面或已信任的 HTTPS 页面里提供麦克风。Zenoh 仍只监听回环地址。

## 自然语言目标交给在线模型：按住说话或循环收音

[07_start_llm_voice.py](07_start_llm_voice.py) 使用真正的在线模型 Planner，不做离线回退。启动它前配置 `LLM_PROTOCOL`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`（GLM、OpenAI、Claude 等写法见 [.env.example](../../.env.example)），安装 `llm` 依赖；凭据不写代码，`.env` 不自动加载。执行语音目标可能产生模型费用。它同时启动 `asr` 节点，首次加载 TTS 与 ASR 模型需要数十秒。

在 PyCharm 的 `07_start_llm_voice` 运行配置中，将 `Paths to .env files` 指向仓库根目录
的 `.env`。只填写文件或在 IDE 终端设置变量，
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
[02_read_frame.py](../camera/02_read_frame.py) 和
[03_start_yolo.py](../camera/03_start_yolo.py) 已统一配置为语音环境，可直接运行：

```python
ENDPOINT = "tcp/127.0.0.1:7451"
ENV_ID = "voice-goal"
SESSION = "voice"
```

保持现有相机 BACKEND、设备和 YOLO 权重配置。然后分别运行：

1. `voice/07_start_llm_voice.py`，等待就绪；已包含 Router、Agent、TTS、Voice、ASR。
2. `camera/01_start_camera.py`，提供 RGB、深度和点云。
3. `camera/03_start_yolo.py`，需要检测/描述物体时启动，持续读取 01 的新帧。
4. `voice/09_viewer.py`，打开 `http://127.0.0.1:8001`。

这套流程不需要 `camera/00_start_router.py`、04 或 05；另开的 7449 Router 不会把
相机接到 7451。02 是可选 OpenCV 预览，已使用上面三项配置。
只看 RGB/深度/点云不需要 YOLO；想说“识别画面中的物体并播报”则需要 03，Agent
在同一环境动态发现 `describe_objects` 技能，执行取决于模型提案和 Runtime 校验。
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

## 当前收音边界

Qwen ASR 0.6B 常驻，中文，句尾默认 250 ms 低能量截句。循环收音每句最多 4 秒；按住说话由松手决定断句，封顶 15 秒。超时或过长整句丢弃，不截断。语音超出范围时缩短指令，不能把截断文本当作完整授权。识别/换句期间有短暂间隙，当前不是无缝流式或全双工通话。

“机器人”是识别后的文字前缀，不是新的声学唤醒模型，只有常开麦克风的循环收音要求它；它同时挡住被麦克风收回来的自己的播报，所以建议使用耳机防止扬声器回声。按住说话没有这道前缀，靠按住按钮界定一句话。未实现回声消除、声纹鉴权和远场抗噪；低能量分句不等于停止意图。录音只在内存，不默认保存或上传；提交的目标文本会发给所配置的在线模型。Ctrl+C 结束收音/观察，不隐式取消远端执行；先说“停止”并确认，再关闭输入进程。

安装、依赖隔离、模型大小和已有实测见 [Qwen 语音指南](../../docs/QWEN_SPEECH.md)。
