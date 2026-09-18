"""Mock TTS has its own process, journal, boot session and control epoch."""

from wrs_agent.actions import ActionExecutor
from wrs_agent.skills import SKILLS, SpeechState


def make_mock_tts(journal_path, *, duration=0.4):
    return ActionExecutor(
        journal_path,
        state=SpeechState(),
        skills={"speak": SKILLS["speak"]},
        backend="mock_tts",
        capabilities_extra={
            "robot_controls": False,
            "controller_flush": False,
            "controlled_stop": False,
        },
        duration=duration,
    )
