"""Explicit candidate selection shared by detector and segmentation backends."""

import numpy as np


def select_mask(boxes, scores, selection):
    if len(scores) == 0:
        raise ValueError("target_not_found")
    if (boxes.shape != (len(scores), 4) or not np.isfinite(boxes).all()
            or not np.isfinite(scores).all() or len(scores) > 200):
        raise ValueError("invalid_segmentation_output")
    if selection not in {"only", "leftmost", "rightmost"}:
        raise ValueError("invalid_target_selection")
    if len(scores) == 1:
        return 0
    if selection == "only":
        raise ValueError("target_ambiguous")
    centers = (boxes[:, 0] + boxes[:, 2]) / 2
    order = np.argsort(centers)
    if selection == "rightmost":
        order = order[::-1]
    if abs(centers[order[0]] - centers[order[1]]) < 1:
        raise ValueError("target_ambiguous")
    return int(order[0])


def select_text_mask(boxes, similarities, selection, *, minimum, margin):
    """Cosine scores are heuristic evidence, not calibrated object-presence probabilities."""
    if (boxes.shape != (len(similarities), 4) or not np.isfinite(boxes).all()
            or not np.isfinite(similarities).all() or len(similarities) > 100):
        raise ValueError("invalid_segmentation_output")
    eligible = np.flatnonzero(similarities >= minimum)
    selected = select_mask(boxes[eligible], similarities[eligible], selection)
    if len(eligible) == 1 and len(similarities) > 1:
        runner_up = np.max(np.delete(similarities, eligible[0]))
        if similarities[eligible[0]] - runner_up < margin:
            raise ValueError("target_ambiguous")
    return int(eligible[selected])


def select_box_mask(masks, box, *, minimum=.5, margin=.05):
    """Match enclosing boxes, so thin/curved objects need not fill the detection rectangle."""
    box = np.asarray(box)
    if (masks.ndim != 3 or masks.dtype != np.bool_ or len(masks) > 100
            or box.shape != (4,) or not np.isfinite(box).all()):
        raise ValueError("invalid_segmentation_output")
    height, width = masks.shape[1:]
    if not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
        raise ValueError("invalid_region_box")
    if not len(masks):
        raise ValueError("region_mask_not_found")
    columns, rows = masks.any(axis=1), masks.any(axis=2)
    present = columns.any(axis=1)
    if not present.any():
        raise ValueError("region_mask_not_found")
    # Half-open pixel bounds: the last foreground pixel contributes its entire unit cell.
    bounds = np.column_stack((columns.argmax(1), rows.argmax(1),
                              width - columns[:, ::-1].argmax(1),
                              height - rows[:, ::-1].argmax(1)))
    overlap = np.minimum(bounds[:, 2:], box[2:]) - np.maximum(bounds[:, :2], box[:2])
    intersections = np.maximum(overlap, 0).prod(axis=1)
    unions = ((bounds[:, 2:] - bounds[:, :2]).prod(axis=1)
              + (box[2:] - box[:2]).prod() - intersections)
    scores = intersections / unions
    scores[~present] = 0
    order = np.argsort(scores)[::-1]
    best = int(order[0])
    if scores[best] < minimum:
        raise ValueError("region_mask_not_found")
    if len(order) > 1 and scores[best] - scores[order[1]] < margin:
        raise ValueError("region_mask_ambiguous")
    return best, float(scores[best])
