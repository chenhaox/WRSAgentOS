"""Line protocol for a model process. stdout is reserved for JSON replies."""

import json
import os
import sys


class VisionError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def serve(infer):
    """Print one ready line, then answer one JSON object per stdin line."""
    proto = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)

    def reply(payload):
        proto.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proto.flush()

    reply({"ready": True})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if len(line) > 1_000_000:
            reply({"ok": False, "error": "request_too_large"})
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise VisionError("invalid_request")
            reply(infer(request))
        except VisionError as exc:
            reply({"ok": False, "error": exc.code})
        except Exception as exc:
            print(f"vision worker: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            reply({"ok": False, "error": "inference_failed"})


def boxes_from_results(results, limit=32):
    items = []
    for result in results:
        names = getattr(result, "names", {}) or {}
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        for box in boxes:
            cls = int(box.cls[0])
            if isinstance(names, dict):
                label = names.get(cls, str(cls))
            elif isinstance(names, (list, tuple)) and cls < len(names):
                label = names[cls]
            else:
                label = str(cls)
            score = float(box.conf[0])
            items.append(
                {
                    "label": str(label)[:64] or "object",
                    "score": max(0.0, min(1.0, score)),
                    "xyxy": [round(float(value), 2) for value in box.xyxy[0].tolist()],
                }
            )
            if len(items) >= limit:
                return items
    return items
