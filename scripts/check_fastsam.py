"""Run the verified FastSAM-x on a local image, without cameras or text-model downloads."""

import argparse
import json
import os
import time
from pathlib import Path

from download_fastsam import MANIFEST, ROOT, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, default=ROOT / ".local/models/fastsam/FastSAM-x.pt")
    parser.add_argument("--image", type=Path, help="Defaults to the SDK's bundled bus.jpg")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/fastsam_inference.json")
    args = parser.parse_args()
    entry = json.loads(MANIFEST.read_text("utf-8"))
    verify(args.weights, entry)
    os.environ["YOLO_OFFLINE"] = "true"
    os.environ["YOLO_AUTOINSTALL"] = "false"
    config_directory = ROOT / ".local/ultralytics"
    config_directory.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(config_directory)
    import torch
    import ultralytics
    from ultralytics import FastSAM, settings
    from ultralytics.utils import ASSETS

    settings.update({"sync": False})
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable; CPU requires explicit --device cpu")
    source = (args.image or ASSETS / "bus.jpg").resolve(strict=True)
    model = FastSAM(str(args.weights.resolve()))
    if args.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    results = model.predict(source=str(source), device=0 if args.device == "cuda" else "cpu",
                            retina_masks=True, imgsz=640, conf=.4, iou=.9, max_det=100,
                            verbose=False, save=False)
    result, = results
    if result.masks is None or len(result.masks) == 0:
        raise RuntimeError("No masks on the probe image")
    assert tuple(result.masks.data.shape[1:]) == result.orig_shape
    assert torch.isfinite(result.masks.data).all()
    elapsed = time.perf_counter() - started
    box = result.boxes.xyxy[0].tolist()
    # Reuse this inference for box prompting; do not run the image network again.
    selected, = model.predictor.prompt(results, bboxes=[box])
    assert selected.masks is not None and len(selected.masks) == 1
    report = {
        "model": entry["model"], "release": entry["release"], "sha256": entry["sha256"],
        "ultralytics": ultralytics.__version__, "torch": torch.__version__,
        "device": args.device,
        "gpu": torch.cuda.get_device_name() if args.device == "cuda" else None,
        "image": str(source), "image_size_hw": list(result.orig_shape),
        "mask_count": len(result.masks), "box_prompt_masks": len(selected.masks),
        "first_prediction_seconds": elapsed,
        "peak_cuda_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2
        if args.device == "cuda" else None,
        "trained_inference": "passed", "text_grounding": "not_tested",
        "camera_and_robot": "not_tested",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.report.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
