"""Mock robot state and verification; unrelated skills live on other nodes."""

from wrs_agent.actions import ActionExecutor, SkillFailure
from wrs_agent.skills import FUNCTIONS, ROBOT_SKILLS, VirtualWorld


def make_mock_environment(journal_path, *, duration=0.4, fault=None):
    remaining = 1 if fault in {"grasp_once", "localization_once"} else None

    def perform(skill, world, args, fault):
        nonlocal remaining
        if skill == "pick" and remaining != 0:
            if fault in {"localization", "localization_once", "grasp", "grasp_once"}:
                if remaining is not None:
                    remaining -= 1
                reason = (
                    "localization_failed" if fault.startswith("localization") else "grasp_failed"
                )
                raise SkillFailure(reason)
        return FUNCTIONS[skill][1](world, args)

    return ActionExecutor(
        journal_path,
        state=VirtualWorld({"A": "table", "D": "table"}),
        perform=perform,
        skills=ROBOT_SKILLS,
        backend="mock",
        duration=duration,
        fault=fault,
    )
