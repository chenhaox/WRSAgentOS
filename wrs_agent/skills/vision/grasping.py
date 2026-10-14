"""Task contracts for visual grasp preparation. No model SDK or transport imports."""

from typing import Literal

from pydantic import Field

from wrs_agent.schemas import Boundary, Name
from wrs_agent.skills.contracts import Skill


class SegmentArgs(Boundary):
    target: str = Field(min_length=1, max_length=128)
    selection: Literal["only", "leftmost", "rightmost"] = "only"


class SegmentOutput(Boundary):
    segment_id: Name
    observation_id: Name


class LocateArgs(Boundary):
    target: str = Field(min_length=1, max_length=64)
    selection: Literal["only", "leftmost", "rightmost"] = "only"


class LocationOutput(Boundary):
    location_id: Name
    observation_id: Name


class RegionArgs(Boundary):
    location_id: Name


class InferGraspsArgs(Boundary):
    segment_id: Name


class GraspsOutput(Boundary):
    candidate_set_id: Name
    observation_id: Name
    count: int = Field(ge=1, le=32)


class ExecuteGraspArgs(Boundary):
    candidate_set_id: Name


class GraspOutput(Boundary):
    object_id: Name
    observation_id: Name
    text: str = Field(min_length=1, max_length=512)


SEGMENT = Skill(
    name="segment_object", arguments=SegmentArgs, output=SegmentOutput,
    resources=("segmentation",), timeout=30,
    description="Segment a named object in one fresh RGB, depth and point-cloud observation.",
    instructions="Use a short English object phrase, preserving the requested object. "
                 "Use leftmost/rightmost only when explicitly requested; otherwise only. "
                 "Multiple matches fail with target_ambiguous; ask the user to disambiguate. "
                 "Pass this step's segment_id output to infer_grasps.",
    verification="mask_bound_to_task_and_observation",
)

LOCATE = Skill(
    name="locate_object", arguments=LocateArgs, output=LocationOutput,
    resources=("object_localization",), timeout=30,
    description="Locate a named object and retain its complete observation for segmentation.",
    instructions="Preserve the requested object and attributes in a short English target phrase. "
                 "Use leftmost/rightmost only when explicitly requested; otherwise only. "
                 "Multiple matches fail; never silently choose the highest confidence one. "
                 "Pass location_id to segment_region in the same task.",
    verification="box_bound_to_task_and_observation",
)

SEGMENT_REGION = Skill(
    name="segment_region", arguments=RegionArgs, output=SegmentOutput,
    result_parameters=("location_id",), resources=("segmentation",), timeout=30,
    description="Segment a located object's box in exactly the observation used by the detector.",
    instructions="Bind location_id to locate_object output in the same task. "
                 "Pass segment_id to infer_grasps; do not invent pixel coordinates or object IDs.",
    verification="mask_bound_to_location_and_observation",
)

INFER_GRASPS = Skill(
    name="infer_grasps", arguments=InferGraspsArgs, output=GraspsOutput,
    result_parameters=("segment_id",), resources=("grasp_inference",), timeout=30,
    description="Propose grasp poses from the selected object's same-frame XYZ.",
    instructions="Bind segment_id to segment_object or segment_region output in the same task. "
                 "Candidates are proposals, not a successful grasp or a motion authorization. "
                 "Execution requires Environment calibration, collision and freshness checks.",
    verification="candidates_bound_to_segment_and_observation",
)

VIRTUAL_GRASP = Skill(
    name="execute_visual_grasp", arguments=ExecuteGraspArgs, output=GraspOutput,
    result_parameters=("candidate_set_id",), resources=("arm", "gripper"), timeout=30,
    description="Grasp and lift in a configured WRS virtual scene; no physical robot execution.",
    instructions="Bind candidate_set_id to infer_grasps output from the same task. "
                 "Environment validates calibration, observed geometry, collisions and age. "
                 "Report the returned text verbatim; this verifies a virtual attachment only.",
    verification="wrs_virtual_attachment_and_lift",
)
