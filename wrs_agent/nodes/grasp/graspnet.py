"""Adapter for a pinned local graspnet-baseline checkout and its CUDA extensions."""

import importlib
import subprocess
import sys
from pathlib import Path

import numpy as np

from wrs_agent.errors import AgentError, error_info
from wrs_agent.nodes.grasp.data import GraspCandidate
from wrs_agent.nodes.vision.artifacts import checkpoint_path


# Local context for an approach, not the rest of the room. GraspNet's farthest-point
# seeds otherwise land on distant walls before they ever reach a small object.
TARGET_MARGIN_M = 0.10
# A loose mask often includes the wall behind the object. Those points are a separate
# surface, and GraspNet's object/background score then rejects every seed on the mask.
FOREGROUND_GAP_M = 0.05


def foreground_target(points, target, *, gap_m=FOREGROUND_GAP_M):
    """Keep the nearer surface when the mask also covers a wall behind it."""
    if not np.isfinite(gap_m) or gap_m <= 0:
        raise ValueError("invalid_foreground_gap")
    chosen = points[target, 2]
    if chosen.size < 64:
        return target
    ordered = np.sort(chosen)
    body = ordered[int(ordered.size * 0.02):]
    if body.size < 64:
        return target
    gaps = np.diff(body)
    cut = int(np.argmax(gaps))
    if gaps[cut] < gap_m:
        return target
    near = (points[:, :, 2] >= body[0]) & (points[:, :, 2] <= body[cut])
    return target & near


def sample_scene(points, mask, *, count=20000, margin_m=TARGET_MARGIN_M):
    """Sample the target surface and nearby points, keeping each point's ROI bit."""
    if not np.isfinite(margin_m) or margin_m < 0:
        raise ValueError("invalid_grasp_margin")
    valid = np.isfinite(points).all(axis=2) & (points[:, :, 2] > 0)
    target = valid & mask
    if np.count_nonzero(target) < 64:
        raise ValueError("insufficient_target_depth")
    target = foreground_target(points, target)
    if np.count_nonzero(target) < 64:
        raise ValueError("insufficient_target_depth")
    low = points[target].min(axis=0) - margin_m
    high = points[target].max(axis=0) + margin_m
    flat = points.reshape(-1, 3)
    near = valid.ravel() & (flat >= low).all(axis=1) & (flat <= high).all(axis=1)
    pixels = np.flatnonzero(near)
    rng = np.random.default_rng(0)  # Stable sampling; GPU inference may still vary.
    if len(pixels) >= count:
        pixels = rng.choice(pixels, count, replace=False)
    else:
        pixels = np.concatenate((pixels, rng.choice(pixels, count - len(pixels), replace=True)))
    cloud = np.ascontiguousarray(points.reshape(-1, 3)[pixels], dtype=np.float32)
    return cloud, target.ravel()[pixels]


def target_rows(rows, cloud, roi, seed_indices, objectness):
    """Follow the pinned decoder's seed order; never match a nearby object's point."""
    if (rows.ndim != 2 or rows.shape[1] != 17 or not np.isfinite(rows).all()
            or seed_indices.ndim != 1 or seed_indices.dtype.kind not in "iu"
            or objectness.shape != seed_indices.shape or objectness.dtype != np.bool_
            or np.any(seed_indices < 0) or np.any(seed_indices >= len(cloud))
            or roi.shape != (len(cloud),) or roi.dtype != np.bool_):
        raise ValueError("invalid_graspnet_output")
    # pred_decode keeps only objectness==1, without changing seed order or translation.
    indices = seed_indices[objectness]
    if (len(rows) != len(indices)
            or not np.allclose(rows[:, 13:16], cloud[indices], rtol=0, atol=1e-6)):
        raise ValueError("invalid_graspnet_output")
    selected = rows[roi[indices]]
    if not len(selected):
        sampled, seeded = int(roi.sum()), int(roi[seed_indices].sum())
        code = ("grasp_roi_not_sampled" if sampled == 0 else
                "grasp_roi_no_seed" if seeded == 0 else "grasp_roi_rejected_as_background")
        error = error_info(code)
        # Only bounded counts enter the public message; do not retain frame/GPU arrays.
        counts = (f" ROI采样点 {sampled}/{len(cloud)}；ROI种子 {seeded}/{len(seed_indices)}；"
                  f"场景候选 {len(rows)}；ROI候选 0。")
        raise AgentError(error.model_copy(update={"message": error.message + counts}))
    return selected


def candidates_from_rows(rows, *, max_width_m):
    if rows.ndim != 2 or rows.shape[1] != 17 or not np.isfinite(rows).all():
        raise ValueError("invalid_graspnet_output")
    usable = rows[(rows[:, 1] > 0) & (rows[:, 1] <= max_width_m) & (rows[:, 0] >= 0)]
    usable = usable[np.argsort(-usable[:, 0], kind="stable")[:32]]
    if not len(usable):
        raise ValueError("no_grasp_within_gripper_width")
    return [GraspCandidate(score=float(row[0]), width_m=float(row[1]),
                           height_m=float(row[2]), depth_m=float(row[3]),
                           rotation=row[4:13].tolist(), translation=row[13:16].tolist())
            for row in usable]


def baseline_module(source, revision):
    """Upstream uses bare imports. Confine its search paths to this dedicated node process."""
    root = Path(source).resolve(strict=True)
    actual = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True, timeout=10).stdout.strip()
    if actual != revision:
        raise ValueError("graspnet_source_revision_mismatch")
    subprocess.run(["git", "-C", str(root), "diff", "--exit-code", "HEAD", "--"],
                   check=True, capture_output=True, timeout=10)
    paths = [root / name for name in ("models", "dataset", "utils")]
    if not all(path.is_dir() for path in paths):
        raise ValueError("invalid_graspnet_source")
    for path in reversed(paths):
        sys.path.insert(0, str(path))
    module = importlib.import_module("graspnet")
    if Path(module.__file__).resolve() != root / "models" / "graspnet.py":
        raise ValueError("unexpected_graspnet_module")
    return module


class GraspNetPredictor:
    name = "graspnet-baseline"

    def __init__(self, weights, sha256, *, source, source_revision, max_width_m=0.05):
        path = checkpoint_path(weights, sha256)
        module = baseline_module(source, source_revision)
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("graspnet_requires_cuda_extensions")
        self.torch, self.decode = torch, module.pred_decode
        self.sha256, self.revision = sha256, source_revision
        self.max_width_m = max_width_m
        self.model = module.GraspNet(
            input_feature_dim=0, num_view=300, num_angle=12, num_depth=4,
            cylinder_radius=0.05, hmin=-0.02, hmax_list=[0.01, 0.02, 0.03, 0.04],
            is_training=False,
        ).to("cuda")
        checkpoint = torch.load(str(path), map_location="cpu", weights_only=True)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.eval()

    def infer(self, points, mask):
        cloud, roi = sample_scene(points, mask)
        try:
            with self.torch.inference_mode():
                tensor = self.torch.from_numpy(cloud[None]).to("cuda")
                result = self.model({"point_clouds": tensor})
                rows = self.decode(result)[0].detach().cpu().numpy()
                indices = result["fp2_inds"][0].detach().cpu().numpy()
                objectness = result["objectness_score"][0].argmax(dim=0).detach().cpu().numpy()
        except self.torch.cuda.OutOfMemoryError as exc:
            raise ValueError("grasp_gpu_out_of_memory") from exc
        selected = target_rows(rows, cloud, roi, indices, objectness == 1)
        return candidates_from_rows(selected, max_width_m=self.max_width_m)
