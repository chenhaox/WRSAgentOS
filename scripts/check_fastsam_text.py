"""Probe local FastSAM/CLIP target selection on a local photo; no devices or downloads."""

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from wrs_agent.nodes.segmentation.fastsam import FastSamSegmenter
from wrs_agent.nodes.vision.selection import select_text_mask

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, help="Defaults to the SDK's bundled zidane.jpg")
    parser.add_argument("--target", action="append",
                        help="Repeatable; defaults to tie and elephant")
    parser.add_argument("--selection", choices=["only", "leftmost", "rightmost"], default="only")
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/fastsam_text.json")
    args = parser.parse_args()
    assets = ROOT / "wrs_agent/nodes/segmentation"
    weights = ROOT / ".local/models/fastsam"
    entry = json.loads((assets / "fastsam_assets.json").read_text("utf-8"))
    model = FastSamSegmenter(weights / entry["filename"], entry["sha256"],
                             clip_weights=weights / "ViT-B-32.pt", device=args.device)
    import torch
    import ultralytics
    from ultralytics.utils import ASSETS

    source = (args.image or ASSETS / "zidane.jpg").resolve(strict=True)
    rgb = np.asarray(ImageOps.exif_transpose(Image.open(source)).convert("RGB"))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    outcomes = []
    for target in args.target or ["tie", "elephant"]:
        began = time.perf_counter()
        result = {"target": target}
        try:
            boxes, masks, scores = model.candidates(rgb, target)
            top = np.argsort(scores)[-3:][::-1]
            result.update(candidates=len(scores),
                          top_scores=[float(scores[index]) for index in top])
            selected = select_text_mask(boxes, scores, args.selection,
                                        minimum=model.minimum, margin=model.margin)
        except ValueError as exc:
            if str(exc) not in {"target_not_found", "target_ambiguous"}:
                raise
            result.update(status="rejected", reason=str(exc))
        else:
            mask_path = args.report.with_name(f"{args.report.stem}_{len(outcomes)}_mask.png")
            Image.fromarray(masks[selected].astype("uint8") * 255).save(mask_path)
            result.update(status="selected", mask=str(mask_path), box=boxes[selected].tolist(),
                          pixels=int(masks[selected].sum()), score=float(scores[selected]))
        result["seconds"] = time.perf_counter() - began
        outcomes.append(result)
    report = {
        "model": model.name, "weights_sha256": model.sha256,
        "text_weights_sha256": model.text_sha256, "score_kind": model.score_kind,
        "minimum": model.minimum, "margin": model.margin,
        "torch": torch.__version__, "ultralytics": ultralytics.__version__,
        "device": args.device, "image": str(source), "outcomes": outcomes,
        "target_accuracy": "not_benchmarked", "camera_and_robot": "not_tested",
    }
    output = json.dumps(report, indent=2) + "\n"
    args.report.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
