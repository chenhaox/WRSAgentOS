import numpy as np
import pytest

from tests.fixtures.grasping.virtual_data import data
from wrs_agent.env.fixed_camera import load_calibration, validate_observation
from wrs_agent.env.wrs_grasp import box_capture_width, dh50_pose, match_scene
from wrs_agent.executor import SkillFailure
from wrs_agent.nodes.wrs.node import WrsOptions
from wrs_agent.processes import ROOT
from wrs_agent.scene import load_scene


def calibration():
    return load_calibration(ROOT / "tests/fixtures/grasping/virtual_camera.json")


def test_fixed_camera_transform_and_tool_axes():
    sample, calibrated = data(), calibration()
    pose = dh50_pose(sample.info.candidates[0], calibrated)
    np.testing.assert_allclose(pose[:3, 3], [.4, .1, .05], atol=1e-8)
    np.testing.assert_allclose(pose[:3, :3], np.diag([1., -1., -1.]), atol=1e-8)
    scene = load_scene(ROOT / "tests/fixtures/grasping/virtual_scene.toml")
    assert match_scene(sample.segment, calibrated, scene) == "box"
    assert box_capture_width(pose, scene["box"]) == .01


def test_wrong_frame_expired_observation_and_virtual_hardware_calibration_are_rejected():
    observation = data().info.segment.observation
    for value, age, hardware in (
        (observation.model_copy(update={"frame_id": "other-camera"}), 1., False),
        (observation, 11., False), (observation, 0., True), (observation, float("nan"), False),
    ):
        with pytest.raises(ValueError):
            validate_observation(calibration(), value, age, hardware=hardware)
    with pytest.raises(ValueError):
        WrsOptions(backend="wrs-grasp")


def test_missing_bad_or_reflected_calibration_is_not_replaced_with_identity(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_calibration(tmp_path / "absent.json")
    value = calibration().model_dump()
    value["world_from_camera"][0] = -1.
    with pytest.raises(ValueError, match="invalid_camera_extrinsics"):
        type(calibration()).model_validate(value)


def test_unknown_observed_obstacle_rejects_static_scene_match():
    sample = data()
    changed = sample.segment.arrays["points"].copy()
    changed[0, 0] = [.9, .9, .4]
    sample.segment.arrays["points"] = changed
    with pytest.raises(SkillFailure, match="unmodeled_scene_points"):
        match_scene(sample.segment, calibration(),
                    load_scene(ROOT / "tests/fixtures/grasping/virtual_scene.toml"))
