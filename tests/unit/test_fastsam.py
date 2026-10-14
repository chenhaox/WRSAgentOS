import hashlib

import numpy as np
import pytest
from pydantic import ValidationError

from wrs_agent.nodes.segmentation.fastsam import FastSamSegmenter
from wrs_agent.nodes.segmentation.node import SegmentationOptions
from wrs_agent.nodes.vision.selection import select_mask, select_text_mask

BOXES = np.array([[0, 0, 10, 10], [20, 0, 30, 10], [40, 0, 50, 10]])


def select(scores, selection="only"):
    return select_text_mask(BOXES, np.asarray(scores), selection, minimum=.28, margin=.03)


def test_text_selection_preserves_original_index_and_requires_absolute_evidence():
    assert select([.12, .36, .2]) == 1
    # Argmax alone would select an object even when the requested object is absent.
    with pytest.raises(ValueError, match="target_not_found"):
        select([.1, .2, .25])


def test_close_second_candidate_is_ambiguous_even_below_threshold():
    with pytest.raises(ValueError, match="target_ambiguous"):
        select([.29, .27, .1])


def test_multiple_eligible_masks_require_explicit_spatial_selection():
    with pytest.raises(ValueError, match="target_ambiguous"):
        select([.3, .2, .45])
    assert select([.3, .2, .45], "leftmost") == 0
    assert select([.3, .2, .45], "rightmost") == 2


@pytest.mark.parametrize("scores", [[float("nan"), .3, .1], [.1, float("inf"), .2]])
def test_nonfinite_similarities_fail(scores):
    with pytest.raises(ValueError, match="invalid_segmentation_output"):
        select(scores)


def test_spatial_selection_rejects_indistinguishable_centers():
    with pytest.raises(ValueError, match="target_ambiguous"):
        select_text_mask(np.array([[0, 0, 10, 10], [0, 2, 10, 12]]), np.array([.4, .35]),
                         "leftmost", minimum=.28, margin=.03)
    with pytest.raises(ValueError, match="invalid_target_selection"):
        select_mask(BOXES[:1], np.array([.9]), "guess")


def test_empty_or_excessive_candidates_fail():
    with pytest.raises(ValueError, match="target_not_found"):
        select_text_mask(np.empty((0, 4)), np.array([]), "only", minimum=.28, margin=.03)
    with pytest.raises(ValueError, match="invalid_segmentation_output"):
        select_text_mask(np.zeros((101, 4)), np.ones(101), "only", minimum=.28, margin=.03)


def test_backend_configuration_requires_explicit_local_text_weights():
    base = {"weights": "sam.pt", "weights_sha256": "0" * 64}
    assert SegmentationOptions(**base).backend == "sam3"
    with pytest.raises(ValidationError, match="requires_local_clip"):
        SegmentationOptions(**base, backend="fastsam")
    with pytest.raises(ValidationError, match="require_fastsam_backend"):
        SegmentationOptions(**base, clip_weights="clip.pt")
    assert SegmentationOptions(**base, backend="fastsam", clip_weights="clip.pt").clip_weights


def test_both_assets_are_verified_before_any_model_sdk_import(tmp_path):
    file = tmp_path / "model.pt"
    file.write_bytes(b"not-weights")
    with pytest.raises(ValueError, match="checksum"):
        FastSamSegmenter(file, "0" * 64, clip_weights=file)
    digest = hashlib.sha256(b"not-weights").hexdigest()
    with pytest.raises(FileNotFoundError):
        FastSamSegmenter(file, digest, clip_weights=tmp_path / "missing.pt")
    # A locally supplied CLIP file cannot override the pinned upstream checksum.
    with pytest.raises(ValueError, match="checksum"):
        FastSamSegmenter(file, digest, clip_weights=file)
