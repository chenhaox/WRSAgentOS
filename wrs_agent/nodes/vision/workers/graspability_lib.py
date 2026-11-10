"""Segment one depth region, then score parallel-jaw grasps on that surface.

Depth is meters in the camera frame: x right, y down, z forward. Invalid
samples are non-finite or not positive. The box uses the same pixels as the
depth image, so a color detector box is valid only after the depth is aligned
to color.

The near surface inside the box is the closer connected band. Graspability
then slides a DH50-sized jaw across that mask: both fingers should land on
the silhouette, and the opening between them should be the object. The jaw
opens in the image plane and approaches along camera +z.
"""

from loop import VisionError

_JUMP_NEAR = 0.01
_JUMP_FAR = 0.025
_MIN_PIXELS = 40
_MARGIN = 32
_OPENINGS = (0.02, 0.025, 0.03, 0.035, 0.04, 0.045, 0.05)
_FINGER_THICKNESS = 0.008
_FINGER_LENGTH = 0.02
_SCORE_FLOOR = 0.25


def _imports():
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise VisionError("opencv_missing") from None
    return cv2, np


def _segment(depth, box, cv2, np):
    """Near surface of the detector box, plus a margin so the jaw can see the edge."""
    height, width = depth.shape
    x1, y1, x2, y2 = (int(np.floor(value)) for value in box)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        raise VisionError("invalid_region")
    left, top = max(0, x1 - _MARGIN), max(0, y1 - _MARGIN)
    right, bottom = min(width, x2 + _MARGIN), min(height, y2 + _MARGIN)
    crop = depth[top:bottom, left:right]
    inner = crop[y1 - top : y2 - top, x1 - left : x2 - left]
    usable = np.isfinite(inner) & (inner > 0.05) & (inner < 2.0)
    if int(usable.sum()) < _MIN_PIXELS:
        raise VisionError("region_empty")
    near = float(np.percentile(inner[usable], 10))
    valid = np.isfinite(crop) & (crop > 0.05) & (crop < 2.0)
    band = valid & (crop >= near - _JUMP_NEAR) & (crop <= near + _JUMP_FAR)
    count, labels = cv2.connectedComponents(band.astype(np.uint8), connectivity=4)
    if count <= 1:
        raise VisionError("region_empty")
    overlap = np.bincount(
        labels[y1 - top : y2 - top, x1 - left : x2 - left].ravel(), minlength=count
    )
    overlap[0] = 0
    label = int(np.argmax(overlap))
    if int(overlap[label]) < _MIN_PIXELS:
        raise VisionError("region_empty")
    return crop, labels == label, left, top


def _kernel(np, center, half, thick, length):
    size = center * 2 + 1

    def span(middle, extent):
        start = int(np.floor(middle - extent / 2))
        stop = int(np.ceil(middle + extent / 2))
        start = max(0, start)
        stop = min(size, max(start + 1, stop))
        return start, stop

    def paint(x_start, x_stop):
        kernel = np.zeros((size, size), np.float32)
        y_start, y_stop = span(center, length)
        kernel[y_start:y_stop, x_start:x_stop] = 1
        total = float(kernel.sum())
        if total <= 0:
            return None
        return kernel / total

    left = paint(*span(center - half, thick))
    right = paint(*span(center + half, thick))
    gap_start = span(center - half, thick)[1]
    gap_stop = span(center + half, thick)[0]
    gap = paint(gap_start, gap_stop) if gap_stop > gap_start else None
    if left is None or right is None or gap is None:
        return None
    return left, right, gap


def _search(mask, z, fx, cv2, np):
    meters = z / fx
    found = []
    height, width = mask.shape
    center_xy = ((width - 1) / 2, (height - 1) / 2)
    image = mask.astype(np.float32)
    for degrees in range(0, 180, 15):
        rotation = cv2.getRotationMatrix2D(center_xy, -degrees, 1.0)
        turned = cv2.warpAffine(
            image, rotation, (width, height), flags=cv2.INTER_NEAREST, borderValue=0
        )
        for opening in _OPENINGS:
            half = opening / meters / 2
            thick = max(1.0, _FINGER_THICKNESS / meters)
            length = max(1.0, _FINGER_LENGTH / meters)
            radius = int(np.ceil(half + thick / 2 + 2))
            kernels = _kernel(np, radius, half, thick, length)
            if kernels is None:
                continue
            left, right, gap = kernels
            contact_l = cv2.filter2D(turned, cv2.CV_32F, left, borderType=cv2.BORDER_CONSTANT)
            contact_r = cv2.filter2D(turned, cv2.CV_32F, right, borderType=cv2.BORDER_CONSTANT)
            filled = cv2.filter2D(turned, cv2.CV_32F, gap, borderType=cv2.BORDER_CONSTANT)
            score = filled * (4 * contact_l * (1 - contact_l)) * (4 * contact_r * (1 - contact_r))
            score *= turned
            if radius < height and radius < width:
                score[:radius, :] = 0
                score[-radius:, :] = 0
                score[:, :radius] = 0
                score[:, -radius:] = 0
            index = int(np.argmax(score))
            best = float(score.flat[index])
            if best < _SCORE_FLOOR:
                continue
            row, col = np.unravel_index(index, score.shape)
            original = cv2.invertAffineTransform(rotation)
            mapped = original @ np.array([col, row, 1], dtype=np.float32)
            found.append((best, float(mapped[0]), float(mapped[1]), opening, degrees))
    return found


def _pose(col, row, depth, fx, fy, cx, cy, opening, degrees, score, np):
    height, width = depth.shape
    u = min(width - 1, max(0, int(round(col))))
    v = min(height - 1, max(0, int(round(row))))
    z = float(depth[v, u])
    if not np.isfinite(z) or z <= 0.05:
        return None
    theta = np.deg2rad(degrees)
    cosine, sine = float(np.cos(theta)), float(np.sin(theta))
    return {
        "label": "grasp",
        "score": max(0.0, min(1.0, score)),
        "translation": [
            round((u - cx) * z / fx, 5),
            round((v - cy) * z / fy, 5),
            round(z, 5),
        ],
        "rotation": [
            round(cosine, 5),
            round(-sine, 5),
            0.0,
            round(sine, 5),
            round(cosine, 5),
            0.0,
            0.0,
            0.0,
            1.0,
        ],
        "width": round(float(opening), 5),
    }


def evaluate(depth, box, fx, fy, cx, cy, top):
    """Return up to `top` grasps. Coordinates are pixels of `depth`."""
    cv2, np = _imports()
    depth = np.asarray(depth)
    if depth.ndim != 2 or depth.shape[0] > 2048 or depth.shape[1] > 2048:
        raise VisionError("invalid_depth")
    if min(depth.shape) < 16:
        raise VisionError("invalid_depth")
    crop, mask, origin_x, origin_y = _segment(depth, box, cv2, np)
    surface = crop[mask]
    z = float(np.median(surface))
    if not np.isfinite(z) or z <= 0.05 or fx <= 0 or fy <= 0:
        raise VisionError("invalid_depth")
    ranked = sorted(_search(mask, z, fx, cv2, np), reverse=True)
    chosen = []
    items = []
    for score, col, row, opening, degrees in ranked:
        if any(abs(col - kept[0]) < 16 and abs(row - kept[1]) < 16 for kept in chosen):
            continue
        item = _pose(
            col + origin_x, row + origin_y, depth, fx, fy, cx, cy, opening, degrees, score, np
        )
        if item is None:
            continue
        chosen.append((col, row))
        items.append(item)
        if len(items) >= top:
            break
    return items
