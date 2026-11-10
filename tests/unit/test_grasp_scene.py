"""Full scene network input and exact same-pixel ROI selection after decoding."""

import numpy as np
import pytest

from wrs_agent.errors import AgentError
from wrs_agent.nodes.grasp.graspnet import candidates_from_rows, sample_scene, target_rows


def scene():
    y, x = np.indices((16, 16), dtype=np.float32)
    points = np.stack((x * .01, y * .01, np.full_like(x, .3)), axis=-1)
    mask = x < 8
    points[0, :4] = [[0, 0, 0], [0, 0, -1], [np.nan, 0, .3], [0, np.inf, .3]]
    return points, mask


def decoded(cloud, indices):
    rows = np.tile([.9, .04, .02, .03, *np.eye(3).ravel(), 0, 0, 0, -1.], (len(indices), 1))
    rows[:, 13:16] = cloud[indices]
    return rows


def test_sampling_preserves_background_and_exact_roi_with_invalid_depth_and_padding():
    points, mask = scene()
    before = points.copy()
    cloud, roi = sample_scene(points, mask, count=300)
    assert cloud.shape == (300, 3) and cloud.dtype == np.float32
    assert cloud.flags.c_contiguous and roi.dtype == np.bool_
    assert np.isfinite(cloud).all() and (cloud[:, 2] > 0).all()
    assert roi.any() and (~roi).any()
    np.testing.assert_array_equal(roi, cloud[:, 0] < .08)
    valid = np.isfinite(points).all(axis=2) & (points[:, :, 2] > 0)
    np.testing.assert_array_equal(cloud[:252], points[valid])
    np.testing.assert_array_equal(points, before)


def test_network_sampling_does_not_change_when_only_the_target_mask_changes():
    points, left = scene()
    a, roi_a = sample_scene(points, left, count=128)
    b, roi_b = sample_scene(points, ~left, count=128)
    # This grid is smaller than the neighborhood margin, so both targets still see it.
    np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(roi_a, ~roi_b)
    assert len(np.unique(a, axis=0)) == 128


def test_sampling_keeps_nearby_background_and_drops_the_rest_of_the_room():
    points, _mask = scene()
    points = points.copy()
    points[:, 12:, 0] = 5.0
    mask = np.zeros(points.shape[:2], dtype=bool)
    mask[:, 4:8] = True
    cloud, roi = sample_scene(points, mask, count=400, margin_m=.10)
    assert (cloud[:, 0] < 1).all()
    assert roi.any() and (~roi).any()
    assert cloud[roi, 0].max() < .08
    far = points.copy()
    far_mask = np.zeros(mask.shape, dtype=bool)
    far_mask[:, 12:] = True
    other, other_roi = sample_scene(far, far_mask, count=64, margin_m=.10)
    assert (other[:, 0] > 1).all() and other_roi.all()


def test_sampling_keeps_the_near_surface_and_drops_the_wall_behind_the_mask():
    points, _mask = scene()
    points = points.copy()
    points[:, 8:, 2] = 0.8
    mask = np.zeros(points.shape[:2], dtype=bool)
    mask[:, 4:12] = True
    cloud, roi = sample_scene(points, mask, count=400, margin_m=.10)
    assert roi.any() and (~roi).any()
    assert (cloud[roi, 2] < 0.4).all()
    assert (cloud[:, 2] < 0.5).all()
    assert cloud[roi, 0].min() >= .04 and cloud[roi, 0].max() < .08


def test_target_requires_enough_valid_depth_even_when_background_is_present():
    points, mask = scene()
    mask[:] = False
    mask[:4] = True  # 64 pixels, but four of them have invalid depth.
    with pytest.raises(ValueError, match="^insufficient_target_depth$"):
        sample_scene(points, mask)


