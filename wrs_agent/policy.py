"""Structured replay policy. VAD is never robot stop authority."""


def decide_event(event):
    if event.quoted or event.negated or event.confidence < 0.9:
        return "clarify"
    return {
        "vad": "ignore",
        "ack": "ignore",
        "query": "answer",
        "append": "enqueue",
        "revise": "update",
        "stop": "hold",
        "barge_in": "cancel_tts",
    }[event.kind]
