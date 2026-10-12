"""Resident YOLO, SAM3, GraspNet, and graspability nodes. Each owns one process."""

import asyncio
from pathlib import Path

from wrs_agent.executor import ActionExecutor
from wrs_agent.nodes import Node
from wrs_agent.nodes.vision.skills import DETECT, GRASPABILITY, INFER_GRASPS, SEGMENT
from wrs_agent.nodes.vision.state import VisionState
from wrs_agent.nodes.vision.worker import ModelWorker, vision_python


class _VisionNode(Node):
    node_type = "vision"
    action_service = True
    worker_script = ""
    skill = None

    def _prepare(self, worker):
        result_dir = Path(self.journal).resolve().parent / f"{self.node_id}-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        worker.start()
        return result_dir

    async def setup(self):
        worker = ModelWorker(self.worker_script, self.node_id)
        print(f"{self.node_id} python: {vision_python()}", flush=True)
        try:
            result_dir = await asyncio.to_thread(self._prepare, worker)
        except BaseException:
            worker.close()
            raise
        self.on_close(worker.close)
        self.actions(
            ActionExecutor(
                self.journal,
                state=VisionState(worker, result_dir),
                backend=self.node_id,
                skills=[self.skill],
                features_extra={
                    "robot_controls": False,
                    "hardware": False,
                    "controller_flush": False,
                    "verification": "vision_result_recorded",
                    "stop_scope": "inference_call",
                },
            )
        )


class YoloNode(_VisionNode):
    features = ("vision.detect",)
    worker_script = "yolo_main.py"
    skill = DETECT


class Sam3Node(_VisionNode):
    features = ("vision.segment",)
    worker_script = "sam3_main.py"
    skill = SEGMENT


class GraspNetNode(_VisionNode):
    features = ("vision.grasp",)
    worker_script = "graspnet_main.py"
    skill = INFER_GRASPS


class GraspabilityNode(_VisionNode):
    features = ("vision.graspability",)
    worker_script = "graspability_main.py"
    skill = GRASPABILITY
