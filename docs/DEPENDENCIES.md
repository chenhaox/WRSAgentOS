# 依赖与 Git submodule 决策

2026-10-13 用户明确同意非商业研究用途后，已取得 GraspNet 官方 RealSense checkpoint：
12,468,415 字节，固定 Drive ID 1hd0G8LN6tRpi4742XOTEisbTXNZ-1jmk，模型与许可存于
.local/models/graspnet。首次本地 SHA256 为
`60680087c61cba2b6791614fef1519071e294f6dcaf99b3f581bb95f7c51a868`；上游没有公开
SHA256，此值只固定首次下载内容及后续一致性，不冒称官方哈希。版本、URL、许可来源
和哈希性质记录于 `wrs_agent/nodes/grasp/assets.json`（加入包资源），下载是独立显式
入口，节点启动不下载。已用 weights_only=True 加载并完成香蕉点云训练模型推理。
本次没有增加 SDK 依赖或改变环境版本；权重确认阻塞已解除。

2026-10-13 GraspNet 原生扩展：新增独立 graspnet extra，Torch 2.9.0 CU128、NumPy、
Ninja 1.13.2、setuptools 84.0.0、wheel 0.48.0；uv 工具生成 152 包锁文件。
固定基础解释器创建 .local/venvs/graspnet，共享/语音/vision 环境不变。官方源码固定
280c215129f759ed8649cb4e89fc5dfee55f4f80，位于 .local/src，非 submodule。
setup_graspnet.ps1 -BuildExtensions 准备本地 CUDA 12.8.1 的 nvcc/cudart/cccl，
依据 NVIDIA redistrib_12.8.1.json 中的官方大小/SHA256 校验；不安装驱动或修改系统。
MSVC 14.44 编译独立扩展副本，修正 Windows KNN 索引宽度、头文件/API 和编译参数；
模型源码保持干净。9 项算子数值检查与随机初始化网络前向通过；训练权重随后完成上节验证。
GraspNet 官方源码许可限非商业内部研究，构建代码/二进制不进 Git；本地保留 LICENSE。
具体入口和限制见 [视觉抓取](VISUAL_GRASP.md)。

同帧 RGB-D 检查追加：graspnet extra 显式列入 SciPy 与 Pillow，读取官方 meta.mat
和 RGB/深度 PNG，不依赖共享解释器碰巧安装的库。uv 重新生成锁仍为 152 包；隔离
graspnet 环境实际新增 scipy 1.18.1、pillow 12.3.0，vision/语音环境不变。仅处理
已下载的固定示例文件，没有新增模型或数据下载。

2026-10-13 YOLO26 权重：按用户明确要求下载 Ultralytics assets v8.4.0 的 yolo26n.pt
到 `.local/models/yolo/`，5,544,453 字节，校验通过 GitHub 官方资产 SHA256
`9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef`。
许可/下载地址见 [CAMERA.md](CAMERA.md)。02_start_yolo 默认选用该文件，运行时不下载。
实际使用固定 Python 3.12.0 已安装的 Ultralytics 8.4.24、Torch 2.9.0+cu128 完成 CPU
单图推理；没有安装或升级任何依赖，不修改 pyproject/uv.lock 或现有 vision 环境。

2026-10-13 OpenCV 预览：camera extra 与 dev 增加 `opencv-python>=4.10,<6`，复用
原 YOLO 已锁定的 5.0.0.93，由 uv 0.12.15 重新生成真实锁文件（150 包）。
已按带哈希的导出文件增量安装到项目 `.local/deps`，共享 Python 的 OpenCV 4.10.0 未修改。
项目 OpenCV 实际报告 5.0.0 / WIN32UI；测试只调用像素转换和绘字，未弹窗、未连接设备。

2026-10-13 后续增加 SAM3/GraspNet 本地适配器，用户尚无模型环境/权重。未自动安装 SDK
或下载权重；core lockfile 不代表完整模型栈。独立环境及验证边界见
[VISUAL_GRASP.md](VISUAL_GRASP.md)。本阶段未新增依赖或修改锁文件。

