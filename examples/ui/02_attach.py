"""把 UI 节点接到已在运行的 examples/wrs/05_start_node.py 上；关闭看板不会停止其他节点。

先在另一个终端留下 05。本进程只打开看板：可选厨具技能，也可在决策大脑里接大模型。
不要同时运行 07_viewer.py，它占用 8000 时看板会嵌进旧画面，看不到夹爪和技能动作。
"""

import asyncio

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node
from wrs_agent.nodes.ui import UiNode

if __name__ == "__main__":
    use_local_token("wrs")
    print("看板 http://127.0.0.1:8010/ ，画面在 http://127.0.0.1:8000/ 。", flush=True)
    print("Ctrl+C 只关闭 UI 节点，05 里的场景和 Agent 继续运行。", flush=True)
    try:
        asyncio.run(serve_node(
            UiNode,
            endpoint="tcp/127.0.0.1:7449",
            env_id="wrs-demo",
            # 07_viewer.py 若已占用 8000，UI 节点会直接嵌入它。
            options={"port": 8010, "viewer_port": 8000},
        ))
    except KeyboardInterrupt:
        pass
