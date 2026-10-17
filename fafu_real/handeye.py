"""眼在手上标定文件读写，格式和比赛 demo 的 handeye_eye_in_hand.json 完全一样。

    世界点 = FK(当前关节的法兰) @ T_flange_cam @ 相机点

demo 里已经标好的文件可以直接拷过来用（HANDEYE_JSON 指过去也行）。
"""

from __future__ import annotations

import json
import os

import fafu_config as C
import numpy as np


class HandEyeError(RuntimeError):
    pass


def load_raw(path: str | None = None) -> np.ndarray:
    """标定脚本用：文件不存在返回单位阵，不报错。"""
    path = path or C.HANDEYE_JSON
    if not os.path.isfile(path):
        return np.eye(4)
    with open(path, encoding="utf-8-sig") as f:
        mat = np.asarray(json.load(f).get("affine_mat"), dtype=float)
    if mat.shape != (4, 4):
        raise HandEyeError(f"{path} 的 affine_mat 不是 4x4")
    return mat


def load(path: str | None = None) -> np.ndarray:
    """识别 / 读坐标用：没标定（文件不存在或还是单位阵）就报错。"""
    path = path or C.HANDEYE_JSON
    if not os.path.isfile(path):
        raise HandEyeError(
            f"还没有眼在手上标定文件：{path}，先跑 python fafu_real/hand_eye_calib.py"
        )
    mat = load_raw(path)
    if np.allclose(mat, np.eye(4)):
        raise HandEyeError(f"{path} 里还是单位阵，不能当标定用")
    return mat


def save(mat, path: str | None = None) -> str:
    path = path or C.HANDEYE_JSON
    mat = np.asarray(mat, dtype=float)
    if mat.shape != (4, 4):
        raise HandEyeError(f"affine_mat 必须是 4x4，收到 {mat.shape}")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"mode": "eye_in_hand", "affine_mat": mat.tolist(), "note": "T_flange_cam 法兰→相机"},
            f,
            ensure_ascii=False,
            indent=2,
        )
    return os.path.abspath(path)


def describe(mat) -> str:
    mat = np.asarray(mat, dtype=float)
    pos = mat[:3, 3]
    return f"法兰→相机 平移 [{pos[0]:+.4f}, {pos[1]:+.4f}, {pos[2]:+.4f}]m" + (
        "  （还是单位阵，未标定）" if np.allclose(mat, np.eye(4)) else ""
    )


def camera_pose(flange: np.ndarray, mat: np.ndarray) -> np.ndarray:
    """相机在世界系的位姿；第三列是视线方向。"""
    return np.asarray(flange, dtype=float) @ np.asarray(mat, dtype=float)


def try_load() -> np.ndarray | None:
    try:
        return load()
    except HandEyeError:
        return None
