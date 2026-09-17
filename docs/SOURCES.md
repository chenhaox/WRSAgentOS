# 官方资料与核查边界

资料核查日期：2026-09-17。链接用于追溯设计依据，不代表这些库在用户目标设备上已经通过兼容性或性能验证。库的 API 与版本可能继续变化，M0/M1 应针对实际安装版本复查。此计划的架构、阶段和验收属于本项目设计，不是声称各参考系统均已实现这些功能。

## S01 · WRS 公开上游

`https://github.com/chenhaox/WRS2`

已核对用户指定 WRS2 fork：Python >=3.12、setuptools 包装、WGPU 查看器；已固定提交与实际接口审计见 WRS_AUDIT.md。

## S02 · Zenoh Python 官方仓库与 API

`https://github.com/eclipse-zenoh/zenoh-python`

`https://zenoh-python.readthedocs.io/en/latest/api_reference.html`

官方包名为 eclipse-zenoh；Python 接口提供 pub/sub、query/queryable 及 QoS。查询回复、优先级、回调等细节应按锁定版本核对；API 有 QoS 并不构成 Python 或机器人硬实时保证。

## S03 · Zenoh 接入与访问控制

`https://zenoh.io/docs/getting-started/first-app/`

`https://zenoh.io/docs/manual/access-control/`

作为显式连接和保护配置的依据。不能把 topic 路径或消息内自报 source 当成认证。V1 双机部署必须验证实际启用的规则。

## S04 · Git submodule 官方语义

`https://git-scm.com/docs/gitsubmodules`

`https://git-scm.com/docs/git-submodule`

父仓库 gitlink 记录具体 commit，.gitmodules 记录路径/远程等信息。需要真正的 gitlink，而不是一个同名目录或仅手写配置。

## S05 · OpenAI：Codex 项目说明与执行计划

`https://developers.openai.com/codex/guides/agents-md`

`https://developers.openai.com/cookbook/articles/codex_exec_plans`

前者描述 Codex 的 AGENTS.md 发现规则；后者提供将长任务组织成可维护、可验证执行计划的做法。本任务包采用简短项目规则与分阶段计划，不要求用户提供全部对话历史。

## S06 · GLM/Z.AI 工具调用与 SDK 兼容入口

`https://docs.z.ai/guides/capabilities/function-calling`

`https://docs.z.ai/`

工具调用包含函数名、JSON 参数及调用 ID；官方文档提供 SDK 与 OpenAI 兼容接入。GLM 实际端点/模型需以用户账户为准，不硬编码从页面上看到的模型名。

## S07 · OpenAI 原生工具调用

`https://developers.openai.com/api/docs/guides/function-calling`

后续 GPT 适配应正确处理原生 Responses 的工具建议/结果和响应状态，不能简单把 GLM 的 base_url 换一下就宣布完全兼容。

## S08 · Claude 原生工具调用

`https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview`

Claude 客户端工具使用 tool_use/tool_result 等结构。Provider 适配负责格式；机器人执行权仍由本项目 Runtime/Environment 管理。

## S09 · HoloAgent

`https://github.com/HorizonRobotics/HoloAgent`

官方描述 Embodied AgentOS、3D Spatial Memory、Embodied Skills，以及受监控技能图的闭环执行。V1 只吸收技能和状态反馈思想，不引入完整运行时与机器人全栈。

## S10 · RPent

`https://github.com/RLinf/RPent`

`https://rpent.readthedocs.io/en/latest/rst_source/development/architecture.html`

官方系统描述规划、工具与独立环境连接的分离。V1 借鉴边界与闭环恢复，不把研究框架作为自己的底层依赖。

## S11 · DimOS

`https://github.com/dimensionalOS/dimos`

公开示例体现 Module、In/Out、RPC 与 blueprint 的模块组合。V1 保留常驻能力节点和显式输入输出，不复制完整装配系统。

## S12 · Python asyncio 的并发边界

`https://docs.python.org/3/library/asyncio-dev.html`

外部线程调用 asyncio 需要线程安全桥接，阻塞工作不应占用事件循环。这不能解决底层设备的停止语义，需要设备接口独立验证。

## S13 · sherpa-onnx

`https://github.com/k2-fsa/sherpa-onnx`

官方项目支持本地流式/非流式语音识别、VAD、关键词检测等；作为可选语音组件候选。具体中文模型权重、许可、时延和准确率尚未替用户验证。

## S14 · sounddevice

`https://python-sounddevice.readthedocs.io/en/latest/examples.html`

官方提供音频流和 asyncio 相关例子，用于采音/播报边界参考；不直接在音频回调中做网络调用和重计算。

## S15 · WRS 开发说明

`https://github.com/chenhaox/WRS2/blob/2bb014b747833c2fd9345115fbe26ffb11376f20/docs/API_INDEX.md`

作为源码审计入口之一。真正的接口与可取消能力必须以用户锁定提交中的代码和测试为准。

## S16 · uv 依赖管理

`https://docs.astral.sh/uv/concepts/projects/dependencies/`

用于 core、开发、GLM、语音等依赖分组及可复现安装。锁文件由工具生成，不把计划文本中的占位值当成可用锁文件。