def test_roi_uses_pixel_identity_even_for_coincident_points_and_removed_objectness():
    cloud = np.array([[0, 0, .3], [0, 0, .3], [.1, 0, .3]], dtype=np.float32)
    roi = np.array([False, True, True])
    indices = np.array([2, 0, 1])  # Decoder has a different order than the sampled scene.
    objectness = np.array([False, True, True])
    rows = decoded(cloud, indices[objectness])
    rows[:, 0] = [.99, .5]
    selected = target_rows(rows, cloud, roi, indices, objectness)
    assert selected.shape == (1, 17)
    np.testing.assert_array_equal(selected[0], rows[1])


def test_roi_is_selected_before_score_top32_and_gripper_width():
    cloud = np.zeros((42, 3), dtype=np.float32)
    cloud[:, 0] = np.arange(42) * .01
    cloud[:, 2] = .3
    indices = np.arange(42)
    roi = indices >= 40
    rows = decoded(cloud, indices)
    rows[:, 0] = np.linspace(.99, .1, 42)
    rows[40, 1] = .06  # The first ROI candidate exceeds the gripper opening.
    selected = target_rows(rows, cloud, roi, indices, np.ones(42, dtype=bool))
    candidates = candidates_from_rows(selected, max_width_m=.05)
    assert len(candidates) == 1
    np.testing.assert_allclose(candidates[0].translation, cloud[41])


@pytest.mark.parametrize("change", ["wrong_order", "missing_row", "bad_index", "float_index",
                                     "nonfinite", "wrong_objectness", "wrong_roi"])
def test_roi_rejects_corrupt_or_misaligned_model_outputs(change):
    cloud = np.array([[0, 0, .3], [.1, 0, .3]], dtype=np.float32)
    indices, roi, objectness = np.arange(2), np.ones(2, dtype=bool), np.ones(2, dtype=bool)
    rows = decoded(cloud, indices)
    if change == "wrong_order":
        rows = rows[::-1]
    elif change == "missing_row":
        rows = rows[:1]
    elif change == "bad_index":
        indices[1] = 2
    elif change == "float_index":
        indices = indices.astype(float)
    elif change == "nonfinite":
        rows[0, 0] = np.nan
    elif change == "wrong_objectness":
        objectness = objectness[:1]
    elif change == "wrong_roi":
        roi = roi.astype(float)
    with pytest.raises(ValueError, match="^invalid_graspnet_output$"):
        target_rows(rows, cloud, roi, indices, objectness)


@pytest.mark.parametrize("has_objects", [True, False])
def test_empty_target_predictions_never_fall_back_to_background(has_objects):
    cloud = np.array([[0, 0, .3], [.1, 0, .3]], dtype=np.float32)
    indices, roi = np.arange(2), np.array([True, False])
    objectness = np.array([False, has_objects])
    rows = decoded(cloud, indices[objectness])
    with pytest.raises(AgentError, match="^grasp_roi_rejected_as_background:"):
        target_rows(rows, cloud, roi, indices, objectness)


@pytest.mark.parametrize("roi,indices,objectness,code,sampled,seeded", [
    ([False, False], [0, 1], [True, True], "grasp_roi_not_sampled", 0, 0),
    ([True, False], [1], [True], "grasp_roi_no_seed", 1, 0),
    ([True, False], [0, 1], [False, True], "grasp_roi_rejected_as_background", 1, 1),
])
def test_empty_roi_errors_identify_the_stage_with_counts(
    roi, indices, objectness, code, sampled, seeded,
):
    cloud = np.array([[0, 0, .3], [.1, 0, .3]], dtype=np.float32)
    indices, roi, objectness = np.array(indices), np.array(roi), np.array(objectness)
    rows = decoded(cloud, indices[objectness])
    with pytest.raises(AgentError) as failure:
        target_rows(rows, cloud, roi, indices, objectness)
    assert failure.value.code == code
    message = failure.value.error.message
    assert f"ROI采样点 {sampled}/2" in message
    assert f"ROI种子 {seeded}/{len(indices)}" in message
    assert f"场景候选 {objectness.sum()}" in message
    assert "ROI候选 0" in message and len(message) <= 240
