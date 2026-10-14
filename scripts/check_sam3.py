"""Check the installed SAM3 SDK; optional inference uses only explicit local files."""

import argparse
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace


def check_sdk(device):
    import numpy as np
    import torch
    import torchvision
    import transformers
    from transformers import Sam3ImageProcessor, Sam3Model, Sam3Processor

    from wrs_agent.nodes.segmentation.sam3 import prediction_mask

    # Exercise real CUDA/SDK postprocessing with fixture logits, not trained inference.
    outputs = SimpleNamespace(
        pred_logits=torch.tensor([[5.0, -5.0, 4.0]], device=device),
        pred_boxes=torch.tensor([[[0, 0, .3, 1], [.4, 0, .6, 1], [.7, 0, 1, 1]]],
                                device=device),
        pred_masks=torch.tensor([[[[-5, 5], [-5, 5]], [[-5, -5], [-5, -5]],
                                  [[5, -5], [5, -5]]]], device=device, dtype=torch.float32),
        presence_logits=torch.tensor([[5.0]], device=device),
    )
    processor = Sam3ImageProcessor()
    # Compare selection-before-resizing with the SDK's full result.
    with torch.inference_mode():
        expected = processor.post_process_instance_segmentation(
            outputs, threshold=.5, target_sizes=[(24, 32)],
        )[0]
        for selection, index in (("leftmost", 0), ("rightmost", 1)):
            mask, score = prediction_mask(processor, outputs, (24, 32), selection, .5)
            np.testing.assert_array_equal(mask, expected["masks"][index].cpu().numpy())
            assert abs(score - float(expected["scores"][index])) < 1e-6
        try:
            prediction_mask(processor, outputs, (24, 32), "only", .5)
        except ValueError as exc:
            assert str(exc) == "target_ambiguous"
        else:
            raise AssertionError("Ambiguous target was accepted")
        outputs.pred_logits[0, 0] = float("nan")
        try:
            prediction_mask(processor, outputs, (24, 32), "only", .5)
        except ValueError as exc:
            assert str(exc) == "invalid_segmentation_output"
        else:
            raise AssertionError("Invalid score shifted the selected mask")
    return {
        "torch": torch.__version__, "torchvision": torchvision.__version__,
        "transformers": transformers.__version__, "device": device,
        "gpu": torch.cuda.get_device_name() if device == "cuda" else None,
        "sdk_classes": [Sam3Model.__name__, Sam3Processor.__name__],
        "sdk_postprocessing": "passed", "trained_inference": "unverified",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--target")
    parser.add_argument("--selection", choices=["only", "leftmost", "rightmost"], default="only")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    model_args = (args.weights, args.sha256, args.image, args.target)
    if any(model_args) and not all(model_args):
        parser.error("Inference requires --weights, --sha256, --image and --target together")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    report = check_sdk(args.device)
    if args.weights:
        import numpy as np
        from PIL import Image

        from wrs_agent.nodes.segmentation.sam3 import Sam3Segmenter

        model = Sam3Segmenter(args.weights, args.sha256, device=args.device)
        with Image.open(args.image) as image:
            rgb = np.array(image.convert("RGB"))
        started = time.perf_counter()
        mask, score = model.segment(rgb, args.target, args.selection)
        report.update(trained_inference="passed", pixel_count=int(mask.sum()), score=score,
                      seconds=time.perf_counter() - started, weights_sha256=args.sha256)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
