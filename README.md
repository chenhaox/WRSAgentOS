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

第二个示例演示并行动作、挂起的 Mock 模型等待、只取消 TTS、持物停止、拒绝旧动作/旧模型结果，以及重新规划到 C。结束时清理自己启动的进程。前台持续运行用 `./scripts/run.ps1 -m wrs_agent launch`，Ctrl+C 停止。

实际验证：44 项单元测试、6 项真实 Zenoh 集成测试、两个示例、Ruff 和 doctor 通过。verify.py 会在本地 reports/ 生成验收结果。开发助手指令、执行计划、IDE 配置和机器报告保留本地，不提交远程。

通信只有三种语义：

- Event/Stream：发布事实和进度，多订阅者直接接收；事件可能丢失，状态查询补偿。
- Query：能力、状态等短请求；控制服务有独立有界入口。
- Action：快速 ACCEPTED，随后进度/终态、按 ID 查询和取消；WRS/TTS 共用同一合同。

每节点最多一个资源动作，最多 12 步任务、16 项追加任务、4096 条会话动作/控制记录；达到容量明确拒绝，不删除去重历史再重放。动作 ID、任务 revision、节点 boot_id/epoch、短期授权和停止确认各自独立。

默认只连接回环地址，关闭发现；凭据由启动器生成，经环境变量传给自己的子进程，不靠 source/category/节点名称授予权限。该配置仅用于受信本机 Mock，远程身份绑定与 ACL 尚未实现。网络拥塞/查询丢失返回错误或超时，不自动重发物理动作。

WRS submodule 固定到用户指定 [chenhaox/WRS2](https://github.com/chenhaox/WRS2) 的 `2bb014b747833c2fd9345115fbe26ffb11376f20`。真实 WRS import 和 Lite6 FK 已通过；详情见 [docs/WRS_AUDIT.md](docs/WRS_AUDIT.md)。本任务未修改原有本地 WRS2 工作拷贝。

尚未完成：WRS 连续虚拟运动适配、GLM 原生服务接入、条件缓存与有限恢复、独立 Vision 节点、真实 ASR/TTS/麦克风、双机保护配置和负载基准。挂起测试没有调用 GLM。所有动作和播报结果均为 Mock；实机后端不可选择。
