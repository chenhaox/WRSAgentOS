"""Synchronous script API. Nodes keep running in their own processes between calls."""

import asyncio
import threading
from contextlib import contextmanager

from wrs_agent.system import System


def _require_sync():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("Use 'async with System.local()' inside async code.")


class Action:
    """Acceptance returns promptly. Only wait() waits for verified completion."""

    def __init__(self, session, action):
        self._session, self._action = session, action
        self.id, self.receipt = action.id, action.receipt

    def status(self):
        return self._session._call(self._action.status)

    def wait(self, *, timeout=10):
        return self._session._call(self._action.wait, timeout=timeout)

    def cancel(self):
        return self._session._call(self._action.cancel)


class Session:
    """A thin caller-side facade; all work uses the existing asynchronous System."""

    def __init__(self, runner, system):
        self._runner, self._system = runner, system
        self._thread = threading.get_ident()
        self._closed = False

    def _call(self, method, *args, **kwargs):
        if self._closed:
            raise RuntimeError("Session is closed; use commands inside 'with launch()'.")
        if threading.get_ident() != self._thread:
            raise RuntimeError("Use this synchronous session from its owning thread.")
        _require_sync()
        return self._runner.run(method(*args, **kwargs))

    def nodes(self):
        return self._call(self._system.nodes)

    def start(self, *steps):
        return self._call(self._system.start, *steps)

    def goal(self, text):
        return self._call(self._system.goal, text)

    def status(self):
        return self._call(self._system.status)

    def wait(self, *, timeout=10):
        return self._call(self._system.wait, timeout=timeout)

    def watch(self, *, timeout=10):
        stream = self._system.watch(timeout=timeout)
        try:
            while True:
                try:
                    state = self._call(anext, stream)
                except StopAsyncIteration:
                    return
                yield state
        finally:
            if not self._closed:
                self._call(stream.aclose)

    def action(self, skill, **args):
        return Action(self, self._call(self._system.action, skill, **args))

    def snapshot(self, node=None):
        return self._call(self._system.snapshot, node)

    def resume(self, node=None):
        return self._call(self._system.resume, node)

    def replay(self, kind):
        return self._call(self._system.replay, kind)


@contextmanager
def launch(*, backend="mock", duration=0.4, bindings=None):
    """Start a local system for a plain Python script; close owned nodes on exit."""
    _require_sync()
    with asyncio.Runner() as runner:
        context = System.local(backend=backend, duration=duration, bindings=bindings)
        session = Session(runner, runner.run(context.__aenter__()))
        try:
            yield session
        finally:
            try:
                runner.run(context.__aexit__(None, None, None))
            finally:
                session._closed = True
