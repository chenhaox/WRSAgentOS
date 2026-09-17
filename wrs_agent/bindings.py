"""Small, validated deployment bindings; model output cannot select endpoints."""

import tomllib
from pathlib import Path

from wrs_agent.skills import SPECS


def load_bindings(path=None):
    path = path or Path(__file__).resolve().parents[1] / "configs/bindings.toml"
    config = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    if set(config) != {"nodes", "skills"} or set(config["nodes"]) != {"wrs", "tts"}:
        raise ValueError("invalid_bindings")
    if set(config["skills"]) != set(SPECS):
        raise ValueError("incomplete_bindings")
    suffixes = {}
    for node, settings in config["nodes"].items():
        if set(settings) != {"suffix"} or settings["suffix"] not in {"", "-tts"}:
            raise ValueError("invalid_endpoint_binding")
        suffixes[node] = settings["suffix"]
    if len(set(suffixes.values())) != len(suffixes):
        raise ValueError("duplicate_execution_endpoint")
    for skill, node in config["skills"].items():
        if node != SPECS[skill].node:
            raise ValueError("binding_contract_mismatch")
    return suffixes, config["skills"]
