"""Transformers SAM3 image API; one resident model and strictly local assets."""

import hashlib
import json
import os
from importlib.resources import files
from types import SimpleNamespace

import numpy as np

from wrs_agent.nodes.vision.artifacts import checkpoint_path
from wrs_agent.nodes.vision.selection import select_mask


def model_directory(weights, sha256):
    """Check weights and the pinned processor/config bundle before importing any SDK."""
    path = checkpoint_path(weights, sha256)
    entry = json.loads(files(__package__).joinpath("assets.json").read_text("utf-8"))
    if path.name != entry["weights"] or path.stat().st_size != entry["weights_size"]:
        raise ValueError("sam3_requires_pinned_safetensors_bundle")
    allowed = {entry["weights"], "README.md", ".gitattributes", "sam3.pt"}
    allowed.update(item["path"] for item in entry["files"])
    # Transformers may discover optional tokenizer/config sidecars automatically.
    if any(asset.is_file() and asset.name not in allowed for asset in path.parent.iterdir()):
        raise ValueError("sam3_unverified_sidecar")
    for item in entry["files"]:
        asset = path.parent / item["path"]
        if asset.stat().st_size != item["size"]:
            raise ValueError("sam3_asset_size_mismatch: " + item["path"])
        data = asset.read_bytes()
        digest = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if digest != item["git_sha1"]:
            raise ValueError("sam3_asset_hash_mismatch: " + item["path"])
    return path.parent


def prediction_mask(processor, outputs, shape, selection, confidence):
    """Select from small boxes first; resize only the chosen mask to camera resolution."""
    detections = processor.post_process_object_detection(
        outputs, threshold=-1.0, target_sizes=[shape],
    )[0]
    scores = detections["scores"].detach().float().cpu().numpy()
    boxes = detections["boxes"].detach().float().cpu().numpy()
    # A NaN score is removed even at threshold=-1; reject instead of shifting query indices.
    if (len(scores) != outputs.pred_logits.shape[1] or not np.isfinite(scores).all()
            or not np.isfinite(boxes).all()):
        raise ValueError("invalid_segmentation_output")
    keep = np.flatnonzero(scores > confidence)
    selected = int(keep[select_mask(boxes[keep], scores[keep], selection)])
    one = SimpleNamespace(
        pred_logits=outputs.pred_logits[:, selected:selected + 1],
        pred_boxes=outputs.pred_boxes[:, selected:selected + 1],
        pred_masks=outputs.pred_masks[:, selected:selected + 1],
        presence_logits=outputs.presence_logits,
    )
    result = processor.post_process_instance_segmentation(
        one, threshold=confidence, mask_threshold=0.5, target_sizes=[shape],
    )[0]
    mask = result["masks"][0].detach().cpu().numpy()
    if not mask.any():
        raise ValueError("empty_target_mask")
    return np.array(mask, dtype=bool, copy=True), float(scores[selected])


class Sam3Segmenter:
    name = "sam3-image"
    score_kind = "sam3_presence_class"

    def __init__(self, weights, sha256, *, device="cuda", confidence=0.5):
        directory = model_directory(weights, sha256)
        # Also cover tokenizer's nested loads. A node never authenticates or downloads.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        from transformers import Sam3Model, Sam3Processor

        self.torch, self.sha256 = torch, sha256
        self.device, self.confidence = device, confidence
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("sam3_cuda_unavailable")
        self.model = Sam3Model.from_pretrained(
            directory, local_files_only=True, use_safetensors=True,
            trust_remote_code=False,
        ).to(device).eval()
        self.processor = Sam3Processor.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False,
        )

    def segment(self, rgb, target, selection):
        from PIL import Image

        # Keep intermediate GPU tensors local to this call; retain just one selected mask.
        with self.torch.inference_mode():
            inputs = self.processor(images=Image.fromarray(rgb), text=target,
                                    return_tensors="pt").to(self.device)
            outputs = self.model(**inputs)
            return prediction_mask(self.processor, outputs, rgb.shape[:2],
                                   selection, self.confidence)
