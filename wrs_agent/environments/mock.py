"""Mock robot state and verification; unrelated skills live on other nodes."""

from wrs_agent.actions import ActionExecutor
from wrs_agent.skills import FUNCTIONS, ROBOT_SKILLS, VirtualWorld


def make_mock_environment(journal_path, *, duration=0.4, fault=None):
    def perform(skill, world, args, fault):
        if fault == "localization" and skill == "pick":
            raise ValueError("object_not_observed")
        if fault == "grasp" and skill == "pick":
            # A completed virtual trajectory did not acquire the object.
            return world.held == args.object
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
