import asyncio

import pytest
from conftest import action, control, eventually

from wrs_agent.environments.mock import make_mock_environment
from wrs_agent.schemas import TERMINAL


async def test_action_and_control_idempotence_and_fencing(make_env):
    env = make_env(duration=1)
    request = action(env)
    try:
        first = await env.submit(request)
        assert first.accepted
        assert (await env.submit(request)).accepted
        conflict = request.model_copy(update={"args": {"object": "D"}})
        assert (await env.submit(conflict)).reason == "action_id_conflict"
        await eventually(lambda: env.status(request.action_id), lambda s: s.state == "RUNNING")
        stop = control(env)
        received = await env.hold(stop)
        assert received.phase == "STOPPING"
        assert env.epoch == 1
        assert await env.hold(stop) == received
        assert env.epoch == 1
        changed = stop.model_copy(update={"action_id": request.action_id})
        assert (await env.hold(changed)).reason == "interrupt_id_conflict"
        await eventually(lambda: env.status(request.action_id), lambda s: s.state == "CANCELLED")
        assert env.executions == 1
        assert (await env.submit(action(env))).reason == "admission_closed"
        stale = request.model_copy(update={"action_id": "late"})
        assert (await env.submit(stale)).reason == "stale_epoch"
        assert not (await env.control("resume", control(env), authorized=False)).accepted
        assert (await env.resume(control(env, world_version=env.world.version))).accepted
        assert env.executions == 1  # Resume doesn't revive trajectories.
    finally:
        await env.close()


async def test_control_does_not_wait_for_intent_write(make_env):
    env = make_env(duration=1)
    entered, release = asyncio.Event(), asyncio.Event()
    original = env.journal.save

    async def slow_write(request, status):
        entered.set()
        await release.wait()
        await original(request, status)

    env.journal.save = slow_write
    pending = asyncio.create_task(env.submit(action(env)))
    await entered.wait()
    held = await asyncio.wait_for(env.hold(control(env)), 0.1)
    assert held.accepted and env.epoch == 1
    release.set()
    await pending
    assert env.executions == 0
    await env.close()


@pytest.mark.parametrize(
    "fault,state,verification",
    [
        ("grasp", "FAILED", "FAIL"),
        ("localization", "FAILED", "FAIL"),
        ("unknown", "UNKNOWN", "INCONCLUSIVE"),
        ("inconclusive", "UNKNOWN", "INCONCLUSIVE"),
    ],
)
async def test_faults_do_not_claim_success(make_env, fault, state, verification):
    env = make_env(duration=0.02, fault=fault)
    request = action(env)
    try:
        await env.submit(request)
        result = await eventually(
            lambda: env.status(request.action_id), lambda s: s.state in TERMINAL
        )
        assert result.state == state and result.verification == verification
        assert env.world.held is None
        if state == "UNKNOWN":
            assert not (await env.resume(control(env, world_version=env.world.version))).accepted
            assert not (await env.submit(action(env))).accepted
    finally:
        await env.close()


async def test_single_resource_and_cancel_target(make_env):
    env = make_env(duration=0.1)
    try:
        request = action(env)
        await env.submit(request)
        assert (await env.submit(action(env))).reason == "resource_busy"
        assert not (await env.cancel(control(env, action_id="missing"))).accepted
        assert (await env.cancel(control(env, action_id=request.action_id))).accepted
    finally:
        await env.close()


async def test_restart_marks_inflight_unknown_and_rejects_old_boot(tmp_path):
    path = tmp_path / "restart.sqlite3"
    env = make_mock_environment(path)
    old = action(env)
    from wrs_agent.schemas import ActionStatus

    await env.journal.save(old.model_dump(), ActionStatus(action_id=old.action_id, state="RUNNING"))
    await env.close()
    restarted = make_mock_environment(path)
    try:
        assert restarted.boot_id != old.boot_id
        assert restarted.status(old.action_id).state == "UNKNOWN"
        assert restarted.admission == "UNKNOWN"
        assert restarted.executions == 0
        assert (
            await restarted.submit(old.model_copy(update={"action_id": "late"}))
        ).reason == "stale_boot"
    finally:
        await restarted.close()


async def test_unknown_stop_never_resumes(make_env):
    env = make_env(duration=1, fault="stop_unknown")
    try:
        req = action(env)
        await env.submit(req)
        await eventually(lambda: env.status(req.action_id), lambda s: s.state == "RUNNING")
        await env.hold(control(env))
        await eventually(lambda: env.status(req.action_id), lambda s: s.state == "UNKNOWN")
        assert not (await env.resume(control(env, world_version=env.world.version))).accepted
    finally:
        await env.close()


async def test_expired_grant_and_old_revision_cannot_execute(make_env):
    env = make_env(duration=0.01)
    try:
        expired = action(env)
        epoch, version, _ = env.leases[expired.lease_id]
        env.leases[expired.lease_id] = (epoch, version, 0.0)
        assert (await env.submit(expired)).reason == "expired_lease"
        current = action(env, task_revision=2)
        await env.submit(current)
        await eventually(lambda: env.status(current.action_id), lambda s: s.state == "SUCCEEDED")
        old = action(env, skill="place", args={"object": "A", "target": "B"}, task_revision=1)
        assert (await env.submit(old)).reason == "stale_revision"
        assert env.executions == 1
    finally:
        await env.close()
