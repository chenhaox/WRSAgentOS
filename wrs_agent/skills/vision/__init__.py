"""Visual descriptions as ordinary functions, without model SDK or transport imports."""

import asyncio
import time
from collections import Counter

from pydantic import Field

from wrs_agent.executor import SkillFailure
from wrs_agent.schemas import Boundary, Name
from wrs_agent.skills.contracts import Skill

# Unknown model labels remain intact rather than being silently renamed.
LABELS = {
    "person": "人", "bottle": "瓶子", "cup": "杯子", "wine glass": "高脚杯",
    "bowl": "碗", "fork": "叉子", "knife": "刀", "spoon": "勺子",
    "banana": "香蕉", "apple": "苹果", "orange": "橙子", "sandwich": "三明治",
    "book": "书", "cell phone": "手机", "laptop": "笔记本电脑", "mouse": "鼠标",
    "keyboard": "键盘", "remote": "遥控器", "scissors": "剪刀", "clock": "时钟",
    "potted plant": "盆栽", "chair": "椅子", "vase": "花瓶", "teddy bear": "玩具熊",
}


class DescribeArgs(Boundary):
    max_age_s: float = Field(default=2.0, ge=0.1, le=5)


class Description(Boundary):
    text: str = Field(min_length=1, max_length=512)
    result_id: Name
    observation_id: Name


def describe_result(result):
    scope = "桌面区域" if result.scope == "table_region" else "画面中"
    if result.source == "synthetic":
        scope = "合成测试" + scope
    counts = Counter(item.label for item in result.items)
    if not counts:
        return f"{scope}未识别到物体。"
    names = [f"{LABELS.get(label, label)}{count}个" for label, count in sorted(counts.items())]
    # Limit sentence size explicitly, even with a custom class vocabulary.
    visible = []
    for name in names:
        if len("、".join([*visible, name])) > 450:
            break
        visible.append(name)
    suffix = "等物体" if len(visible) < len(names) else ""
    return f"{scope}识别到：{'、'.join(visible)}{suffix}。"


async def describe_objects(state, args, stop, progress):
    deadline = time.monotonic() + 5.0
    while not stop.is_set():
        result = state.latest
        if result is not None and time.monotonic() - state.observed_at <= args.max_age_s:
            text = describe_result(result)
            state.completed += 1
            return Description(text=text, result_id=result.result_id,
                               observation_id=result.observation_id)
        if time.monotonic() >= deadline:
            raise SkillFailure(state.health if state.health != "ready" else "vision_stale")
        await asyncio.sleep(0.05)
    return False


DESCRIBE = Skill(
    name="describe_objects", arguments=DescribeArgs, output=Description,
    handler=describe_objects, resources=("vision_description",), timeout=6,
    description="Describe detected objects in a fresh camera observation; return text for speech.",
    instructions="To announce what is visible, include this skill as a plan step, then a speak "
                 "step with inputs.text referencing this step's output field text and depends_on "
                 "naming this step. Submit the plan through propose_plan, not a direct skill call. "
                 "Do not invent an object list. "
                 "Scope is the image or configured table region, without 3D support verification.",
    verification="description_bound_to_observation",
)
