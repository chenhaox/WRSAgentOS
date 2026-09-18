"""Mock robot state and verification; unrelated skills live on other nodes."""

from dataclasses import replace

from wrs_agent.actions import ActionExecutor, SkillFailure
from wrs_agent.skills import SKILLS, VirtualWorld


def make_mock_environment(journal_path, *, duration=0.4, fault=None):
    remaining = 1 if fault in {"grasp_once", "localization_once"} else None

    def pick(world, args, stop, progress):
        nonlocal remaining
        if remaining != 0:
            if fault in {"localization", "localization_once", "grasp", "grasp_once"}:
                if remaining is not None:
                    remaining -= 1
                reason = (
                    "localization_failed" if fault.startswith("localization") else "grasp_failed"
                )
                raise SkillFailure(reason)
        return SKILLS["pick"].handler(world, args, stop, progress)

    skills = {
        name: SKILLS[name] for name in ("observe", "move_named_pose", "pick", "place", "verify")
    }
    skills["pick"] = replace(skills["pick"], handler=pick)
    return ActionExecutor(
        journal_path,
        state=VirtualWorld({"A": "table", "D": "table"}),
        skills=skills,
        backend="mock",
        duration=duration,
        fault=fault,
    )
