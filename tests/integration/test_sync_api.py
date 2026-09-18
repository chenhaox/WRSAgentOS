import time

import pytest

from wrs_agent import launch, step

pytestmark = pytest.mark.zenoh


def test_sync_actions_run_in_parallel_while_caller_is_idle():
    with launch(duration=0.5) as system:
        speech = system.action("speak", text="working")
        motion = system.action("move_named_pose", pose="B")
        assert speech.receipt.status.state == motion.receipt.status.state == "ACCEPTED"
        assert system.snapshot().active_action == motion.id
        assert speech.status().state in {"ACCEPTED", "RUNNING"}
        # No client event loop runs during this ordinary blocking Python call.
        time.sleep(0.7)
        assert speech.status().state == motion.status().state == "SUCCEEDED"
        assert motion.wait().verification == "PASS"
        assert system.snapshot().data.pose == "B"


def test_sync_wait_timeout_and_scoped_cancel_preserve_remote_control():
    with launch(duration=2) as system:
        speech = system.action("speak", text="working")
        motion = system.action("move_named_pose", pose="B")
        epoch = system.snapshot().control_epoch
        with pytest.raises(TimeoutError):
            motion.wait(timeout=0.02)
        assert motion.status().state in {"ACCEPTED", "RUNNING"}
        first = speech.cancel()
        assert first.accepted and speech.wait().state == "CANCELLED"
        assert speech.cancel() == first
        assert system.snapshot().control_epoch == epoch
        assert system.snapshot().active_action == motion.id
        assert system.replay("stop")["accepted"]
        assert motion.wait().state == "CANCELLED"
        assert system.snapshot().stop_confirmed
        assert system.resume().accepted
        assert motion.status().state == "CANCELLED"


def test_sync_task_watch_dependencies_and_exception_cleanup():
    with pytest.raises(LookupError, match="user_script_error"):
        with launch(duration=0.04) as system:
            processes = list(system._system._local_stack.processes)
            observe = step("observe")
            pick = step("pick", object="A", after=observe)
            place = step("place", object="A", target="B", after=pick)
            verify = step("verify", object="A", target="B", after=place)
            system.start(step("speak", text="hello"), observe, pick, place, verify)
            states = list(system.watch())
            assert states[-1]["state"] == "SUCCEEDED"
            assert states[-1]["planner_calls"] == 0
            assert system.snapshot().data.objects["A"] == "B"
            raise LookupError("user_script_error")
    assert all(p.poll() is not None for p in processes)
    with pytest.raises(RuntimeError, match="closed"):
        system.status()


def test_sync_keyboard_interrupt_cleans_owned_processes():
    with pytest.raises(KeyboardInterrupt):
        with launch(duration=2) as system:
            processes = list(system._system._local_stack.processes)
            action = system.action("move_named_pose", pose="B")
            raise KeyboardInterrupt
    assert all(p.poll() is not None for p in processes)
    with pytest.raises(RuntimeError, match="closed"):
        action.status()


def test_sync_skill_lookup_uses_current_nodes_without_executing_or_planning():
    with launch() as system:
        skills = system.skills("播报当前状态")
        assert [skill.name for skill in skills] == ["speak"]
        assert "name: speech" in skills[0].instructions
        skills[0].aliases.append("caller-only-change")
        assert "caller-only-change" not in system.skills("播报")[0].aliases
        status = system.status()
        assert status["planner_calls"] == 0 and status["active_actions"] == {}
        assert system.snapshot().data.objects == {"A": "table", "D": "table"}


def test_sync_goals_reuse_verified_remote_plans_with_fresh_action_ids():
    with launch(duration=0.02) as system:
        counts = []
        for goal in ["put A in B", "put A in B", "put A in B", "put A in C"]:
            ack = system.goal(goal)
            assert ack["accepted"] and ack["request_id"]
            result = system.wait()
            assert result["state"] == "SUCCEEDED", result
            counts.append(result["planner_calls"])
        # First run starts on the table; only the third has matching conditions.
        assert counts == [1, 2, 2, 3] and result["cache_hits"] == 1
        history = result["action_history"]
        assert len({a["action_id"] for a in history}) == len(history) == 16
        assert len({a["task_id"] for a in history}) == 4
        assert system.snapshot().data.objects["A"] == "C"
