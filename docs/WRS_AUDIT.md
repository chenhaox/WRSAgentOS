# WRS 与环境审计（2026-09-17）

解释器：`D:\code\venv312\.venv\Scripts\python.exe`，Python 3.12.0，Windows 11。core 经 scripts/run.ps1 使用项目独立依赖；WRS 探测子进程使用同一解释器及用户已有科学依赖，未安装/升级共享环境。

唯一远程：`https://github.com/chenhaox/WRS2.git`。固定 commit：
`2bb014b747833c2fd9345115fbe26ffb11376f20`。真实 gitlink 模式 160000，见 reports/doctor.json。submodule 无本任务修改。

初次审计本地 `D:\code\ch\WRS2` 的 origin 相同，HEAD 为 `5cbe75e829d8aa28701a0ad25be036e80c25320a`，有多项未提交修改。本任务仅读取该目录。向公开远程 fetch 此 SHA 返回 `not our ref`，故没有搬运不可还原提交或未提交文件；采用公开远程可取得的固定提交。父仓库原先未初始化 Git，现已初始化，未自动提交或发布。

| 项目 | 固定提交中的实际证据 | 本轮结果 |
|---|---|---|
| 包装 | pyproject.toml，setuptools，Python >=3.12 | 已审阅；未执行全量 editable 安装 |
| 最小导入 | wrs/__init__.py；本项目 environments/wrs.py 中的 probe | PASS，未打开 viewer |
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
