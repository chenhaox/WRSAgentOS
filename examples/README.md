# 示例学习顺序

想先弄清基础概念和每个例子背后的设计，可以先读 [从一个动作开始，读懂 WRS-Agent](../docs/getting_started.md)，再按下面的顺序运行。

从 beginner 开始，不需要先学 asyncio、Zenoh 消息或模型 SDK。下面命令在仓库根目录运行；指定 Python 和本地依赖的安装方式见主 README。

| 顺序 | 示例 | 学会什么 | 写法 / 条件 |
|---|---|---|---|
| 1 入门 | [beginner/01_action.py](beginner/01_action.py) | 调用一次动作、查询、等待结果 | 同步，Mock |
| 2 入门 | [beginner/02_skills.py](beginner/02_skills.py) | 查看当前有哪些技能 | 同步，Mock |
| 3 任务 | [tasks/01_parallel_and_stop.py](tasks/01_parallel_and_stop.py) | 多步任务、并行、查询和分别停止 | 同步，Mock + 语音事件回放 |
| 4 任务 | [tasks/02_cache_reuse.py](tasks/02_cache_reuse.py) | 重复目标复用计划，条件变化拒绝复用 | 同步，脚本化 Mock 模型 |
| 5 虚拟机器人 | [tasks/03_wrs_scene.py](tasks/03_wrs_scene.py) | 同一套 API 操作真实 WRS 虚拟模型 | 同步，需要 WRS 依赖 |
| 6 连接已有节点 | [tasks/04_connect.py](tasks/04_connect.py) | 启动和使用分开；退出客户端不关闭节点 | 同步，需要先启动 TTS |
| 7 核心开发 | [developer/01_zenoh_roundtrip.py](developer/01_zenoh_roundtrip.py) | LocalStack 启动了什么；程序怎样问答、收通知 | async，真实 Zenoh，逐步中文输出 |
| 8 核心开发 | [developer/02_mock_interrupt.py](developer/02_mock_interrupt.py) | 挂起模型、拒绝旧命令、持物停止、替换任务 | async，故障验证 |
| 9 模型接入 | [developer/03_glm_adapter.py](developer/03_glm_adapter.py) | 查看 GLM 原生响应怎样变成计划 | async，默认离线 HTTP 夹具 |

先运行：

```powershell
./scripts/run.ps1 examples/beginner/01_action.py
./scripts/run.ps1 examples/beginner/02_skills.py
./scripts/run.ps1 examples/tasks/01_parallel_and_stop.py
./scripts/run.ps1 examples/tasks/02_cache_reuse.py
```

调用 `action()` 后节点开始独立执行，脚本可以接着做别的事；`wait()` 才等待终态。先提交多个动作、之后分别等待，就能让不同节点并行。`cancel()` 返回取消受理结果，`wait()` 确认动作终态；等待超时不等于已经停止。

Task 是一次确定计划的执行；Skill 是能力名称，Action 是这次能力调用。只做一次动作时不用组装 Task、Planner 或客户端。三个 developer 示例的执行顺序、运行命令和输出解读见 [导读第 9 节](../docs/getting_started.md#developer-examples)。其中的 async 用来明确等待网络和控制路径，不是入门前提。

缓存演示通过独立 Agent 进程运行。默认 Mock 模型只识别已有的严格转移模板；其他输入仍返回固定 home 计划。它不理解任意自然语言，也没有调用 GLM。缓存成功节省的是 Mock 模型调用，计数为 1→1→2→3。

所有脚本默认无付费模型调用、无真实音频、无实机。WRS 示例只是虚拟 FK 验证，pick/place 仍只在 Mock 中演示。进入目录不会运行模块，也没有新依赖或自动加载机制。

原平铺 01/02 移入 developer，03/05/10 移入 tasks，09 移入 beginner，04 移入 developer。旧路径不再保留转发脚本，完整映射和接口迁移见 [API 记录](../docs/api_simplification.md)。

<a id="connect"></a>

连接已有节点：两个终端先在各自环境变量中设置同一个 `WRS_AGENT_TOKEN`（16–128 字符），不要保存到代码或 TOML。

终端一启动服务，保持运行：

```powershell
./scripts/run.ps1 -m wrs_agent launch --bindings configs/tts.toml --env-id arm01
```

终端二调用：

```powershell
./scripts/run.ps1 examples/tasks/04_connect.py
```

这里仅启动本机 router + Mock TTS；没有 Agent、机器人、真实音频或模型请求。
调用程序退出后，TTS 仍在线；可以再次运行同一例子。终端一 Ctrl+C 才清理其拥有的服务。
节点启动后，Zenoh 会通知已连接的程序；只接受 TOML 中已配置的节点，不自动加载新 Python 技能。
连接示例由真实子进程集成测试运行；单独运行前需按以上步骤准备服务。
