"""Prepare the pinned upstream RGB-D example for offline perception; no device is opened."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
from build_graspnet import REVISION, ROOT
from PIL import Image
from scipy.io import loadmat


def prepare(source, output):
    source = source.resolve(strict=True)
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=10,
    ).strip()
    if revision != REVISION:
        raise ValueError("graspnet_source_revision_mismatch")
    subprocess.run(["git", "-C", str(source), "diff", "--exit-code", "HEAD", "--",
                    "doc/example_data"], check=True, capture_output=True, timeout=10)
    directory = source / "doc/example_data"
    rgb = np.asarray(Image.open(directory / "color.png").convert("RGB"))
    depth = np.asarray(Image.open(directory / "depth.png"))
    workspace = np.asarray(Image.open(directory / "workspace_mask.png")) > 0
    meta = loadmat(directory / "meta.mat")
    intrinsic = meta["intrinsic_matrix"]
    scale = float(meta["factor_depth"].item())
    if (rgb.shape[:2] != depth.shape or workspace.shape != depth.shape
            or depth.dtype != np.uint16 or intrinsic.shape != (3, 3)
            or not np.isfinite(intrinsic).all() or not np.isfinite(scale) or scale <= 0
            or intrinsic[0, 0] <= 0 or intrinsic[1, 1] <= 0):
        raise ValueError("invalid_example_rgbd")
    # This dataset supplies depth/intrinsics, not SDK vertices. Projection is only offline.
    height, width = depth.shape
    z = depth.astype(np.float32) / scale
    x = (np.arange(width)[None] - intrinsic[0, 2]) * z / intrinsic[0, 0]
    y = (np.arange(height)[:, None] - intrinsic[1, 2]) * z / intrinsic[1, 1]
    points = np.stack((x, y, z), axis=-1).astype(np.float32)
    hashes = {}
    for name in ("color.png", "depth.png", "workspace_mask.png", "meta.mat"):
        with (directory / name).open("rb") as handle:
            hashes[name] = hashlib.file_digest(handle, "sha256").hexdigest()
    metadata = {
        "source_kind": "graspnet_example", "source_revision": revision,
        "source_sha256": hashes, "frame_id": "graspnet-example-camera-optical",
        "grid_hw": list(depth.shape), "intrinsic_matrix": intrinsic.tolist(),
        "depth_units_per_meter": scale, "point_units": "meter",
        "point_source": "offline_pinhole_projection", "live_observation": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, rgb=rgb, depth=depth, points=points, workspace_mask=workspace,
                        metadata=json.dumps(metadata))
    output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n",
                                          encoding="utf-8")
    print(json.dumps({"observation": str(output), "grid_hw": list(depth.shape),
                      "valid_depth_pixels": int((depth > 0).sum()), "live_observation": False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / ".local/src/graspnet-baseline")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/graspnet_example.npz")
    args = parser.parse_args()
    if args.output.suffix != ".npz":
        parser.error("--output must end in .npz")
    prepare(args.source, args.output)


if __name__ == "__main__":
    main()
