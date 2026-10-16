"""SAM3 text-prompt worker. Run with the interpreter that has ultralytics."""

import os

from loop import VisionError, boxes_from_results, serve

_predictor = None


def _weights():
    return os.environ.get("WRS_SAM3_WEIGHTS", "sam3.pt")


def _prompts(request):
    raw = request.get("text")
    if not isinstance(raw, str):
        raise VisionError("invalid_request")
    prompts = [part.strip()[:40] for part in raw.split(",") if part.strip()]
    if not prompts or len(prompts) > 8:
        raise VisionError("invalid_request")
    return prompts


def infer(request):
    global _predictor
    image = request.get("image")
    if not isinstance(image, str) or not os.path.isfile(image):
        raise VisionError("image_missing")
    prompts = _prompts(request)
    try:
        conf = float(request.get("conf", 0.25))
    except (TypeError, ValueError):
        raise VisionError("invalid_request") from None
    if _predictor is None:
        try:
            from ultralytics.models.sam import SAM3SemanticPredictor
        except ImportError:
            raise VisionError("ultralytics_missing") from None
        print(f"loading sam3 {_weights()}", flush=True)
        _predictor = SAM3SemanticPredictor(
            overrides={
                "conf": conf,
                "task": "segment",
                "mode": "predict",
                "model": _weights(),
                "imgsz": 644,
                "verbose": False,
                "save": False,
            }
        )
    _predictor.args.conf = conf
    results = _predictor(image, text=prompts)
    items = boxes_from_results(results)
    return {"ok": True, "task": "segment", "count": len(items), "items": items}


if __name__ == "__main__":
    serve(infer)
