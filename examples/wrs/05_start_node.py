"""启动独立 WRS 仿真节点与任务服务，供其他进程连接；Ctrl+C 关闭。"""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from examples._session import use_local_token
from wrs_agent import System
from wrs_agent.env.wrs import ROOT
from wrs_agent.layout_session import kitchen_session

CONFIG = Path(__file__).with_name("bindings.toml")

SCENE = ROOT / "wrs_agent/assets/kitchen/scene.toml"

async def main(live_model):
    with kitchen_session(SCENE) as runtime_scene:
        async with System.launch(
            backend="wrs", bindings=CONFIG, scene=runtime_scene,
            port=7449, env_id="wrs-demo", duration=1.0, live_model=live_model,
        ) as system:
            print("WRS 节点已上线：tcp/127.0.0.1:7449", flush=True)
            snapshot = await system.snapshot()
            print("动作准入：", snapshot.admission, flush=True)
            print("场景物体：", list(snapshot.data.objects), flush=True)
            print("摆放修改仅对本次运行有效；重新启动 05 后恢复默认。", flush=True)
            print(
                "另开终端运行 python examples/ui/02_attach.py 接看板。"
                "本窗口保持运行；看板里可以单步选技能，或在决策大脑里接大模型。",
                flush=True,
            )
            await asyncio.Event().wait()


if __name__ == "__main__":
    # 不依赖 IDE 的工作目录；显式环境变量优先于仓库 .env。Agent 子进程继承这些 LLM_*。
    load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
    live_model = bool(os.environ.get("LLM_BASE_URL", "").strip() and os.environ.get("LLM_MODEL", "").strip())
    if live_model:
        print(f"大模型规划已启用：{os.environ['LLM_MODEL'].strip()}（配置来自 .env）", flush=True)
    else:
        print("未配置 LLM_BASE_URL / LLM_MODEL，大模型规划未启用；看板里仍可手动填写。", flush=True)
    # 启动前准备共享口令，供 06_control_arm / 07_viewer 连接本服务。
    use_local_token("wrs", create=True)
    try:
        asyncio.run(main(live_model))
    except KeyboardInterrupt:
        pass
