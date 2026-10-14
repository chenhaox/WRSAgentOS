"""Run verified local YOLO-World and FastSAM on a photo; never connects a device."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from wrs_agent.nodes.grasp.graspnet import sample_target
from wrs_agent.nodes.segmentation.fastsam import FastSamSegmenter
from wrs_agent.nodes.vision.locations import select_object
from wrs_agent.nodes.vision.yolo import YoloDetector

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--image", type=Path, help="Defaults to the SDK's bus.jpg")
    inputs.add_argument("--observation", type=Path, help="Offline NPZ: paired rgb/depth/points")
    parser.add_argument("--target", action="append", help="Repeatable English target phrase")
    parser.add_argument("--selection", choices=["only", "leftmost", "rightmost"], default="only")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/box_segmentation.json")
    args = parser.parse_args()
    yolo_asset = json.loads((ROOT / "wrs_agent/nodes/vision/assets.json").read_text("utf-8"))
    sam_asset = json.loads((ROOT / "wrs_agent/nodes/segmentation/fastsam_assets.json").read_text())
    detector = YoloDetector(ROOT / ".local/models/yolo" / yolo_asset["filename"],
                            yolo_asset["sha256"], device="0",
                            clip_weights=ROOT / ".local/models/fastsam/ViT-B-32.pt")
    segmenter = FastSamSegmenter(ROOT / ".local/models/fastsam" / sam_asset["filename"],
                                 sam_asset["sha256"])
    import torch
    import ultralytics
    from ultralytics.utils import ASSETS

    points = None
    if args.observation:
        source = args.observation.resolve(strict=True)
        with np.load(source, allow_pickle=False) as data:
            rgb, depth, points = data["rgb"], data["depth"], data["points"]
            metadata = json.loads(str(data["metadata"].item()))
        if (rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3
                or depth.dtype != np.uint16 or depth.shape != rgb.shape[:2]
                or points.dtype != np.float32 or points.shape != rgb.shape
                or not (0 < rgb.shape[0] <= 1080 and 0 < rgb.shape[1] <= 1280)):
            raise ValueError("invalid_offline_observation")
        source_size = inference_size = [rgb.shape[1], rgb.shape[0]]
        with source.open("rb") as handle:
            observation_sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
        preprocessing = "native RGB-D/XYZ grid; no EXIF transform or resize"
    else:
        source = (args.image or ASSETS / "bus.jpg").resolve(strict=True)
        picture = Image.open(source)
        source_size = picture.size
        picture = ImageOps.exif_transpose(picture).convert("RGB")
        picture.thumbnail((1280, 1080))
        rgb, inference_size = np.asarray(picture), picture.size
        preprocessing = "EXIF orientation, then fit within 1280x1080"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    outcomes = []
    for target in args.target or ["bus", "elephant", "person"]:
        began = time.perf_counter()
        items = detector.locate(rgb, target)
        result = {"target": target, "detections": [item.model_dump() for item in items]}
        try:
            item = select_object(items, target, args.selection)
            mask, score = segmenter.segment_box(rgb, item.box)
        except ValueError as exc:
            if str(exc) not in {"target_not_found", "target_ambiguous",
                                "region_mask_not_found", "region_mask_ambiguous"}:
                raise
            result.update(status="rejected", reason=str(exc))
        else:
            path = args.report.with_name(f"{args.report.stem}_{len(outcomes)}_mask.png")
            Image.fromarray(mask.astype("uint8") * 255).save(path)
            result.update(status="selected", box_iou=score,
                          mask=str(path), pixels=int(mask.sum()))
            if points is not None:
                cloud = sample_target(points, mask)
                selected = args.report.with_name(f"{args.report.stem}_{len(outcomes)}_input.npz")
                provenance = metadata | {
                    "observation_sha256": observation_sha256, "target": target,
                    "detector_sha256": detector.sha256, "segmenter_sha256": segmenter.sha256,
                    "text_sha256": detector.text_sha256, "box": item.box, "box_iou": score,
                }
                np.savez_compressed(selected, rgb=rgb, depth=depth, points=points, mask=mask,
                                    metadata=json.dumps(provenance))
                valid = mask & np.isfinite(points).all(axis=2) & (points[:, :, 2] > 0)
                result.update(grasp_input=str(selected), valid_target_points=int(valid.sum()),
                              sampled_points=len(cloud), depth_range_m=[float(cloud[:, 2].min()),
                                                                      float(cloud[:, 2].max())])
        result["seconds"] = time.perf_counter() - began
        outcomes.append(result)
    report = {
        "detector": detector.name, "detector_sha256": detector.sha256,
        "text_sha256": detector.text_sha256, "segmenter_sha256": segmenter.sha256,
        "torch": torch.__version__, "ultralytics": ultralytics.__version__,
        "image": str(source), "source_size_wh": source_size, "inference_size_wh": inference_size,
        "preprocessing": preprocessing,
        "outcomes": outcomes, "restored_announcement_detections": len(detector.detect(rgb)),
        "semantic_review": "required", "depth_and_grasp": "not_tested",
    }
    if points is not None:
        report.update(observation_sha256=observation_sha256,
                      depth_and_grasp="offline_xyz_prepared; grasp_inference_not_tested")
    output = json.dumps(report, indent=2) + "\n"
    args.report.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
