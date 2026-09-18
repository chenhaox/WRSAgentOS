# WRS 与环境审计（2026-09-17）

解释器：`D:\code\venv312\.venv\Scripts\python.exe`，Python 3.12.0，Windows 11。core 经 scripts/run.ps1 使用项目独立依赖；WRS 探测子进程使用同一解释器及用户已有科学依赖，未安装/升级共享环境。

唯一远程：`https://github.com/chenhaox/WRS2.git`。固定 commit：
`2bb014b747833c2fd9345115fbe26ffb11376f20`。真实 gitlink 模式 160000，见 reports/doctor.json。submodule 无本任务修改。

初次审计本地 `D:\code\ch\WRS2` 的 origin 相同，HEAD 为 `5cbe75e829d8aa28701a0ad25be036e80c25320a`，有多项未提交修改。本任务仅读取该目录。向公开远程 fetch 此 SHA 返回 `not our ref`，故没有搬运不可还原提交或未提交文件；采用公开远程可取得的固定提交。父仓库原先未初始化 Git，现已初始化，未自动提交或发布。

| 项目 | 固定提交中的实际证据 | 本轮结果 |
|---|---|---|
| 包装 | pyproject.toml，setuptools，Python >=3.12 | 已审阅；未执行全量 editable 安装 |
| 最小导入 | wrs/__init__.py；本项目 env/wrs.py 中的 probe | PASS，未打开 viewer |
| 机器人类 | wrs/robots/manipulators/ 下 Lite6、UR3、FR3、RS007L、CVR038、CRX5IA、OpenArm | 源码枚举，不表示设备经过测试 |
| FK/状态 | robots/base/mech_base.py:101 的 fk(qs)、qs 和 link transforms | Lite6 构造/FK PASS，shape=[7,4,4]，6 个关节；虚拟状态 |
| IK | robots/base/mech_base.py:307 的 ik(...) | 已定位，数值求解 UNVERIFIED |
| 运动规划 | motion/probabilistic/rrt.py 的 RRTConnectPlanner.solve/solve_iter | 已定位，路径和碰撞验证 UNVERIFIED |
| 设备运动/读回 | 锁定版本模型与规划源码 | 未发现并验证可用实机入口；型号/地址未知 |
| stop/flush/夹持 | viewer/world.py:88 的 stop(function) 仅取消调度回调 | 不可当设备停车；控制器停止/清队列/物理确认 UNVERIFIED |
| 查看器线程 | viewer/world.py:125 的 run()，调用线程 tick、后台 publisher 采样 scene | WGPU/web viewer；未运行 viewer，不沿用 Panda3D 假设 |
| 授权来源 | 根 LICENSE，MIT，WRS Research Group 2026 | 代码许可证已读；网格/独立资产仍需逐项核查 |

实际探测：`./scripts/run.ps1 scripts/doctor.py --probe-wrs --output reports/doctor.json`。

已有 WRS 依赖：NumPy 1.26.4、SciPy 1.16.2、MuJoCo 3.5.0、WGPU 0.32.0、websockets 15.0.1。这些是已观察版本，不代表 WRS 全部传递依赖已锁定。干净机器的科学依赖恢复留待 M3。

core 的工具生成 uv.lock 锁定 Zenoh 1.9.0、Pydantic 2.13.5、pytest 9.1.1、pytest-asyncio 1.4.0、Ruff 0.16.8。uv 工具为 0.12.15。官方 zenohd 1.9.0 MSVC ZIP 的 SHA256：
`07af486bedd6e2138e187f277d4d8a74632ec1e7659f10dc76aa18a36b5d2a75`，
下载与校验脚本为 scripts/install_router.ps1。

本轮只证明 WRS 源码、导入与 FK 可用。M3 连续虚拟运动尚未实现；GLM、真实音频和实机全部 UNVERIFIED。CLI 只能选择 Mock 节点。

## 2026-09-17：router 版本检查误报

使用指定 Python 3.12.0 复核：本地 router 输出 `zenohd v1.9.0 built with rustc 1.93.0 (254b59607 2026-01-19)`；现有 ZIP 的 SHA256 与上述固定值一致。设置 `RUST_LOG=info` 或 `debug` 后，`--version` 会先向 stdout 写入带时间戳的 INFO 日志，然后才输出独立版本行。`LocalStack.start_router` 对整个输出执行 `startswith(b"zenohd v1.9.0 ")`，因此错误拒绝正确的二进制。

复现命令：`$env:RUST_LOG = 'info'; & 'D:\code\venv312\.venv\Scripts\python.exe' -X utf8 -S scripts/run.py examples/developer/02_mock_interrupt.py`，修复前退出码 1，证据 `reports/router_version_before.txt`。决策：从独立版本行提取完整版本号并严格比较 1.9.0，保留继承的日志设置；真实不匹配时报告期望版本、二进制路径和实际输出，不放宽到任意版本。

另观察到直接使用共享 site-packages 时 `eclipse-zenoh` 为 1.10.1；项目启动器加载 `.local/deps` 的锁定 1.9.0。两者与 router 版本检查误报分开处理，不修改共享环境。后续验证通过项目启动器执行；真实硬件、模型 API 和音频不在本修复范围内。

修复验证：指定解释器下新增 13 项版本检查单元测试及 2 项真实 router 集成测试通过。设置 RUST_LOG=info 执行当前完整单元集 92 passed、Zenoh 集成集 8 passed，0 failed / 0 errors / 0 skipped；两个示例、全仓库 Ruff、doctor 均 PASS，无阻塞检查。Mock 中断示例最终 SUCCEEDED，A 位于 C，旧动作拒绝 stale_epoch，迟到模型结果 STALE。实际命令、输出和依赖版本见 reports/router_version_fix.json、reports/router_fix_*.txt 和 reports/router_fix_doctor.json。共享环境未修改；WRS 连续虚拟动作、真实模型、音频和实机仍 UNVERIFIED。

## M3：已验证的 Lite6 虚拟节点

env/wrs.py 在独立 Environment 进程使用真实 Lite6、wmij.interp_by_n 和 robot.fk，单所有者线程操作模型，Zenoh/控制循环读取带时间、单位、frame/source 的镜像状态。验证关节到位与 FK 变换一致，默认 headless，不构造硬件或查看器。

capabilities / snapshot / observe / move_named_pose / status / cancel / hold / resume admission 已经真实跨进程验证。每次 FK 不可抢占：控制先撤权，等待在途 FK 返回后才确认停止；不对线程 future 执行 cancel 来冒充设备停车。没有控制器轨迹队列，controller_flush=false，stop_scope=virtual_fk_boundary。

固定源码有 robots/end_effectors/ee_mixins.py 的模型 hold/release、manipulation/arm.py 的抓取辅助接口；本 Lite6 profile 未装配夹爪、有效目标几何与碰撞校验，因此 pick/place/物体 verify 明确 unsupported。未把模型挂接当接触成功。RRT/IK 与 GUI 未接入动作路径，实机始终拒绝。

指定 venv 的科学依赖继承基础解释器 site-packages；core 不加载，WRS adapter 只读追加。完整科学依赖的干净安装仍未宣称可复现。测试/示例证据见 reports/m3.xml 和 reports/m3_example_cancel.txt。
