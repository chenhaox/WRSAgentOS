"""Ultralytics adapter. Load one explicitly selected, checksum-verified local model."""

import hashlib
from pathlib import Path

import numpy as np

from wrs_agent.nodes.vision.local_models import clip_checkpoint, configure_ultralytics
from wrs_agent.nodes.vision.results import Object2D


class YoloDetector:
    def __init__(self, weights, sha256, *, confidence=0.35, device="cpu", clip_weights=None):
        path = Path(weights).resolve(strict=True)
        if not path.is_file() or path.suffix != ".pt":
            raise ValueError("local_yolo_checkpoint_required")
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != sha256:
            raise ValueError("yolo_checkpoint_checksum_mismatch")
        text_asset = clip_checkpoint(clip_weights) if clip_weights is not None else None
        configure_ultralytics()
        from ultralytics import YOLO, YOLOWorld, settings

        settings.update({"sync": False})
        self.model = YOLOWorld(str(path)) if text_asset else YOLO(str(path), task="detect")
        self.confidence, self.device = confidence, device
        self.name = path.stem
        self.sha256 = actual
        self.text_sha256 = text_asset[1] if text_asset else None
        self.labels = list(self.model.names.values())
        if text_asset:
            import torch
            from ultralytics.nn.text_model import CLIP

            selected = torch.device("cpu" if device == "cpu" else f"cuda:{device}")
            self.model.to(selected)
            # CLIP accepts a local filename; checking it first disables its download path.
            self.model.model.clip_model = CLIP(str(text_asset[0]), device=selected)
            self.default_embeddings = self.model.model.txt_feats.clone()

    def detect(self, rgb):
        # Ultralytics' numpy input convention is BGR; camera arrays are explicitly RGB.
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        result, = self.model.predict(bgr, conf=self.confidence, device=self.device,
                                     max_det=32, verbose=False, save=False)
        boxes = result.boxes
        if boxes is None:
            return []
        xyxy = boxes.xyxy.cpu().numpy()
        scores = boxes.conf.cpu().numpy()
        classes = boxes.cls.cpu().numpy()
        return [Object2D(instance_id=str(i), label=str(result.names[int(cls)])[:64],
                         score=float(score), box=[float(v) for v in box])
                for i, (box, score, cls) in enumerate(zip(xyxy, scores, classes, strict=True))]

    def locate(self, rgb, target):
        """Called on the same single worker as continuous detection; vocabulary cannot race."""
        if self.text_sha256 is None:
            if target.casefold() not in {label.casefold() for label in self.labels}:
                raise ValueError("unsupported_target_label")
            return self.detect(rgb)
        try:
            self.model.model.clip_model.tokenize([target], truncate=False)
        except RuntimeError as exc:
            raise ValueError("target_text_too_long") from exc
        try:
            self.model.set_classes([target])
            return [item.model_copy(update={"label_source": "prompt"})
                    for item in self.detect(rgb)]
        finally:
            # Restore the normal announcement vocabulary even when inference fails.
            self.model.model.txt_feats = self.default_embeddings
            self.model.model.model[-1].nc = len(self.labels)
            self.model.model.names = dict(enumerate(self.labels))
            self.model.predictor = None
