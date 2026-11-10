"""Turn one vision result into a short sentence for the TTS node."""

import re
import unicodedata

_NAMES = {
    "person": "人",
    "bicycle": "自行车",
    "car": "汽车",
    "motorcycle": "摩托车",
    "airplane": "飞机",
    "bus": "公共汽车",
    "train": "火车",
    "truck": "卡车",
    "boat": "船",
    "traffic light": "红绿灯",
    "fire hydrant": "消防栓",
    "stop sign": "停止标志",
    "parking meter": "停车计时器",
    "bench": "长凳",
    "bird": "鸟",
    "cat": "猫",
    "dog": "狗",
    "horse": "马",
    "sheep": "羊",
    "cow": "牛",
    "elephant": "大象",
    "bear": "熊",
    "zebra": "斑马",
    "giraffe": "长颈鹿",
    "backpack": "背包",
    "umbrella": "伞",
    "handbag": "手提包",
    "tie": "领带",
    "suitcase": "行李箱",
    "frisbee": "飞盘",
    "skis": "滑雪板",
    "snowboard": "单板",
    "sports ball": "球",
    "kite": "风筝",
    "baseball bat": "棒球棒",
    "baseball glove": "棒球手套",
    "skateboard": "滑板",
    "surfboard": "冲浪板",
    "tennis racket": "网球拍",
    "bottle": "瓶子",
    "wine glass": "酒杯",
    "cup": "杯子",
    "fork": "叉子",
    "knife": "刀",
    "spoon": "勺子",
    "bowl": "碗",
    "banana": "香蕉",
    "apple": "苹果",
    "sandwich": "三明治",
    "orange": "橙子",
    "broccoli": "西兰花",
    "carrot": "胡萝卜",
    "hot dog": "热狗",
    "pizza": "披萨",
    "donut": "甜甜圈",
    "cake": "蛋糕",
    "chair": "椅子",
    "couch": "沙发",
    "potted plant": "盆栽",
    "plant": "植物",
    "bed": "床",
    "dining table": "餐桌",
    "toilet": "马桶",
    "tv": "电视",
    "laptop": "笔记本电脑",
    "mouse": "鼠标",
    "remote": "遥控器",
    "keyboard": "键盘",
    "cell phone": "手机",
    "microwave": "微波炉",
    "oven": "烤箱",
    "toaster": "烤面包机",
    "sink": "水槽",
    "refrigerator": "冰箱",
    "book": "书",
    "clock": "时钟",
    "vase": "花瓶",
    "scissors": "剪刀",
    "teddy bear": "泰迪熊",
    "hair drier": "吹风机",
    "toothbrush": "牙刷",
    "box": "盒子",
    "phone": "手机",
    "object": "物体",
}
_COUNT = {2: "两", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八"}


def object_name(label):
    """Use a spoken Chinese name when the detector returns an English class."""
    text = str(label).strip()
    return _NAMES.get(text.lower(), text or "物体")


_SPOKEN = {}
for _english, _chinese in _NAMES.items():
    _SPOKEN.setdefault(_chinese, _english)
_GRASP = re.compile(
    r"(?:抓取|抓起|抓住|拿起|拿|抓|grasp|pick(?:\s+up)?)"
    r"\s*(?:一下|the|a|an|那个|这个)?\s*(.+)"
)


def grasp_label(text):
    """English detector label named by a grasp command, or None."""
    raw = unicodedata.normalize("NFKC", str(text)).strip().lower()
    raw = raw.rstrip("。.!！?？,， ")
    if not raw or re.search(r"不|别|\bnot\b|don't", raw):
        return None
    found = _GRASP.search(raw)
    if found is None:
        return None
    name = found.group(1).strip()
    if not name:
        return None
    if name in _SPOKEN:
        return _SPOKEN[name]
    return name if name in _NAMES else None


def grasp_prompt(text):
    """SAM prompt for a grasp sentence. Known names become detector labels."""
    raw = unicodedata.normalize("NFKC", str(text)).strip().lower()
    raw = raw.rstrip("。.!！?？,， ")
    if not raw or re.search(r"不|别|\bnot\b|don't", raw):
        return None
    found = _GRASP.search(raw)
    if found is None:
        return None
    name = found.group(1).strip()
    if not name or len(name) > 32:
        return None
    if name in _SPOKEN:
        return _SPOKEN[name]
    if name in _NAMES:
        return name
    return name


def grasp_instruction():
    """Ask a model for a short visual phrase, not a fixed class name."""
    return (
        "用户想让机械臂抓一个物体。"
        "用不超过四个英文单词描述画面里要找的东西，可以包含颜色或形状，不要限于固定类别。"
        "只输出这个短语，不要解释，不要逗号。"
        "不是抓取时只输出 none。"
    )


def _choice_line(text):
    raw = unicodedata.normalize("NFKC", str(text)).strip().lower()
    raw = raw.strip("`\"'“”")
    line = raw.splitlines()[0].strip() if raw else ""
    return line.rstrip("。.!！?？,， ")


def grasp_declined(text):
    """True when the model says this sentence is not a grasp."""
    return _choice_line(text) in {"", "none", "null", "无", "没有"}


def grasp_choice(text):
    """English detector label from one model line, or None."""
    line = _choice_line(text)
    if line in {"", "none", "null", "无", "没有"}:
        return None
    if line in _NAMES and line != "object":
        return line
    label = _SPOKEN.get(line)
    if label is not None and label != "object":
        return label
    return None


def grasp_query(text):
    """Short open-vocabulary prompt from one model line, or None."""
    line = _choice_line(text)
    if line in {"", "none", "null", "无", "没有"}:
        return None
    mapped = _SPOKEN.get(line)
    if mapped is not None and mapped != "object":
        line = mapped
    line = " ".join(line.replace(",", " ").split())
    return line[:40].strip() or None


def best_match(items, label):
    """Highest-scoring detection of this grasp label, or None."""
    wanted = object_name(label)
    hits = []
    for item in items:
        raw = item.label if hasattr(item, "label") else item.get("label")
        score = item.score if hasattr(item, "score") else item.get("score", 0)
        text = str(raw or "")
        if text.lower() == str(label).lower() or object_name(text) == wanted:
            hits.append((float(score), item))
    if not hits:
        return None
    return max(hits, key=lambda pair: pair[0])[1]


def spoken_objects(items):
    """One sentence listing detected labels. Repeated labels are counted."""
    counts = {}
    order = []
    for item in items:
        name = object_name(getattr(item, "label", None) or item["label"])
        if name not in counts:
            order.append(name)
            counts[name] = 0
        counts[name] += 1
    if not order:
        return "没有识别到桌面上的物体。"
    parts = [
        name if counts[name] == 1 else f"{_COUNT.get(counts[name], counts[name])}个{name}"
        for name in order
    ]
    listed = parts[0] if len(parts) == 1 else f"{'、'.join(parts[:-1])}和{parts[-1]}"
    return f"桌面上有{listed}。"[:512]
