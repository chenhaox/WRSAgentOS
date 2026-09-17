# WRS-Agent V1

已实现 M0–M2 的可运行切片及“独立能力节点”增量：单 Runtime/Planner、独立 Mock WRS 和 Mock TTS、Voice 事件回放，经真实 Zenoh router 跨进程通信。

技能由 `configs/bindings.toml` 显式绑定执行节点。Runtime 按依赖和资源调度；WRS 只执行机器人技能，TTS 自己管理播报。Voice 可直接取消 TTS 或停止 WRS；查询和 VAD 不停止机械臂。模型等待不会占用控制路径。

运行环境固定为 `D:\code\venv312\.venv\Scripts\python.exe`（3.12.0）。`scripts/run.ps1` 使用该解释器和项目 `.local/deps`，不修改共享虚拟环境。

在本目录的 PowerShell 中运行：

```powershell
git submodule update --init --recursive
./scripts/bootstrap.ps1
./scripts/install_router.ps1
./scripts/run.ps1 examples/01_zenoh_roundtrip.py
./scripts/run.ps1 examples/02_mock_interrupt.py
./scripts/run.ps1 scripts/verify.py
```

也可直接指定解释器，通过同一个项目启动入口运行：

```powershell
& 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py examples/02_mock_interrupt.py
```

在 IDE 中运行时，将脚本设为 `scripts/run.py`、参数设为 `examples/02_mock_interrupt.py`、解释器选项设为 `-X utf8 -S`，工作目录设为仓库根目录。这会加载项目锁定依赖；仅选择同一个 Python 而直接运行示例，仍可能加载共享环境中其他版本的包。router 版本检查支持 `RUST_LOG=info/debug` 产生的前置日志，真实版本不匹配时会显示期望版本、路径和实际输出。

第二个示例演示并行动作、挂起的 Mock 模型等待、只取消 TTS、持物停止、拒绝旧动作/旧模型结果，以及重新规划到 C。结束时清理自己启动的进程。前台持续运行用 `./scripts/run.ps1 -m wrs_agent launch`，Ctrl+C 停止。

实际验证：92 项单元测试、8 项真实 Zenoh 集成测试、三个示例、Ruff 和 doctor 通过；独立提交快照同样 100 passed。GLM 为离线 HTTP 夹具，结果见 docs/ACCEPTANCE.md。verify.py 会在本地 reports/ 生成验收结果。开发助手指令、执行计划、IDE 配置和机器报告保留本地，不提交远程。

通信只有三种语义：

- Event/Stream：发布事实和进度，多订阅者直接接收；事件可能丢失，状态查询补偿。
- Query：能力、状态等短请求；控制服务有独立有界入口。
- Action：快速 ACCEPTED，随后进度/终态、按 ID 查询和取消；WRS/TTS 共用同一合同。

每节点最多一个资源动作，最多 12 步任务、16 项追加任务、4096 条会话动作/控制记录；达到容量明确拒绝，不删除去重历史再重放。动作 ID、任务 revision、节点 boot_id/epoch、短期授权和停止确认各自独立。

默认只连接回环地址，关闭发现；凭据由启动器生成，经环境变量传给自己的子进程，不靠 source/category/节点名称授予权限。该配置仅用于受信本机 Mock，远程身份绑定与 ACL 尚未实现。网络拥塞/查询丢失返回错误或超时，不自动重发物理动作。

WRS submodule 固定到用户指定 [chenhaox/WRS2](https://github.com/chenhaox/WRS2) 的 `2bb014b747833c2fd9345115fbe26ffb11376f20`。真实 WRS import 和 Lite6 FK 已通过；详情见 [docs/WRS_AUDIT.md](docs/WRS_AUDIT.md)。本任务未修改原有本地 WRS2 工作拷贝。

尚未完成：WRS 连续虚拟运动适配、GLM 真实账号服务验证、条件缓存与有限恢复、独立 Vision 节点、真实 ASR/TTS/麦克风、双机保护配置和负载基准。挂起测试没有调用 GLM。所有动作和播报结果均为 Mock；实机后端不可选择。

GLM 客户端已支持国内智谱的 OpenAI 兼容 Chat Completions，复用 ModelPlanner，默认仅运行原生 tool_calls 的离线 HTTP 夹具：

```powershell
./scripts/bootstrap.ps1 -Extra glm
./scripts/run.ps1 examples/04_glm_task.py --dry-run
```

输出明确包含 network=false、actions_submitted=0。端点为 `https://open.bigmodel.cn/api/coding/paas/v4`，没有客户端身份伪装。环境变量见 .env.example；该文件不自动加载。模型名必须填写账号实际支持值，密钥只从进程环境读取。

具备对应服务使用权限后，在本机环境设置 GLM_API_KEY、GLM_MODEL 和 GLM_BASE_URL，再执行 `./scripts/run.ps1 examples/04_glm_task.py --live-model --dry-run`。示例只返回计划建议，不启动动作节点。当前运行节点仍默认 MockClient；真实 Runtime 接入须先完成独立服务验收。

适配器保留原生 message/tool_calls 和 usage 于内存，拒绝多工具、未知工具、非法/截断参数与拒绝响应；不自动执行工具。当前只接受非流式请求，stream=true、未知参数和 Claude 协议在配置时失败。客户端复用连接，设置总超时，至多重试一次建连；HTTP 错误不重试、不跟随重定向、不切换计费端点。原生内容和凭据不写日志。

[官方套餐协议](https://docs.bigmodel.cn/cn/terms/subscription-agreement) 对自建应用/机器人调用有用途限制，协议兼容不改变授权范围。Coding Plan 聊天端点不能用作 TTS；[glm-tts](https://docs.bigmodel.cn/cn/guide/models/sound-and-video/glm-tts) 使用独立的普通 API `/api/paas/v4/audio/speech`，本仓库未接入或调用该服务。
