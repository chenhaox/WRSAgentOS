"""Adapter for a pinned local graspnet-baseline checkout and its CUDA extensions."""

import importlib
import subprocess
import sys
from pathlib import Path

import numpy as np

from wrs_agent.nodes.grasp.data import GraspCandidate
from wrs_agent.nodes.vision.artifacts import checkpoint_path


def sample_target(points, mask, *, count=20000):
    valid = mask & np.isfinite(points).all(axis=2) & (points[:, :, 2] > 0)
    cloud = points[valid]
    if len(cloud) < 64:
        raise ValueError("insufficient_target_depth")
    # Stable sampling makes input preparation reproducible; GPU inference may still vary.
    rng = np.random.default_rng(0)
    if len(cloud) >= count:
        indices = rng.choice(len(cloud), count, replace=False)
    else:
        indices = np.concatenate((np.arange(len(cloud)),
                                  rng.choice(len(cloud), count - len(cloud), replace=True)))
    return np.ascontiguousarray(cloud[indices], dtype=np.float32)


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
        cloud = sample_target(points, mask)
        with self.torch.inference_mode():
            tensor = self.torch.from_numpy(cloud[None]).to("cuda")
            result = self.model({"point_clouds": tensor})
            rows = self.decode(result)[0].detach().cpu().numpy()
        return candidates_from_rows(rows, max_width_m=self.max_width_m)
