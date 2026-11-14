"""UI node: a loopback web dashboard that operates the other nodes through Zenoh."""

import asyncio
from typing import Literal

from pydantic import Field

from wrs_agent.nodes.node import Node
from wrs_agent.nodes.ui.control import Dashboard
from wrs_agent.nodes.ui.server import DashboardServer
from wrs_agent.schemas import Boundary
from wrs_agent.system import System


class UiOptions(Boundary):
    host: Literal["127.0.0.1", "localhost"] = "127.0.0.1"
    port: int = Field(default=8010, ge=0, le=65535)
    # Host the WRS 3D page from this node's own snapshots; otherwise embed viewer_url.
    viewer: bool = True
    viewer_port: int = Field(default=8000, ge=1, le=65535)
    viewer_url: str | None = Field(default=None, max_length=200)


class UiNode(Node):
    """Owns no robot or model: every button becomes a request to a discovered node."""

    node_type = "ui"
    options_type = UiOptions
    launch_options = {"port": "ui_port", "viewer": "ui_viewer", "viewer_port": "ui_viewer_port"}
    features = ("ui.dashboard",)

    async def setup(self):
        system = System(self.transport, peers=self.peers, registry=self.discover())
        options = self.options
        viewer_url = options.viewer_url or f"http://127.0.0.1:{options.viewer_port}/"
        self.dashboard = Dashboard(
            system, node_id=self.node_id, endpoint=self.endpoint, env_id=self.env_id,
            viewer_url=viewer_url,
        )
        self.server = DashboardServer(
            self.dashboard, asyncio.get_running_loop(), host=options.host, port=options.port,
        )
        self.server.start()
        self.on_close(self.server.close)
        self.on_close(self.dashboard.close)
        self.spawn(self.dashboard.watch())
        if options.viewer:
            from wrs_agent.nodes.ui.scene_view import SceneView

            view = SceneView(self.dashboard, options.viewer_port)
            self.on_close(view.close)
            self.spawn(view.run())
        print(f"dashboard {self.server.url}", flush=True)