2026-10-13 新增可选组 `camera`（NumPy/Pillow）、`realsense`（pyrealsense2
2.56.5.9235）、`yolo`（Ultralytics）。由 uv 生成锁文件；核心依赖仍不强制模型 SDK。
相机、YOLO、TTS 可以使用不同 Python 环境，通过 Zenoh 通信。安装与权重校验要求见
[相机与视觉播报](CAMERA.md)；SDK 安装成功不等于 D405 实机已验证。

## 1. 结论

V1 默认只有 `third_party/wrs` 一个 Git submodule。submodule 用来记录独立源码仓库的确定提交，不是“让系统看起来更模块化”的工具。父仓库的 gitlink 记录具体 commit；只写 `.gitmodules` 并不能创建真实 submodule。[S04]

HoloAgent、RPent、DimOS 是设计参考，不是本项目运行依赖。Zenoh 是确定采用的通信库，但 Python 绑定应作为软件包安装，不因使用它就克隆其 Rust 源码进项目。[S02]

## 2. 依赖清单

| 对象 | V1 引入方式 | 必需程度 | 说明 |
|---|---|---|---|
| 用户指定 chenhaox/WRS2 | `third_party/wrs` submodule | WRS profile 必需 | 固定 2bb014b747833c2fd9345115fbe26ffb11376f20；保留本地修改 |
| eclipse-zenoh | Python 包，真实 lockfile | 核心必需 | 固定已测试版本，核对当前 Python API |
| zenohd | 官方发行二进制，版本与校验值写清单 | 默认部署必需 | 不作为 submodule；只在回环地址启动本地 router |
| pydantic | Python 包，v2 系兼容版本由测试锁定 | 核心必需 | 消息/Skill 参数边界校验 |
| asyncio、sqlite3、dataclasses、graphlib、argparse、tomllib | Python 标准库 | 按使用需要 | 不为同样的基础功能重复安装框架 |
| httpx | `llm` 可选依赖及 dev 夹具依赖 | 在线模型 profile 必需 | 0.28.1，直接映射 OpenAI Chat/Responses 与 Anthropic Messages 三种 HTTP 协议；不引入自动工具 Runner |
| openai、anthropic、litellm、langchain | 不引入 | V1 非必需 | 三种协议由本项目的小型适配直接收发 JSON；Claude 走原生 Messages 格式，不假设与 Chat 同构 |
| sounddevice、sherpa-onnx | `speech` 可选依赖组 | 真实语音 profile 必需 | 软件包而非 submodule；锁定与目标设备兼容的版本 |
| ASR/KWS/VAD/TTS 权重 | 显式资产清单与本地缓存 | 按语音 profile | 固定 revision、checksum、来源、体积和授权；不放主 Git，不自动下载 |
| numpy、WGPU、MuJoCo、WRS 相关依赖 | 独立 WRS 环境或可选组 | WRS profile 按需 | 不把 README 中所有可选依赖灌入 core |
| pytest、pytest-asyncio、ruff | 开发依赖组 | 开发必需 | 排除 third_party；不要重格式化 WRS |
| HoloAgent/RPent/DimOS | 文档、源码局部阅读 | 设计参考 | 本地忽略的 `.references/`，不递归安装全套依赖 |
| LeRobot/openpi/其他 VLA 平台 | 未来独立模型服务或可选包 | V1 不引入 | 真正要维护源码 fork 时再评估 submodule；权重与代码分开 |
| `wrs-skills` 独立技能仓库 | 未来可选 submodule/包 | V1 不拆 | 仅在多个项目共享、独立版本发布时才值得拆库 |

常规包通过 pyproject 与工具生成的 uv.lock 固定。具体版本必须在目标环境实际解析和测试，本文不伪造“兼容最新版”或已测试版本号。uv 支持依赖与可选依赖管理；lockfile 应由工具生成。[S16]

## 3. WRS 的重要现状

指定 WRS2 fork 具有标准 setuptools pyproject.toml，要求 Python >=3.12，采用 WGPU 查看器；不适用早期 WRS 的归档/无包装假设。[S01]

