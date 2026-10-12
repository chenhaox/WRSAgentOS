"""本例只接受这些完整中文指令。移动距离由这句话里的厘米数决定。"""

import re
import unicodedata

# 指令 -> 技能、参数、播报；播报与运动并行，不承诺运动已经成功。
COMMANDS = {
    "向上": ("move_relative", {"dz": 0.02}, "正在上移。"),
    "向下": ("move_relative", {"dz": -0.02}, "正在下移。"),
    "向左": ("move_relative", {"dy": 0.02}, "正在左移。"),
    "向右": ("move_relative", {"dy": -0.02}, "正在右移。"),
    "向前": ("move_relative", {"dx": 0.02}, "正在前移。"),
    "向后": ("move_relative", {"dx": -0.02}, "正在后移。"),
    "回到初始位置": ("move_named_pose", {"pose": "home"}, "正在回到初始位置。"),
    "打开夹爪": ("set_gripper", {"command": "open"}, "正在打开夹爪。"),
    "关闭夹爪": ("set_gripper", {"command": "close"}, "正在关闭夹爪。"),
}


def normalize(text):
    return unicodedata.normalize("NFKC", text).strip().rstrip("。.!！?？")


_CN_DIGIT = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_MOVE_AXIS = {
    "向上": ("dz", 1, "上"),
    "向下": ("dz", -1, "下"),
    "向左": ("dy", 1, "左"),
    "向右": ("dy", -1, "右"),
    "向前": ("dx", 1, "前"),
    "向后": ("dx", -1, "后"),
}


def _cn_below_hundred(text):
    if not text or text == "零":
        return 0
    if text.startswith("零"):
        text = text[1:]
    if "十" not in text:
        if text not in _CN_DIGIT:
            raise ValueError(text)
        return _CN_DIGIT[text]
    left, right = text.split("十", 1)
    tens = 1 if left == "" else _CN_DIGIT[left]
    ones = 0 if right == "" else _CN_DIGIT[right]
    return tens * 10 + ones


def _cn_number(text):
    if "点" in text:
        whole, frac = text.split("点", 1)
        if not frac or any(char not in _CN_DIGIT for char in frac):
            raise ValueError(text)
        value = float(_cn_below_hundred(whole) if "百" not in whole else _cn_with_hundred(whole))
        scale = 0.1
        for char in frac:
            value += _CN_DIGIT[char] * scale
            scale *= 0.1
        return value
    if "百" in text:
        return float(_cn_with_hundred(text))
    return float(_cn_below_hundred(text))


def _cn_with_hundred(text):
    left, right = text.split("百", 1)
    hundreds = 1 if left == "" else _CN_DIGIT[left]
    return hundreds * 100 + _cn_below_hundred(right)


def _centimeters(token):
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token)
    try:
        return _cn_number(token)
    except (KeyError, ValueError):
        return None


def _format_cm(value):
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:g}"


def parse_centimeter_move(text):
    """Return axis deltas in meters when the utterance names a direction and centimeters."""
    compact = normalize(text).replace(" ", "")
    match = re.fullmatch(
        r"(向上|向下|向左|向右|向前|向后)(?:移动|挪动|走)?(.+?)(?:厘米|公分)",
        compact,
    )
    if match is None:
        return None
    centimeters = _centimeters(match.group(2))
    if centimeters is None or centimeters <= 0:
        return None
    axis, sign, name = _MOVE_AXIS[match.group(1)]
    delta = {"dx": 0.0, "dy": 0.0, "dz": 0.0}
    delta[axis] = sign * centimeters / 100.0
    return delta, f"正在向{name}移动{_format_cm(centimeters)}厘米。"


def fafu_payload(text):
    """把一句已接受的动作口令写成真机进程的一行 JSON。"""
    text = normalize(text)
    parsed = parse_centimeter_move(text)
    if parsed is not None:
        delta, speech = parsed
        return {"cmd": "nudge", **delta}, speech
    skill, parameters, speech = COMMANDS[text]
    if skill == "move_relative":
        raise ValueError("请说移动多少厘米")
    if skill == "move_named_pose":
        return {"cmd": "home"}, speech
    if skill == "set_gripper":
        return {"cmd": parameters["command"]}, speech
    raise ValueError(text)


def classify_desk_command(text):
    """Route one utterance on the combined arm and desk page."""
    from wrs_agent.nodes.vision.announce import asks_what_is_there
    from wrs_agent.policy import text_intent
    from wrs_agent.schemas import TextInput

    text = normalize(text)
    if not text:
        return "ignore"
    if asks_what_is_there(text):
        return "ask"
    if text_intent(TextInput(text=text))[0] in {"stop", "cancel_tts", "query"}:
        return "control"
    if parse_centimeter_move(text) is not None:
        return "move"
    if text in COMMANDS and COMMANDS[text][0] != "move_relative":
        return "move"
    return "ignore"


def addressed_text(text):
    """常开麦克风的循环收音用：普通目标需称呼“机器人”。

    前缀在这里兼两个作用：区分“在对机器人下命令”和房间里的闲聊，以及挡住被麦克风
    收回来的自己的播报（没有回声消除）。按住说话不需要它——按住按钮本身就界定了
    这一句是对机器人说的。明确停止与状态查询两种都不经过唤醒判断。
    """
    from wrs_agent.policy import text_intent
    from wrs_agent.schemas import TextInput

    text = normalize(text)
    if not text:
        return None
    if text_intent(TextInput(text=text))[0] in {"stop", "cancel_tts", "query"}:
        return text
    if not text.startswith("机器人"):
        return None
    text = text[len("机器人"):].lstrip(" ，,。:：!！")
    return text or None
