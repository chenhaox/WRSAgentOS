"""Robot node: the Environment remains the only robot action owner."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from wrs_agent.nodes.node import Node
from wrs_agent.nodes.options import Duration
from wrs_agent.schemas import Boundary, Name


class WrsOptions(Boundary):
    backend: Literal["mock", "wrs", "wrs-grasp"] = "mock"
    duration: Duration = 0.4
    scene: Path | None = Field(default=None, strict=False)
    grasp_calibration: Path | None = Field(default=None, strict=False)
    grasp_node: Name = "graspnet"
    fault: (
        Literal[
            "grasp",
            "grasp_once",
            "localization",
            "localization_once",
            "unknown",
            "inconclusive",
            "stop_unknown",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_scene(self):
        if self.scene is not None:
            if self.backend not in {"wrs", "wrs-grasp"}:
                raise ValueError("scene_requires_wrs_backend")
            from wrs_agent.scene import load_scene

            load_scene(self.scene)
        if self.backend == "wrs-grasp" and (self.scene is None or self.grasp_calibration is None):
            raise ValueError("visual_grasp_requires_scene_and_calibration")
        if self.grasp_calibration is not None and self.backend != "wrs-grasp":
            raise ValueError("grasp_calibration_requires_visual_backend")
        return self


class WrsNode(Node):
    node_type = "wrs"
    action_service = True
    launch_options = {
        "backend": "backend",
        "duration": "duration",
        "scene": "scene",
        "fault": "fault",
    }
    options_type = WrsOptions

    async def setup(self):
        options = self.options
        if options.backend == "wrs-grasp":
            from wrs_agent.env.wrs_grasp import make_visual_environment
            from wrs_agent.nodes.grasp.data import read_grasps

            self.discover()

            async def read_data(candidate_set_id, task_id):
                await self.registry.refresh([options.grasp_node])
                return await read_grasps(
                    self.connect(options.grasp_node), candidate_set_id, task_id,
                )

            executor = await make_visual_environment(
                self.journal, scene=options.scene, calibration_path=options.grasp_calibration,
                read_data=read_data,
            )
        elif options.backend == "wrs":
            from wrs_agent.env.wrs import make_wrs_environment

            executor = await make_wrs_environment(
                self.journal,
                duration=options.duration,
                scene=options.scene,
            )
        else:
            from wrs_agent.env.mock import make_mock_environment

            executor = make_mock_environment(
                self.journal,
                duration=options.duration,
                fault=options.fault,
            )
        self.actions(executor)
