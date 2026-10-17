"""YOLO detect worker. Run with the interpreter that has ultralytics."""

import os

from loop import VisionError, boxes_from_results, serve

_model = None


def _model_path():
    return os.environ.get("WRS_YOLO_WEIGHTS", "yolov8n.pt")


def infer(request):
    global _model
    image = request.get("image")
    if not isinstance(image, str) or not os.path.isfile(image):
        raise VisionError("image_missing")
    try:
        conf = float(request.get("conf", 0.25))
    except (TypeError, ValueError):
        raise VisionError("invalid_request") from None
    if _model is None:
        try:
            from ultralytics import YOLO
        except ImportError:
            raise VisionError("ultralytics_missing") from None
        print(f"loading yolo {_model_path()}", flush=True)
        _model = YOLO(_model_path())
    results = _model.predict(source=image, conf=conf, verbose=False)
    items = boxes_from_results(results)
    return {"ok": True, "task": "detect", "count": len(items), "items": items}


if __name__ == "__main__":
    serve(infer)
