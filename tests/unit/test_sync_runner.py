import asyncio
import threading
from types import SimpleNamespace

import pytest

from wrs_agent import System, launch
from wrs_agent.sync import Session


async def test_sync_entry_rejects_existing_event_loop_before_starting():
    with pytest.raises(RuntimeError, match="System.local"):
        with launch():
            pytest.fail("synchronous entry must not start in an async caller")


def test_sync_startup_failure_closes_runner():
    with pytest.raises(ValueError, match="unsupported_backend"):
        with launch(backend="unsupported"):
            pytest.fail("invalid backend must not start")


def test_session_rejects_other_threads_and_calls_after_close():
    errors = []
    with asyncio.Runner() as runner:
        session = Session(runner, SimpleNamespace(status=lambda: pytest.fail("must not run")))

        def other_thread():
            try:
                session.status()
            except RuntimeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert errors == ["Use this synchronous session from its owning thread."]
        session._closed = True
        with pytest.raises(RuntimeError, match="closed"):
            session.status()


@pytest.mark.parametrize("wait_timeout", [0.01, None])
async def test_watch_deadline_does_not_cancel_consumer_while_yielded(wait_timeout):
    async def status():
        return {"state": "RUNNING", "planning": "IDLE"}

    system = object.__new__(System)
    system.status = status
    stream = system.watch(timeout=wait_timeout)
    assert (await anext(stream))["state"] == "RUNNING"
    await asyncio.sleep(0.03)  # The caller must not receive an unexpected cancellation.
    if wait_timeout is None:
        assert (await anext(stream))["state"] == "RUNNING"
    else:
        with pytest.raises(TimeoutError):
            await anext(stream)
    await stream.aclose()
