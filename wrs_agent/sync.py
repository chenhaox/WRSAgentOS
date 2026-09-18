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
    raise RuntimeError("Use 'async with System.local()' or 'System.connect()' inside async code.")


class Action:
    """A submitted action. wait() returns its terminal status, including failure."""

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
    """Single-thread script calls into System; the local loop runs only during calls.

    Remote nodes keep running between calls. A blocking wait occupies the caller;
    use asynchronous System for concurrent control through the same client.
    """

    def __init__(self, runner, system):
        self._runner, self._system = runner, system
        self._thread = threading.get_ident()
        self._closed = False

    @property
    def endpoint(self):
        return self._system.endpoint

    @property
    def site(self):
        return self._system.site

    @property
    def env_id(self):
        return self._system.env_id

    def _call(self, method, *args, **kwargs):
        if self._closed:
            raise RuntimeError(
                "Session is closed; use commands inside 'with launch()' or 'with connect()'."
            )
        if threading.get_ident() != self._thread:
            raise RuntimeError("Use this synchronous session from its owning thread.")
        _require_sync()
        return self._runner.run(method(*args, **kwargs))

    def nodes(self):
        return self._call(self._system.nodes)

    def skills(self, query=""):
        return self._call(self._system.skills, query)

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


def launch(*, backend="mock", duration=0.4, bindings=None, port=0, site="local", env_id=None):
    """Start configured local nodes; close owned nodes and router on exit."""
    return _session(
        System.local(
            backend=backend,
            duration=duration,
            bindings=bindings,
            port=port,
            site=site,
            env_id=env_id,
        )
    )


def connect(endpoint="tcp/127.0.0.1:7447", *, site="local", env_id="arm01", bindings=None):
    """Connect to running nodes with WRS_AGENT_TOKEN; exit only closes this connection."""
    return _session(System.connect(endpoint, site=site, env_id=env_id, bindings=bindings))


@contextmanager
def _session(context):
    _require_sync()
    with asyncio.Runner() as runner:
        session = Session(runner, runner.run(context.__aenter__()))
        try:
            yield session
        finally:
            try:
                runner.run(context.__aexit__(None, None, None))
            finally:
                session._closed = True
