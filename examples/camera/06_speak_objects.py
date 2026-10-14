"""播报识别到的物体：先启动 00、01、03、04、05；02 预览可选。"""

import asyncio

from examples._session import use_local_token
from wrs_agent import System, step

ENDPOINT = "tcp/127.0.0.1:7449"
ENV_ID = "wrs-demo"
SESSION = "wrs"
GOAL = ""  # 留空使用下方固定步骤；填写目标时，需要已有 Agent 显式启用在线 Planner。


async def main():
    use_local_token(SESSION)
    async with System.connect(ENDPOINT, env_id=ENV_ID) as system:
        for node, script in (("agent", "05_start_agent.py"), ("yolo", "03_start_yolo.py"),
                             ("tts", "04_start_tts.py")):
            try:
                await system.registry.wait_for(node, timeout=5)
            except TimeoutError:
                raise SystemExit(
                    f"未发现就绪的 {node} 节点，请先运行 {script} 并保持运行。"
                    f"各脚本的 ENDPOINT / ENV_ID / SESSION 需一致（{ENDPOINT}，{ENV_ID}）。"
                ) from None
        if GOAL:
            # 仅使用已显式启用 live_model 的 Agent；本脚本不会自动启用模型调用。
            goal = await system.goal(GOAL)
            planned = await goal.wait(timeout=45)
            print("规划结果：", planned.state)
            if planned.task is None:
                raise RuntimeError(planned.reason)
            result = await planned.task.wait(timeout=45)
        else:
            see = step("describe_objects")
            say = step("speak", after=see,
                       inputs={"text": {"step_id": see.step_id, "field": "text"}})
            task = await system.start(see, say)
            result = await task.wait(timeout=45)
        print("任务结果：", result.state)
        if result.state != "SUCCEEDED":
            raise SystemExit(f"播报未完成：{result.reason}")
        print("播报文本：", (await system.snapshot("tts")).data.last_text)


if __name__ == "__main__":
    asyncio.run(main())