M0 必须首先识别用户自己的 WRS 分支。若已有工作拷贝，不要覆盖，不要强行改 upstream URL，不要擅自把未提交修改搬进 submodule。新项目中可以引用用户已维护的远程；以后要修复 WRS，应在受控 fork 中修改并提交，然后单独更新父仓库 gitlink。

WRS 的 Python 导入与 Git submodule 是两件事。若目标 commit 有正确 pyproject/setup.py，才使用 editable 安装；否则仅在 WRS Environment 的启动进程使用明确的 PYTHONPATH 或受控安装包装。不在十个模块中散落 sys.path.append，不污染系统 Python，不给 core 测试强制导入 WGPU。

如果 `wrs/__init__.py` 会导入大量可选组件，应根据真实源码决定最小安装或在用户 fork 中做小型兼容修复。不得在主仓库偷偷重写第三方源码并不记录补丁。

## 4. submodule 初始化合同

本轮已创建并固定真实 submodule；下面用于审查/复现，不重复 add。

    git status --short
    git submodule status
    git remote -v

用户已指定唯一远程，当前真实 gitlink 可直接检查：

    git -C third_party/wrs remote -v
    git -C third_party/wrs rev-parse HEAD
    git -C third_party/wrs status --short
    git ls-files --stage third_party/wrs

固定地址为 `https://github.com/chenhaox/WRS2.git`，提交为
`2bb014b747833c2fd9345115fbe26ffb11376f20`。

预期最后一条显示模式 `160000` 和实际 SHA。父仓库提交时应包含 `.gitmodules` 与 gitlink；版本提交和 push 需用户授权；2026-09-17 用户已指定父仓库 https://github.com/chenhaox/WRSAgentOS.git 并要求版本管理和上传排除。助手指令、本地计划、reports、IDE 配置和真实凭据不进入提交。

复现：

    git clone --recurse-submodules https://github.com/chenhaox/WRSAgentOS.git
    git submodule update --init --recursive

不要在 bootstrap、CI 或每次启动里执行 `git submodule update --remote`。升级 WRS 应作为独立变更：取新提交、审计差异、运行 core 与 WRS 验收、更新锁定记录。[S04]

## 5. 常规依赖与多环境

项目要求 Python 3.12。本机开发验证使用 `D:\code\venv312\.venv\Scripts\python.exe`（3.12.0），这个绝对路径不是其他机器的运行要求。Windows 的 run.ps1/bootstrap.ps1 默认调用当前 `python`；如需明确选择，设置 `$env:WRS_AGENT_PYTHON = "本机 Python 3.12 的路径"`。

普通虚拟环境中的 Runtime 子进程继承 `sys.executable` 并正常加载该环境的 site-packages。使用 `python -X utf8 -S scripts/run.py ...` 时，子进程保留相同的开发隔离模式，通过 scripts/run.py 加载项目 .local/deps。WRS adapter 通过标准库 site 获取当前平台的科学包路径，仅追加路径，不执行 .pth。版本见 WRS_AUDIT.md 和 reports/doctor.json。

    ./scripts/bootstrap.ps1
    ./scripts/install_router.ps1
    ./scripts/run.ps1 scripts/doctor.py --probe-wrs

Router 查找顺序是 `WRS_AGENT_ZENOHD` 指定的文件、仓库 `.local/zenoh-1.9.0/zenohd.exe`（Windows）或 `zenohd`（POSIX）、系统 PATH 中的 zenohd。显式路径无效时直接报错，不回退到另一份程序；启动前始终核对版本 1.9.0。可执行文件路径含空格时也作为一个独立参数传递。

PowerShell 安装脚本只覆盖 Windows x64。Linux/macOS 需准备对应平台的依赖和 Router；启动器已使用 POSIX 文件锁，但这些平台尚未实际验收。当前仍以源码 checkout 运行，configs、scripts 与 .local/runs 位于仓库中，未验证独立 wheel 部署。

bootstrap 用项目本地 uv 0.12.15 从 uv.lock 导出带哈希 requirements，再同步至 .local/deps。router 固定官方 1.9.0 并校验 SHA256。脚本可重跑，不更新 WRS 远程指针、不修改共享环境。

