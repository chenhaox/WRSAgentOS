"""FAFU real-arm node. Registers as the robot ("wrs"), so Agent and UI attach unchanged."""

import asyncio
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import Field

from wrs_agent.env.kitchen_motion import publish_kitchen
from wrs_agent.env.wrs import VirtualState
from wrs_agent.executor import ActionExecutor
from wrs_agent.nodes.fafu import handeye
from wrs_agent.nodes.fafu.arm import FafuArm
from wrs_agent.nodes.fafu.collision import CLASSES, load_catalog
from wrs_agent.nodes.fafu.plan import Planner
from wrs_agent.nodes.fafu.skills import RealSkills
from wrs_agent.nodes.node import Node
from wrs_agent.schemas import Boundary

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "fafu"


class FafuOptions(Boundary):
    # False plans and plays every skill on a model of the arm; the serial port stays closed.
    execute: bool = False
    config: Path = Field(default=ASSETS / "real.toml", strict=False)
    classes: Path = Field(default=CLASSES, strict=False)
    handeye: Path = Field(default=handeye.PATH, strict=False)


def load_config(path):
    with Path(path).open("rb") as source:
        return tomllib.load(source)


class FafuNode(Node):
    node_type = "wrs"
    action_service = True
    options_type = FafuOptions

    async def setup(self):
        options = self.options
        config = load_config(options.config)
        catalog = load_catalog(options.classes)
        matrix = handeye.load(options.handeye)
        arm = FafuArm(config, execute=options.execute, start=config["poses"]["home"])
        await asyncio.to_thread(arm.connect)
        self.on_close(lambda: asyncio.to_thread(arm.close))

        owner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fafu-plan")
        self.on_close(lambda: asyncio.to_thread(owner.shutdown, wait=True))
        loop = asyncio.get_running_loop()

        async def call(fn, *args):
            return await loop.run_in_executor(owner, fn, *args)

        planner = await call(Planner, config, catalog, matrix, arm.limits)
        kinematics = await call(planner.start, await asyncio.to_thread(arm.joints))
        state = VirtualState(kinematics, {})
        state.objects = {name: data.model_copy(deep=True) for name, data in planner.objects.items()}
        base = planner.model.base_pos
        state.facts.update(
            robot_model="fafu_baseV1", calibration="fafu-handeye-link6",
            robot_base_x=base[0], robot_base_y=base[1], robot_base_z=base[2],
            physics_enabled=False, collision_checked=True, hardware=options.execute,
            world_source="perception", detections=0, objects=0,
        )
        await call(publish_kitchen, planner.motion, state)
        self.discover()
        skills = RealSkills(self, call, planner, arm, state, config)
        self.actions(ActionExecutor(
            self.journal, state=state, skills=skills.bound(), backend="fafu-real",
            features_extra={
                "hardware": True, "controller_flush": False,
                "verification": "joint_readback", "stop_scope": "path_chunk_boundary",
                "unsupported": {
                    "set_control": "Appliance buttons are not recognized yet.",
                    "stir": "Not validated on the real arm.",
                    "scoop": "Not validated on the real arm.",
                },
            },
        ))
