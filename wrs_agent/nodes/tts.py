"""Mock TTS has its own process, journal, boot session and control epoch."""

from wrs_agent.actions import ActionExecutor
from wrs_agent.skills import FUNCTIONS, SpeechState


def make_mock_tts(journal_path, *, duration=0.4):
    def perform(skill, state, args, fault):
        return FUNCTIONS[skill][1](state, args)

    return ActionExecutor(
        journal_path,
        state=SpeechState(),
        perform=perform,
        skills={"speak"},
        backend="mock_tts",
        duration=duration,
    )
