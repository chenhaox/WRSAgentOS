"""Finite offline checks of real GraspNet operators/network; trained weights are explicit."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import torch
from build_graspnet import REVISION, ROOT

from wrs_agent.nodes.grasp.graspnet import GraspNetPredictor, baseline_module


def neighbors(xyz, centers, count, *, radius, rotation=None):
    """Small CPU reference: upstream queries keep input order and repeat the first hit."""
    result = torch.empty((*centers.shape[:2], count), dtype=torch.int32)
    for batch in range(len(xyz)):
        for index, center in enumerate(centers[batch]):
            relative = xyz[batch] - center
            if rotation is None:
                keep = (relative.square().sum(-1) < radius**2)
            else:
                relative = relative @ rotation[batch, index]
                keep = ((relative[:, 1:].square().sum(-1) < radius**2)
                        & (relative[:, 0] > -.1) & (relative[:, 0] < .1))
            indices = keep.nonzero().flatten()[:count]
            assert len(indices)  # Each test query is also a point in the input.
            result[batch, index] = indices[0]
            result[batch, index, :len(indices)] = indices.to(torch.int32)
    return result


def operators():
    from knn_pytorch import knn_pytorch
    from pointnet2 import _ext

    torch.manual_seed(41)
    xyz = torch.rand(2, 64, 3) + .1
    centers = xyz[:, :7].contiguous()
    features = torch.rand(2, 4, 64)
    gpu, queries, values = xyz.cuda(), centers.cuda(), features.cuda()
    # Check the current stream too: runtime invokes inference from a dedicated thread.
    stream = torch.cuda.Stream()
    stream.wait_stream(torch.cuda.current_stream())
    with torch.cuda.stream(stream):
        sampled = _ext.furthest_point_sampling(gpu, 8)
        expected = torch.zeros((2, 8), dtype=torch.int32)
        distance = torch.full((2, 64), 1e10)
        last = torch.zeros(2, dtype=torch.long)
        batches = torch.arange(2)
        for index in range(1, 8):
            delta = xyz - xyz[batches, last, None]
            distance = torch.minimum(distance, delta.square().sum(-1))
            last = distance.argmax(-1)
            expected[:, index] = last.to(torch.int32)
        torch.testing.assert_close(sampled.cpu(), expected)
        gathered = _ext.gather_points(values, sampled)
        torch.testing.assert_close(gathered.cpu(),
                                   features.gather(2, expected.long()[:, None].expand(-1, 4, -1)))

        distances, indices = _ext.three_nn(queries, gpu)
        reference = (centers[:, :, None] - xyz[:, None]).square().sum(-1)
        squared, nearest = reference.topk(3, largest=False)
        torch.testing.assert_close(distances.cpu(), squared)
        torch.testing.assert_close(indices.cpu().long(), nearest)
        weights = torch.softmax(-distances, -1)
        interpolated = _ext.three_interpolate(values, indices, weights)
        selected = features[:, :, None, :].expand(-1, -1, 7, -1).gather(
            3, nearest[:, None].expand(-1, 4, -1, -1))
        torch.testing.assert_close(interpolated.cpu(),
                                   (selected * weights.cpu()[:, None]).sum(-1))

        balls = _ext.ball_query(queries, gpu, .25, 8)
        torch.testing.assert_close(balls.cpu(), neighbors(xyz, centers, 8, radius=.25))
        grouped = _ext.group_points(values, balls)
        expected_group = features[:, :, None, :].expand(-1, -1, 7, -1).gather(
            3, balls.cpu().long()[:, None].expand(-1, 4, -1, -1))
        torch.testing.assert_close(grouped.cpu(), expected_group)

        angle = torch.tensor(.6)
        rotation = torch.tensor([[angle.cos(), -angle.sin(), 0],
                                 [angle.sin(), angle.cos(), 0], [0, 0, 1]])
        rotation = rotation.expand(2, 7, 3, 3).contiguous()
        cylinders = _ext.cylinder_query(queries, gpu, rotation.cuda().reshape(2, 7, 9),
                                        .25, -.1, .1, 8)
        torch.testing.assert_close(cylinders.cpu(),
                                   neighbors(xyz, centers, 8, radius=.25, rotation=rotation))

        # Upstream KNN uses one-based int64 indices. Compare both batches on CPU and CUDA.
        for device in ("cpu", "cuda"):
            ref = xyz.transpose(1, 2).contiguous().to(device)
            query = centers.transpose(1, 2).contiguous().to(device)
            output = torch.full((2, 3, 7), -999, dtype=torch.int64, device=device)
            knn_pytorch.knn(ref, query, output)
            torch.testing.assert_close(output.cpu(), nearest.transpose(1, 2) + 1)
    stream.synchronize()
    return ["fps", "gather", "three_nn", "interpolate", "ball_query", "group",
            "rotated_cylinder_query", "knn_cpu_int64", "knn_cuda_int64"]


def network(module):
    torch.manual_seed(7)
    model = module.GraspNet(input_feature_dim=0, is_training=False).cuda().eval()
    cloud = torch.rand(1, 20000, 3, device="cuda") * .1
    cloud[:, :, 2] += .3
    torch.cuda.synchronize()
    started = time.perf_counter()
    with torch.inference_mode():
        output = model({"point_clouds": cloud})
        for name in ("objectness_score", "grasp_score_pred", "grasp_width_pred",
                     "grasp_tolerance_pred", "grasp_top_view_rot", "fp2_xyz"):
            assert torch.isfinite(output[name]).all(), name
        rows, = module.pred_decode(output)
        assert rows.ndim == 2 and rows.shape[1] == 17 and torch.isfinite(rows).all()
    torch.cuda.synchronize()
    return {"status": "passed", "initialization": "random_untrained", "input_points": 20000,
            "parameters": sum(p.numel() for p in model.parameters()),
            "forward_and_decode_seconds": time.perf_counter() - started,
            "output_rows": len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / ".local/src/graspnet-baseline")
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--input", type=Path, help="Local NPZ containing points (H,W,3) and mask")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/graspnet_sdk.json")
    args = parser.parse_args()
    if any((args.weights, args.sha256, args.input)) and not all((
        args.weights, args.sha256, args.input,
    )):
        parser.error("--weights, --sha256 and --input must be provided together")
    if not torch.cuda.is_available():
        raise RuntimeError("GraspNet operators require CUDA")
    module = baseline_module(args.source, REVISION)
    checks = operators()
    report = {"source_revision": REVISION, "torch": torch.__version__,
              "gpu": torch.cuda.get_device_name(), "operators_passed": checks,
              "untrained_network": network(module), "trained_inference": "not_tested",
              "physical_grasp": "not_tested"}
    if args.weights:
        with np.load(args.input, allow_pickle=False) as data:
            points, mask = data["points"], data["mask"]
            metadata = json.loads(str(data["metadata"])) if "metadata" in data else None
        if (points.ndim != 3 or points.shape[2] != 3 or mask.shape != points.shape[:2]
                or mask.dtype != np.bool_ or points.dtype != np.float32):
            raise ValueError("Expected float32 XYZ in meters and a same-grid bool mask")
        predictor = GraspNetPredictor(args.weights, args.sha256, source=args.source,
                                      source_revision=REVISION)
        candidates = predictor.infer(points, mask)
        with args.input.open("rb") as source:
            input_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        valid = mask & np.isfinite(points).all(axis=2) & (points[:, :, 2] > 0)
        report.update(trained_inference="passed", weights_sha256=args.sha256,
                      input={"path": str(args.input), "sha256": input_sha256,
                             "shape": list(points.shape), "mask_pixels": int(mask.sum()),
                             "valid_target_points": int(valid.sum()), "metadata": metadata},
                      candidate_count=len(candidates), collision_checked=False,
                      convention="graspnet_camera_x_approach_y_closing",
                      score_kind="graspnet_quality",
                      candidates=[item.model_dump() for item in candidates])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.report.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
