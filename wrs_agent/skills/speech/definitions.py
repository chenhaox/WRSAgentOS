"""Speech parameters and contract; Mock and audio backends supply their own handlers."""

from importlib.resources import files

from pydantic import Field

from wrs_agent.schemas import Boundary
from wrs_agent.skills.contracts import Skill


class SpeakArgs(Boundary):
    text: str = Field(min_length=1, max_length=512)


SKILLS = {
    "speak": Skill(
        name="speak",
        description="Speak an utterance on the independent TTS node",
        instructions=files(__package__).joinpath("SKILL.md").read_text(encoding="utf-8"),
        resources=("speaker",),
        verification="virtual_utterance_complete",
        arguments=SpeakArgs,
        result_parameters=("text",),
    )
}
