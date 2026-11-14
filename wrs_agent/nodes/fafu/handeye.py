"""Eye-in-hand calibration file. world point = T_world_link6(q) @ T_link6_cam @ camera point."""

import json
from pathlib import Path

import numpy as np

PATH = Path(__file__).resolve().parents[2] / "assets" / "fafu" / "handeye.json"
FRAME = "link6"


class HandEyeError(RuntimeError):
    pass


def load(path=PATH):
    path = Path(path)
    if not path.is_file():
        raise HandEyeError(
            f"还没有手眼标定：{path.name}。先运行 examples/fafu/01_calibrate_handeye.py"
        )
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("frame") != FRAME:
        raise HandEyeError(f"{path.name} 不是相对 {FRAME} 标定的，请重新标定")
    matrix = np.asarray(data.get("matrix"), dtype=float)
    if matrix.shape != (4, 4) or not np.allclose(matrix[3], [0, 0, 0, 1]):
        raise HandEyeError(f"{path.name} 里的 matrix 不是 4x4 齐次矩阵")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-4):
        raise HandEyeError(f"{path.name} 里的旋转不正交")
    return matrix


def save(matrix, *, path=PATH, method="pointcloud_align"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "mode": "eye_in_hand",
        "frame": FRAME,
        "method": method,
        "note": "T_link6_cam：相机系的点左乘它得到 link6 系，再左乘 link6 的正运动学得到世界系",
        "matrix": np.asarray(matrix, dtype=float).tolist(),
    }
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging.replace(path)
    return path


def transform(matrix, points):
    """Apply a 4x4 transform to an (..., 3) array."""
    points = np.asarray(points, dtype=float)
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def camera_pose(link6, matrix):
    """Camera pose in the world; its third column is the viewing direction."""
    return np.asarray(link6, dtype=float) @ np.asarray(matrix, dtype=float)
