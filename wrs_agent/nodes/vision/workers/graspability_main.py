"""Graspability worker. Poses stay in the incoming depth camera frame."""

import os

from graspability_lib import evaluate
from loop import VisionError, serve


def _number(request, name):
    try:
        value = float(request[name])
    except (KeyError, TypeError, ValueError):
        raise VisionError("invalid_request") from None
    if value != value:
        raise VisionError("invalid_request")
    return value


def infer(request):
    path = request.get("depth")
    if not isinstance(path, str) or not os.path.isfile(path):
        raise VisionError("depth_missing")
    try:
        top = int(request.get("top", 3))
    except (TypeError, ValueError):
        raise VisionError("invalid_request") from None
    top = min(8, max(1, top))
    box = [_number(request, name) for name in ("x1", "y1", "x2", "y2")]
    fx, fy = _number(request, "fx"), _number(request, "fy")
    cx, cy = _number(request, "cx"), _number(request, "cy")
    if fx <= 0 or fy <= 0 or box[2] <= box[0] or box[3] <= box[1]:
        raise VisionError("invalid_request")
    try:
        import numpy as np

        depth = np.load(path, allow_pickle=False)
    except Exception:
        raise VisionError("invalid_depth") from None
    items = evaluate(depth, box, fx, fy, cx, cy, top)
    return {"ok": True, "task": "graspability", "count": len(items), "items": items}


if __name__ == "__main__":
    serve(infer)
