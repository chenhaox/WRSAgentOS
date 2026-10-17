"""Bounded vision snapshot. Full arrays stay in the result file."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from wrs_agent.executor import SkillFailure
from wrs_agent.schemas import VisionData, VisionItem


def _number_list(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise SkillFailure("vision_result_invalid")
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        raise SkillFailure("vision_result_invalid") from None


def parse_item(raw):
    if not isinstance(raw, dict):
        raise SkillFailure("vision_result_invalid")
    label = str(raw.get("label") or "object")[:64] or "object"
    try:
        score = float(raw["score"])
    except (KeyError, TypeError, ValueError):
        raise SkillFailure("vision_result_invalid") from None
    if score != score:
        raise SkillFailure("vision_result_invalid")
    data = {"label": label, "score": min(1.0, max(0.0, score))}
    if raw.get("xyxy") is not None:
        data["xyxy"] = _number_list(raw["xyxy"], 4)
    if raw.get("translation") is not None:
        data["translation"] = _number_list(raw["translation"], 3)
    if raw.get("rotation") is not None:
        data["rotation"] = _number_list(raw["rotation"], 9)
    if raw.get("width") is not None:
        try:
            data["width"] = float(raw["width"])
        except (TypeError, ValueError):
            raise SkillFailure("vision_result_invalid") from None
    try:
        return VisionItem.model_validate(data)
    except Exception:
        raise SkillFailure("vision_result_invalid") from None


@dataclass
class VisionState:
    worker: object
    result_dir: Path
    version: int = 0
    completed: int = 0
    task: str = ""
    source: str = ""
    summary: str = ""
    result_path: str = ""
    items: tuple = field(default_factory=tuple)

    def snapshot(self):
        return VisionData(
            completed=self.completed,
            task=self.task,
            source=self.source,
            summary=self.summary,
            result_path=self.result_path,
            items=list(self.items),
        )

    def record(self, task, source, reply):
        if not reply.get("ok"):
            code = reply.get("error")
            if not isinstance(code, str) or not code.isascii() or len(code) > 80:
                code = "inference_failed"
            raise SkillFailure(code)
        raw_items = reply.get("items")
        if not isinstance(raw_items, list) or len(raw_items) > 32:
            raise SkillFailure("vision_result_invalid")
        items = tuple(parse_item(item) for item in raw_items[:8])
        path = self.result_dir / f"{task}-{uuid4().hex}.json"
        path.write_text(
            json.dumps(
                {
                    "task": task,
                    "source": source,
                    "count": reply.get("count", len(raw_items)),
                    "items": raw_items,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        labels = ", ".join(item.label for item in items[:4])
        summary = f"{len(raw_items)} {task}" + (f": {labels}" if labels else "")
        self.completed += 1
        self.task = task
        self.source = source[-240:]
        self.summary = summary[:240]
        self.result_path = str(path)[:240]
        self.items = items
