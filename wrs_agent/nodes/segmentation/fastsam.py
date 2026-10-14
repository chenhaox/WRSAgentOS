"""Resident FastSAM masks and local CLIP matching, with bounded candidate batches."""

import numpy as np

from wrs_agent.nodes.vision.artifacts import checkpoint_path
from wrs_agent.nodes.vision.local_models import clip_checkpoint, configure_ultralytics
from wrs_agent.nodes.vision.selection import select_box_mask, select_text_mask


class FastSamSegmenter:
    name = "fastsam-clip"
    score_kind = "clip_cosine"

    def __init__(self, weights, sha256, *, clip_weights=None, device="cuda", confidence=.4,
                 minimum=.28, margin=.03):
        path = checkpoint_path(weights, sha256)
        text_asset = clip_checkpoint(clip_weights) if clip_weights is not None else None
        configure_ultralytics()
        import torch
        from ultralytics import FastSAM, settings

        settings.update({"sync": False})
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("fastsam_cuda_unavailable")
        self.model = FastSAM(str(path))
        self.torch, self.device = torch, device
        self.sha256, self.text_sha256 = sha256, text_asset[1] if text_asset else None
        if text_asset:
            import clip

            self.clip, self.preprocess = clip.load(str(text_asset[0]), device=device, jit=False)
            self.clip.eval()
            self.tokenize = clip.tokenize
        else:
            self.name, self.score_kind = "fastsam-box", "box_iou"
        self.confidence, self.minimum, self.margin = confidence, minimum, margin

    def masks(self, rgb):
        torch = self.torch
        result, = self.model.predict(
            np.ascontiguousarray(rgb[:, :, ::-1]),
            device=0 if self.device == "cuda" else "cpu", conf=self.confidence,
            imgsz=640, iou=.9, max_det=100, retina_masks=True, save=False, verbose=False,
        )
        if result.masks is None:
            raise ValueError("target_not_found")
        if len(result.masks) >= 100:
            raise ValueError("too_many_segment_candidates")
        if tuple(result.masks.data.shape[1:]) != rgb.shape[:2]:
            raise ValueError("segmentation_grid_mismatch")
        if not torch.isfinite(result.masks.data).all():
            raise ValueError("invalid_segmentation_output")
        boxes = result.boxes.xyxy.detach().cpu().numpy()
        masks = result.masks.data.bool().cpu().numpy()
        if boxes.shape != (len(masks), 4) or not np.isfinite(boxes).all():
            raise ValueError("invalid_segmentation_output")
        valid = np.flatnonzero(masks.any(axis=(1, 2)))
        boxes, masks = boxes[valid], masks[valid]
        if not len(boxes):
            raise ValueError("target_not_found")
        return boxes, masks

    def candidates(self, rgb, target):
        """Experimental CLIP mask matching; semantic accuracy remains unverified."""
        from PIL import Image

        if self.text_sha256 is None:
            raise ValueError("fastsam_text_weights_required")
        try:
            tokens = self.tokenize([f"a photo of {target}"], truncate=False)
        except RuntimeError as exc:
            raise ValueError("target_text_too_long") from exc
        tokens = tokens.to(self.device)
        boxes, masks = self.masks(rgb)
        # CLIP's crop-size filter must not discard small box-prompted objects.
        valid = np.flatnonzero(masks.sum(axis=(1, 2)) > 100)
        boxes, masks = boxes[valid], masks[valid]
        if not len(boxes):
            raise ValueError("target_not_found")
        torch = self.torch
        scores = []
        with torch.inference_mode():
            text = self.clip.encode_text(tokens).float()
            text /= text.norm(dim=-1, keepdim=True)
            for start in range(0, len(boxes), 8):
                images = []
                for box, mask in zip(boxes[start:start + 8], masks[start:start + 8], strict=True):
                    x1, y1, x2, y2 = box.astype(int)
                    if not (0 <= x1 < x2 <= rgb.shape[1] and 0 <= y1 < y2 <= rgb.shape[0]):
                        raise ValueError("invalid_segment_box")
                    crop = rgb[y1:y2, x1:x2] * mask[y1:y2, x1:x2, None]
                    images.append(self.preprocess(Image.fromarray(crop)))
                batch = torch.stack(images).to(self.device)
                features = self.clip.encode_image(batch).float()
                features /= features.norm(dim=-1, keepdim=True)
                scores.extend((features @ text.T)[:, 0].cpu().tolist())
        return boxes, masks, np.asarray(scores)

    def segment(self, rgb, target, selection):
        boxes, masks, scores = self.candidates(rgb, target)
        selected = select_text_mask(boxes, scores, selection,
                                    minimum=self.minimum, margin=self.margin)
        return masks[selected].copy(), float(scores[selected])

    def segment_box(self, rgb, box):
        _, masks = self.masks(rgb)
        selected, score = select_box_mask(masks, box)
        return masks[selected].copy(), score
