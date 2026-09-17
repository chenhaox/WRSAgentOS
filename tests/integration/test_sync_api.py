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
        assert system.snapshot().pose == "B"


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
            processes = list(system._system.stack.processes)
            observe = step("observe")
            pick = step("pick", object="A", after=observe)
            place = step("place", object="A", target="B", after=pick)
            verify = step("verify", object="A", target="B", after=place)
            system.start(step("speak", text="hello"), observe, pick, place, verify)
            states = list(system.watch())
            assert states[-1]["state"] == "SUCCEEDED"
            assert states[-1]["planner_calls"] == 0
            assert system.snapshot().objects["A"] == "B"
            raise LookupError("user_script_error")
    assert all(p.poll() is not None for p in processes)
    with pytest.raises(RuntimeError, match="closed"):
        system.status()


def test_sync_keyboard_interrupt_cleans_owned_processes():
    with pytest.raises(KeyboardInterrupt):
        with launch(duration=2) as system:
            processes = list(system._system.stack.processes)
            action = system.action("move_named_pose", pose="B")
            raise KeyboardInterrupt
    assert all(p.poll() is not None for p in processes)
    with pytest.raises(RuntimeError, match="closed"):
        action.status()
