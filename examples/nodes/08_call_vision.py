"""终端三：用 /tmp/vision-test.jpg 调用检测和分割，再用点云调用抓取。"""

import random
import struct
from pathlib import Path

from examples._session import use_local_token
from wrs_agent import connect

IMAGE = "/tmp/vision-test.jpg"
CLOUD = "/tmp/graspnet-test.npy"


def _write_cloud(path):
    rng = random.Random(0)
    body = b"".join(
        struct.pack("<fff", rng.gauss(0, 0.05), rng.gauss(0, 0.05), rng.gauss(0, 0.05) + 0.4)
        for _ in range(2048)
    )
    header = "{'descr': '<f4', 'fortran_order': False, 'shape': (2048, 3), }"
    pad = (16 - ((10 + len(header) + 1) % 16)) % 16
    header = f"{header}{' ' * pad}\n"
    blob = b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("ascii") + body
    Path(path).write_bytes(blob)


def main():
    _write_cloud(CLOUD)
    use_local_token("nodes")
    calls = (
        ("detect", "yolo", {"image": IMAGE}),
        ("segment", "sam3", {"image": IMAGE, "text": "orange"}),
        ("infer_grasps", "graspnet", {"cloud": CLOUD, "top": 3}),
    )
    with connect("tcp/127.0.0.1:7448", env_id="node-demo") as system:
        for skill, node, args in calls:
            status = system.action(skill, **args).wait(timeout=180)
            shot = system.snapshot(node)
            detail = shot.data.summary or status.reason
            print(skill, status.state, detail, flush=True)


if __name__ == "__main__":
    main()
