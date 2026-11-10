"""Turn one vision snapshot into a sentence the TTS node can speak."""

from wrs_agent.nodes.vision.speech import object_name


def spoken_name(label):
    text = str(label).strip()
    if not text:
        return ""
    return object_name(text)


def detection_labels(items):
    """Distinct detector labels, in first-seen order."""
    labels = []
    for item in items:
        raw = item.label if hasattr(item, "label") else item.get("label")
        text = str(raw or "").strip().lower()
        if text and text not in labels:
            labels.append(text)
    return tuple(labels)


class UtteranceGate:
    """Ask for a sentence only after the same labels have appeared twice."""

    def __init__(self):
        self.stable = None
        self.hits = 0
        self.done = None

    def needs_sentence(self, labels):
        if labels == self.stable:
            self.hits += 1
        else:
            self.stable, self.hits = labels, 1
        return self.hits >= 2 and labels != self.done

    def remember(self, labels):
        self.done = labels


def asks_what_is_there(text):
    """True when the utterance asks which objects are present."""
    compact = "".join(str(text or "").split())
    if "没有什么" in compact or "没什么" in compact:
        return False
    return any(piece in compact for piece in ("有什么", "有哪些", "有啥"))


def desktop_utterance(items):
    """Name the distinct labels. An empty result is still a sentence."""
    names = []
    for item in items:
        label = item.label if hasattr(item, "label") else item.get("label")
        name = spoken_name(label or "")
        if name and name not in names:
            names.append(name)
    if not names:
        return "桌面上没有识别到物体。"
    if len(names) == 1:
        sentence = f"桌面上有{names[0]}。"
    else:
        sentence = f"桌面上有{'、'.join(names[:-1])}和{names[-1]}。"
    return sentence[:512]
