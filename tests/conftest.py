import asyncio

import pytest

from wrs_agent.schemas import ActionRequest, ControlRequest, new_id


def action(env, skill="pick", args=None, **updates):
    world = env.snapshot()
    data = dict(
        action_id=new_id(),
        task_id="task",
        task_revision=0,
        boot_id=world.boot_id,
        control_epoch=world.control_epoch,
        lease_id=world.lease_id,
        world_version=world.world_version,
        skill=skill,
        args=args or {"object": "A"},
    )
    data.update(updates)
    return ActionRequest(**data)


def control(env, **updates):
    data = dict(interrupt_id=new_id(), boot_id=env.boot_id, control_epoch=env.epoch)
    data.update(updates)
    return ControlRequest(**data)


async def eventually(call, predicate, timeout=4):
    async with asyncio.timeout(timeout):
        while True:
            result = call()
            if hasattr(result, "__await__"):
                result = await result
            if predicate(result):
                return result
            await asyncio.sleep(0.005)


@pytest.fixture
def make_env(tmp_path):
    from wrs_agent.environments.mock import make_mock_environment

    created = []

    def make(**kwargs):
        result = make_mock_environment(tmp_path / f"{len(created)}.sqlite3", **kwargs)
        created.append(result)
        return result

    return make
