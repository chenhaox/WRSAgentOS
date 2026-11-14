"""看板 http://127.0.0.1:8011/ ，3D 画面 8002；和 wrs/05 的 8010 / 8000 互不占用。"""

import asyncio

from examples._session import use_local_token
from wrs_agent.nodes.serve import serve_node
from wrs_agent.nodes.ui import UiNode

if __name__ == "__main__":
    use_local_token("voice")
    print("看板 http://127.0.0.1:8011/ ，画面 http://127.0.0.1:8002/ 。", flush=True)
    try:
        asyncio.run(serve_node(
            UiNode, endpoint="tcp/127.0.0.1:7451", env_id="voice-goal",
            options={"port": 8011, "viewer_port": 8002},
        ))
    except KeyboardInterrupt:
        pass
