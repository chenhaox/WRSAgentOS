"""GraspNet-baseline worker. Poses stay in the incoming cloud frame."""

import os
from pathlib import Path

from loop import VisionError, serve

_net = None


def _paths():
    root = os.environ.get("WRS_GRASPNET_ROOT", "").strip()
    if not root:
        candidate = Path.home() / "grt/graspnet-baseline"
        if (candidate / "models" / "graspnet.py").is_file():
            root = str(candidate)
    checkpoint = os.environ.get("WRS_GRASPNET_CHECKPOINT", "").strip()
    if not checkpoint and root:
        candidate = Path(root) / "checkpoints" / "checkpoint-rs.tar"
        if candidate.is_file():
            checkpoint = str(candidate)
    return root, checkpoint


def _grasp_group():
    """Load GraspGroup without importing the dataset evaluator."""
    import importlib.util
    import sys
    import types

    spec = importlib.util.find_spec("graspnetAPI")
    if spec is None or not spec.submodule_search_locations:
        raise VisionError("graspnet_import_missing")
    if "graspnetAPI" not in sys.modules:
        package = types.ModuleType("graspnetAPI")
        package.__path__ = list(spec.submodule_search_locations)
        package.__package__ = "graspnetAPI"
        sys.modules["graspnetAPI"] = package
    from graspnetAPI.grasp import GraspGroup

    return GraspGroup


def _load():
    global _net
    if _net is not None:
        return _net
    root, checkpoint = _paths()
    if not root or not Path(root).is_dir():
        raise VisionError("graspnet_root_missing")
    if not checkpoint or not Path(checkpoint).is_file():
        raise VisionError("graspnet_checkpoint_missing")
    import sys

    root_path = Path(root)
    for extra in ("knn", "pointnet2", "utils", "models"):
        sys.path.insert(0, str(root_path / extra))
    sys.path.insert(0, str(root_path))
    try:
        import numpy as np
        import torch
        from graspnet import GraspNet, pred_decode

        GraspGroup = _grasp_group()
    except ImportError:
        raise VisionError("graspnet_import_missing") from None
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    net = GraspNet(
        input_feature_dim=0,
        num_view=300,
        num_angle=12,
        num_depth=4,
        cylinder_radius=0.05,
        hmin=-0.02,
        hmax_list=[0.01, 0.02, 0.03, 0.04],
        is_training=False,
    )
    state = torch.load(checkpoint, map_location=device, weights_only=False)
    if isinstance(state, dict) and "model_state_dict" in state:
        state = state["model_state_dict"]
    net.load_state_dict(state)
    net.to(device)
    net.eval()
    _net = (net, device, np, torch, GraspGroup, pred_decode)
    print(f"loaded graspnet on {device}", flush=True)
    return _net


def _cloud(path, np):
    points = np.load(path)
    if getattr(points, "ndim", 0) != 2 or points.shape[1] < 3 or len(points) < 16:
        raise VisionError("invalid_cloud")
    xyz = np.asarray(points[:, :3], dtype="float32")
    if not np.isfinite(xyz).all():
        raise VisionError("invalid_cloud")
    choice = np.random.choice(len(xyz), 20000, replace=len(xyz) < 20000)
    return xyz[choice]


def _top_indexes(group, np, top, separation=0.03):
    """Keep the highest scores whose centers are at least 3 cm apart."""
    order = np.argsort(-np.asarray(group.scores))
    centers = np.asarray(group.translations)
    chosen = []
    for index in order:
        center = centers[index]
        if all(np.linalg.norm(center - centers[kept]) >= separation for kept in chosen):
            chosen.append(int(index))
        if len(chosen) >= top:
            break
    return chosen


def infer(request):
    cloud = request.get("cloud")
    if not isinstance(cloud, str) or not os.path.isfile(cloud):
        raise VisionError("cloud_missing")
    try:
        top = int(request.get("top", 5))
    except (TypeError, ValueError):
        raise VisionError("invalid_request") from None
    top = min(8, max(1, top))
    net, device, np, torch, GraspGroup, pred_decode = _load()
    xyz = _cloud(cloud, np)
    batch = {
        "point_clouds": torch.from_numpy(xyz[None]).to(device),
        "cloud_colors": torch.ones((1, len(xyz), 3), device=device),
    }
    with torch.no_grad():
        end_points = net(batch)
        preds = pred_decode(end_points)
    group = GraspGroup(preds[0].detach().cpu().numpy())
    if len(group) == 0:
        return {"ok": True, "task": "infer_grasps", "count": 0, "items": []}
    items = []
    for index in _top_indexes(group, np, top):
        score = float(group.scores[index])
        items.append(
            {
                "label": "grasp",
                "score": max(0.0, min(1.0, score)),
                "translation": [round(float(v), 5) for v in group.translations[index]],
                "rotation": [
                    round(float(v), 5) for v in group.rotation_matrices[index].reshape(-1)
                ],
                "width": round(float(group.widths[index]), 5),
            }
        )
    return {"ok": True, "task": "infer_grasps", "count": len(items), "items": items}


if __name__ == "__main__":
    serve(infer)
