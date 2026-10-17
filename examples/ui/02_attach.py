"""把 UI 节点接到已在运行的 examples/wrs/05_start_node.py 上；关闭看板不会停止其他节点。"""

import asyncio

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node
from wrs_agent.nodes.ui import UiNode

if __name__ == "__main__":
    use_local_token("wrs")
    print("看板 http://127.0.0.1:8010/ ，Ctrl+C 只关闭 UI 节点。", flush=True)
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
