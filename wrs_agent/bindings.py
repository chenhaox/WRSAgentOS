"""Optional launch profiles and explicit skill-provider preferences."""

import re
import tomllib
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "configs/bindings.toml"


def load_bindings(path=None):
    config = tomllib.loads(Path(path or DEFAULT).read_text(encoding="utf-8"))
    if (
        "nodes" not in config or set(config) - {"nodes", "skills"}
        or not isinstance(config["nodes"], dict) or not 1 <= len(config["nodes"]) <= 16
    ):
        raise ValueError("invalid_bindings")
    routes = config.get("skills", {})
    if not isinstance(routes, dict) or len(routes) > 64 or any(
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name) or not isinstance(node, str)
        for name, node in routes.items()
    ):
        raise ValueError("invalid_skill_binding")
    endpoints = set()
    for name, node in config["nodes"].items():
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name)
            or set(node) != {"type", "suffix", "actions", "enabled"}
            or node["type"] not in {
                "agent", "wrs", "tts", "voice", "asr", "vision", "ui", "custom",
            }
            or not isinstance(node["suffix"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{0,40}", node["suffix"])
            or type(node["actions"]) is not bool
            or type(node["enabled"]) is not bool
        ):
            raise ValueError("invalid_node_binding")
        if node["actions"]:
            if node["suffix"] in endpoints:
                raise ValueError("duplicate_execution_endpoint")
            endpoints.add(node["suffix"])
    for node_id in routes.values():
        if node_id not in config["nodes"] or not config["nodes"][node_id]["actions"]:
            raise ValueError("invalid_skill_node")
    return config["nodes"], routes
