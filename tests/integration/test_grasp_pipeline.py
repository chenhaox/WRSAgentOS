"""Real Zenoh, Runtime output bindings and explicit offline perception fixtures."""

import asyncio

import numpy as np
import pytest
from conftest import eventually

from wrs_agent.nodes.camera.client import CameraClient
from wrs_agent.nodes.grasp.data import read_grasps
from wrs_agent.processes import ROOT, LocalStack, python_command
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import Plan, Step, TaskCancelRequest, TaskRequest, new_id

pytestmark = pytest.mark.zenoh


def preparation(target="box", *, region=False):
    locate = []
    segment = Step(step_id="segment", skill="segment_object", args={"target": target})
    if region:
        locate = [Step(step_id="locate", skill="locate_object", args={"target": target})]
        segment = Step(step_id="segment", skill="segment_region", depends_on=["locate"],
                       inputs={"location_id": {"step_id": "locate", "field": "location_id"}})
    return Plan(steps=[*locate, segment,
        Step(step_id="grasps", skill="infer_grasps", depends_on=["segment"],
             inputs={"segment_id": {"step_id": "segment", "field": "segment_id"}}),
    ])


@pytest.mark.parametrize("segmentation", ["sam3", "segmentation", "segmentation-box"])
async def test_same_frame_pipeline_ambiguity_and_late_cancel(segmentation):
    region = segmentation == "segmentation-box"
    target = "cup" if region else "box"
    async with LocalStack(duration=.03) as stack:
        stack._spawn("camera", python_command(ROOT / "tests/fixtures/camera_node.py",
                                               stack.endpoint, stack.env_id))
        if region:
            stack._spawn("yolo", python_command(ROOT / "tests/fixtures/vision_node.py",
                                                stack.endpoint, stack.env_id))
            await stack.system.registry.wait_for("yolo", timeout=10)
        for kind in (segmentation, "graspnet"):
            stack._spawn(kind, python_command(ROOT / "tests/fixtures/grasp_perception_node.py",
                                              kind, stack.endpoint, stack.env_id, segmentation))
        registry = stack.system.registry
        for kind in ("camera", segmentation, "graspnet"):
            await registry.wait_for(kind, timeout=10)
        skills = {"segment_region" if region else "segment_object": segmentation,
                  "infer_grasps": "graspnet"}
        if region:
            skills["locate_object"] = "yolo"
        runtime = Runtime(stack.system.clients, skills, registry=registry)
        try:
            receipt = await runtime.start(TaskRequest(request_id=new_id(),
                                                       plan=preparation(target, region=region)))
            result = await eventually(runtime.snapshot,
                                      lambda s: s["state"] in {"SUCCEEDED", "FAILED"}, timeout=8)
            assert result["state"] == "SUCCEEDED", result
            aid = next(a["action_id"] for a in runtime.action_history
                       if a["skill"] == "infer_grasps")
            output = (await stack.system.clients["graspnet"].status(aid)).output
            data, age = await read_grasps(registry.transport("graspnet"),
                                          output["candidate_set_id"], receipt["task_id"])
            assert data.info.segment.observation.observation_id == output["observation_id"]
            assert data.info.owner.task_id == data.info.segment.owner.task_id == receipt["task_id"]
            assert age >= .3 and data.info.segment.observation.source == "synthetic"
            if segmentation == "segmentation":
                assert data.info.segment.score_kind == "clip_cosine"
                assert data.info.segment.text_weights_sha256 == "1" * 64
            if region:
                location = data.info.segment.location
                assert location.observation == data.info.segment.observation
                assert location.owner.task_id == receipt["task_id"]
                assert data.info.segment.score_kind == "box_iou"
                bad_location = Plan(steps=[Step(step_id="reuse", skill="segment_region",
                                                args={"location_id": location.location_id})])
                await runtime.start(TaskRequest(request_id=new_id(), plan=bad_location))
                await eventually(runtime.snapshot, lambda s: s["state"] == "FAILED")
            latest, _, _ = await CameraClient(registry.transport("camera")).read()
            assert latest.observation_id != output["observation_id"]
            np.testing.assert_allclose(data.segment.arrays["points"][:, :, 2],
                                       data.segment.arrays["depth"] * .001)
            # A new task cannot use another task's segment reference.
            bad_plan = Plan(steps=[Step(step_id="reuse", skill="infer_grasps",
                                        args={"segment_id": data.info.segment.segment_id})])
            await runtime.start(TaskRequest(request_id=new_id(), plan=bad_plan))
            await eventually(runtime.snapshot, lambda s: s["state"] == "FAILED")
            await runtime.start(TaskRequest(request_id=new_id(),
                                             plan=preparation("ambiguous", region=region)))
            await eventually(runtime.snapshot, lambda s: s["state"] == "FAILED")
            first_skill = "locate_object" if region else "segment_object"
            assert runtime.action_history[-1]["skill"] == first_skill
            cancelled = await runtime.start(TaskRequest(request_id=new_id(),
                                                         plan=preparation(target, region=region)))
            await eventually(runtime.snapshot, lambda s: bool(s["active_actions"]))
            await runtime.cancel(TaskCancelRequest(request_id=new_id(),
                                                   task_id=cancelled["task_id"]))
            await eventually(runtime.snapshot, lambda s: s["state"] == "CANCELLED")
            await asyncio.sleep(.4)
            assert runtime.action_history[-1]["skill"] == first_skill
            if region:
                cancelled = await runtime.start(TaskRequest(
                    request_id=new_id(), plan=preparation(target, region=True),
                ))
                await eventually(
                    runtime.snapshot,
                    lambda s: bool(s["active_actions"])
                    and runtime.action_history[-1]["skill"] == "segment_region",
                )
                await runtime.cancel(TaskCancelRequest(request_id=new_id(),
                                                       task_id=cancelled["task_id"]))
                await eventually(runtime.snapshot, lambda s: s["state"] == "CANCELLED")
                await asyncio.sleep(.4)
                assert runtime.action_history[-1]["skill"] == "segment_region"
        finally:
            await runtime.close()