当前已实现 core/dev/llm（2026-09-23 由 glm 改名）；httpx 同时列入 dev 以运行无网络的协议夹具。部署仅 core 不含 httpx。speech 在真实接入时加入 pyproject 并重新生成锁文件。没有空 extras、默认付费调用或权重下载。表中未接入项仍是后续合同。

## 6. 什么时候才增加第二个 submodule

至少满足以下实质条件之一，并写决策记录：需要长期修改第三方源码；需要独立维护与本项目协同的 fork；依赖无法通过正常包/服务使用，且确实属于运行时必要组件。

“论文里用了”“学生需要看源码”“仓库看起来重要”都不是理由。用于复现的 RPent/HoloAgent 应独立环境运行；核心项目只保存参考路径、commit 与借鉴点，避免整套依赖互相污染。

参考仓库中的 Skill 文档不能不经审查自动成为模型的高权限指令。少量源码复用记录来源与授权。机器人模型网格、数据和模型权重的授权可能不同于顶层代码许可证，应分别登记；本文件不是对未审计资产的商用授权承诺。

2026-09-17：GLM 使用 httpx 异步客户端直接发送 OpenAI 兼容 JSON；原计划 openai SDK 为候选，未引入。可运行 `./scripts/bootstrap.ps1 -Extra glm`；锁文件由 uv 0.12.15 重新生成，不修改共享 Python。只有非流式/auto 工具模式经离线协议测试，真实账号模型及 GLM TTS 未验证。

2026-09-23：模型适配由 GLM 专用改为按线协议通用（openai_chat、openai_responses、anthropic_messages），变量统一为 `LLM_*`。extra `glm` 改名 `llm`，依赖内容不变（仍只有 httpx >=0.28,<0.29）；uv.lock 由项目本地 uv 0.12.15 `uv lock` 重新生成，差异只有 extra 名称三行。安装命令改为 `./scripts/bootstrap.ps1 -Extra llm`。没有新增厂商 SDK。三种协议仅经手写离线夹具测试，未对任何真实账号联网验证。

M3 科学包路径补充：指定 venv 的 pyvenv.cfg 设置 include-system-site-packages=true。WRS adapter 仅在自己的进程向 sys.path 后部追加该 venv 与基础解释器的 site-packages；项目锁定 core 包优先，固定 WRS submodule 位于前部并检查真实模块路径。不执行 .pth、不升级或修改共享包。M3 干净机器科学依赖重建仍未验证。

## Qwen 语音可选依赖

FastSAM 权重准备（2026-10-13）：复用已有 yolo extra/uv.lock，Ultralytics 8.4.175、
Torch 2.9.0+cu128、Torchvision 0.24.0+cu128。在 `.local/venvs/vision` 实际安装 46 包并
通过 pip check；没有新增依赖约束或手改锁文件。`setup_vision.ps1` 只安装 SDK，
`download_fastsam.py --download` 显式下载固定 v8.4.0 的 FastSAM-x 并核对官方 SHA256。
模型/许可来源见 nodes/segmentation/fastsam_assets.json；权重不进 Git，不需要 SAM3
的 Meta 授权。该阶段只验证了真实图像/框提示；后续节点接入见下段。

FastSAM 后端接入（2026-10-13）：新增 fastsam extra，Ultralytics 沿用已锁定 8.4.175，
OpenAI CLIP SDK 固定 Git 提交 d05afc436d78f1c48dc0dbf8e5980a9d471f35f6（MIT），
普通可选包而非 submodule。uv 实际生成 150 包锁文件；vision 环境现为 52 包，
pip check 通过。setup_vision.ps1 使用固定基础解释器、--locked --extra fastsam；
共享/语音环境不变。CLIP ViT-B/32 复用已存在的本机缓存，复制前后核对官方 SHA256，
未发起该权重下载；清单为 nodes/segmentation/clip_assets.json。节点仅加载显式本地
文件且关闭 SDK 自动安装/下载。FastSAM/CLIP 模型均实际执行，但文本定位实图核对
失败（领带误选衬衫）；这项质量限制见 VISUAL_GRASP.md，不能用 SDK 运行成功替代。

