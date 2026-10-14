"""Independent synthetic camera process for real Zenoh integration tests."""

import asyncio
import sys

from wrs_agent.nodes.camera import CameraNode
from wrs_agent.nodes.serve import serve_node

if __name__ == "__main__":
    asyncio.run(serve_node(
        CameraNode, endpoint=sys.argv[1], env_id=sys.argv[2],
        options={"width": 320, "height": 240, "fps": 15},
    ))
