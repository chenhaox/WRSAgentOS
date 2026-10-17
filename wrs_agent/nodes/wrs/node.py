"""Robot node: the Environment remains the only robot action owner."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from wrs_agent.nodes.node import Node
from wrs_agent.nodes.options import Duration
from wrs_agent.schemas import Boundary, Name


class WrsOptions(Boundary):
    backend: Literal["mock", "wrs", "wrs-grasp", "fafu"] = "mock"
    duration: Duration = 0.4
    scene: Path | None = Field(default=None, strict=False)
    grasp_calibration: Path | None = Field(default=None, strict=False)
    grasp_node: Name = "graspnet"
    sdk_root: Path | None = Field(default=None, strict=False)
    cfg_path: Path | None = Field(default=None, strict=False)
    port: str = Field(default="auto", min_length=1, max_length=80)
    allow_hardware: bool = False
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
        if self.grasp_calibration is not None and self.backend not in {"wrs-grasp", "fafu"}:
            raise ValueError("grasp_calibration_requires_visual_backend")
        if self.backend == "fafu" and self.grasp_calibration is not None:
            from wrs_agent.env.fixed_camera import load_calibration

            if load_calibration(self.grasp_calibration).source != "measured":
                raise ValueError("fafu_hand_eye_requires_measured_calibration")
        if self.backend != "fafu" and (self.sdk_root is not None or self.cfg_path is not None
                                        or self.allow_hardware or self.port != "auto"):
            raise ValueError("fafu_options_require_fafu_backend")
        if self.backend == "fafu" and self.allow_hardware:
            root = self.sdk_root
            if root is None or not (root / "fafu_robot_python" / "fafu_robot_controller.py").is_file():
                raise ValueError("fafu_sdk_not_found")
            cfg = self.cfg_path or root / "fafu_robot_python" / "robot.cfg"
            if not cfg.is_file():
                raise ValueError("fafu_config_not_found")
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
        switch_mode = None
        if options.backend == "fafu":
            from wrs_agent.env.fafu import make_fafu_environment

            read_grasps_fn = None
            if options.grasp_calibration is not None:
                from wrs_agent.nodes.grasp.data import read_grasps

                self.discover()

                async def read_grasps_fn(candidate_set_id, task_id):
                    await self.registry.refresh([options.grasp_node])
                    return await read_grasps(
                        self.connect(options.grasp_node), candidate_set_id, task_id,
                    )

            executor, switch_mode = await make_fafu_environment(
                self.journal,
                sdk_root=options.sdk_root,
                cfg_path=options.cfg_path,
                port=options.port,
                allow_hardware=options.allow_hardware,
                calibration_path=options.grasp_calibration,
                read_grasps=read_grasps_fn,
            )
        elif options.backend == "wrs-grasp":
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
        if switch_mode is not None:
            self.transport.register_handler("request/robot/mode", switch_mode)
