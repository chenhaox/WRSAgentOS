"""把比赛仓库（fafu SDK、demo/real、带 Panthera-HT 的 wrs）挂到 sys.path 最前面。

本仓库 third_party/wrs 是另一版 WRS，没有 Panthera-HT，接口也不同。实机脚本里的
``import wrs`` 必须解析到比赛仓库那份，所以每个入口脚本最先 import 本模块。
"""

from __future__ import annotations

import os
import sys

import numpy as np

# 比赛仓库自带的 trimesh 用 np.fromstring 读 STL 二进制。NumPy 2 删了这种用法，
# 这里在导入 wrs 之前改回 frombuffer，文本形式（sep 非空）仍走原来的函数。
_np_fromstring = np.fromstring


def _fromstring_compat(string, dtype=float, count=-1, sep="", **kwargs):
    if sep == "" and isinstance(string, (bytes, bytearray, memoryview)):
        return np.frombuffer(string, dtype=dtype, count=count)
    return _np_fromstring(string, dtype=dtype, count=count, sep=sep, **kwargs)


np.fromstring = _fromstring_compat

HERE = os.path.dirname(os.path.abspath(__file__))
AGENTOS_ROOT = os.path.dirname(HERE)

import fafu_config as C  # noqa: E402

COMPETITION_ROOT = os.path.abspath(C.COMPETITION_ROOT)
REAL_DIR = os.path.join(COMPETITION_ROOT, "demo", "real")
SDK_DIR = os.path.join(COMPETITION_ROOT, "fafu_arm_sdk-main", "fafu_robot_python")
PANTHERA_MESHES = os.path.join(
    COMPETITION_ROOT, "wrs", "robot_sim", "manipulators", "panthera_ht", "meshes1"
)


def _require(path: str, what: str) -> None:
    if not os.path.exists(path):
        raise SystemExit(
            f"找不到{what}：{path}\n"
            "把 fafu_config.COMPETITION_ROOT（或环境变量 WRS_COMPETITION_DIR）指向 "
            "wrs-competition 仓库（wrs-demo 分支）的根目录。"
        )


_require(os.path.join(REAL_DIR, "real_arm.py"), " demo/real 真机执行层")
_require(os.path.join(SDK_DIR, "robot.cfg"), " fafu SDK 的 robot.cfg")
_require(
    os.path.join(COMPETITION_ROOT, "wrs", "robot_sim", "robots", "panthera_ht"),
    "比赛仓库里的 Panthera-HT 模型",
)
if not os.path.isdir(PANTHERA_MESHES):
    raise SystemExit(
        f"Panthera-HT 网格不在：{PANTHERA_MESHES}\n"
        "比赛仓库的 .gitignore 排除了 *.stl，从 GitHub 拉下来的副本没有网格；"
        "请用你本机带 meshes1/ 的那份仓库。"
    )

loaded = sys.modules.get("wrs")
if loaded is not None and not os.path.abspath(loaded.__file__).startswith(COMPETITION_ROOT):
    raise SystemExit(f"已经导入了另一份 wrs：{loaded.__file__}；实机脚本要用比赛仓库的 wrs。")

for path in (AGENTOS_ROOT, REAL_DIR, COMPETITION_ROOT):
    while path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)
os.environ.setdefault("FAFU_SDK_DIR", SDK_DIR)
