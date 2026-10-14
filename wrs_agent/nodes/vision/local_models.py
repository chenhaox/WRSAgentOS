"""Local SDK settings and the shared, pinned CLIP asset; never downloads models."""

import json
import os
from importlib.resources import files
from pathlib import Path

from wrs_agent.nodes.vision.artifacts import checkpoint_path


def configure_ultralytics():
    os.environ["YOLO_OFFLINE"] = "true"
    os.environ["YOLO_AUTOINSTALL"] = "false"
    config = Path(__file__).resolve().parents[3] / ".local/ultralytics"
    config.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(config)


def clip_checkpoint(weights):
    entry = json.loads(files("wrs_agent.nodes.segmentation").joinpath(
        "clip_assets.json").read_text("utf-8"))
    return checkpoint_path(weights, entry["sha256"]), entry["sha256"]