YOLO-World → FastSAM 框提示（2026-10-13）：复用本机已有 yolov8m-worldv2.pt，与官方
Ultralytics assets v8.4.0 SHA256 核对一致，复制到 .local/models/yolo；没有新下载。
大小 57,233,192 字节，SHA256 b614d33aa35b8e61d988041ff6939dfb3ed627af88ccaf643e4cdb822eb41d71，
发布 asset 340060299。来源/AGPL-3.0 记录在 nodes/vision/assets.json，并列入包数据。
沿用现有 Ultralytics/CLIP 和 vision 环境，lock --check 150 包、pip check 52 包通过，
无新增依赖/环境变更。YOLO-World 的文字编码器显式加载本地已校验 CLIP；FastSAM 框
提示进程不加载 CLIP。CUDA Toolkit 尚未准备；vswhere 实测 VS 2022 Community 的 C++
组件已安装，未在当前 PATH 找到 cl 不能推断缺少 MSVC。本轮没有安装编译器或 Toolkit。

SAM3 环境补充（2026-10-13）：`sam3` extra 使用 Transformers 5.15.1、Torch 2.9.0、
Torchvision 0.24.0，Windows/Linux 从官方 CU128 索引解析 Torch/Torchvision。与 qwen-asr
的 Transformers 4.57.6 互斥，`scripts/setup_sam3.ps1` 使用固定基础解释器创建独立
`.local/venvs/sam3`，不修改共享或语音环境。uv 实际重新解析锁定 147 个包，隔离环境
安装 43 个包并通过 `uv pip check`。该 extra 不下载权重；SAM3 授权、权重可信哈希和
真实推理、GraspNet CUDA 扩展仍未验证，详见 [视觉抓取](VISUAL_GRASP.md)。

实际选用 Qwen3-ASR-0.6B、Qwen3-TTS-12Hz-0.6B-CustomVoice；qwen-asr 0.0.6 与 TTS 侧 faster-qwen3-tts 0.4.0 的 Transformers 精确依赖冲突（4.57.6 与 5.15.1），通过互斥 uv extras 与两个 .local/venvs 环境隔离。Torch/Torchaudio 2.9.0 CU128；核心默认依赖不增加模型 SDK。固定模型资源、校验与安装入口见 [Qwen 语音指南](QWEN_SPEECH.md)。普通 pip 也可分别安装两个 extra，但复现版本以 uv.lock 与 setup_speech.ps1 为准。


2026-09-23：TTS 后端由官方 qwen-tts 0.1.1 换为 faster-qwen3-tts 0.4.0（MIT，CUDA graph 加速），实测短句提速 4.8 倍、41 字长句 6.4 倍，详见 [Qwen 语音指南](QWEN_SPEECH.md)。它硬依赖 `qwen-tts-hf` 0.1.1.post1 顶替官方 `qwen-tts`（同名 `qwen_tts` 包不可共存）并要求 Transformers 5.x；Transformers 固定 5.15.1，因为 5.17.0 会在其 rope 兼容层对 Mimi codec 配置抛 AttributeError。变更限于 `.local/venvs/qwen-tts`，ASR 的 Transformers 4.57.6 与共享环境不受影响；uv.lock 由工具重新生成，`uv pip check` 93 包全部兼容。`qwen-tts-hf` 自述为非官方临时兼容构建，PyPI 仅一个版本、无授权声明、无仓库链接，作者字段沿用上游而非实际维护者；接受此来源风险是用户的明确决定，登记在此以备复核。

2026-09-22：用户明确要求将 ASR 装入已有 `D:\code\venv312\.venv`。使用 uv pip install 增量安装 qwen-asr 0.0.6、torchaudio 2.9.0+cu128、sounddevice 0.5.6，并约束保留已有可见依赖；未对该共享环境运行 sync。Transformers 4.57.6 要求 huggingface-hub<1，因此仅将已有 hub 1.3.2 调整为 0.36.2，其余原包保留。SDK/语音依赖仍在 pyproject 的 qwen-asr extra 中；默认 setup_speech.ps1 继续提供隔离安装，TTS 使用原独立环境。共享环境已有两项与此次无关的缺失：joycon-robotics 的 hid 与 scipy-stubs 的 optype；安装后未新增依赖检查错误。
