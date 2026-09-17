"""Deployment chooses providers. Skill metadata never grants endpoint authority."""

import re
import tomllib
from pathlib import Path

from wrs_agent.skills import SPECS

DEFAULT = Path(__file__).resolve().parents[1] / "configs/bindings.toml"


def load_nodes(path=None):
    config = tomllib.loads(Path(path or DEFAULT).read_text(encoding="utf-8"))
    if set(config) != {"nodes", "skills"} or not 1 <= len(config["nodes"]) <= 16:
        raise ValueError("invalid_bindings")
    if set(config["skills"]) != set(SPECS):
        raise ValueError("incomplete_bindings")
    endpoints = set()
    for name, node in config["nodes"].items():
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name)
            or set(node) != {"type", "suffix", "actions", "enabled"}
            or node["type"] not in {"agent", "wrs", "tts", "voice", "vision"}
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
    for node_id in config["skills"].values():
        if node_id not in config["nodes"] or not config["nodes"][node_id]["actions"]:
            raise ValueError("invalid_skill_provider")
    return config["nodes"], config["skills"]


def load_bindings(path=None):
    nodes, skills = load_nodes(path)
    return {n: d["suffix"] for n, d in nodes.items() if d["actions"]}, skills
