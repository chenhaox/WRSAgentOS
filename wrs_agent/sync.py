"""Synchronous script API. Nodes keep running in their own processes between calls."""

import asyncio
import threading
from contextlib import contextmanager
from dataclasses import replace

from wrs_agent.system import System


def _require_sync():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("Use 'async with System.launch()' or 'System.connect()' inside async code.")


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


class TaskHandle:
    def __init__(self, session, handle):
        self._session, self._handle = session, handle
        self.id = handle.id

    def status(self):
        return self._session._call(self._handle.status)

    def wait(self, *, timeout=10):
        return self._session._call(self._handle.wait, timeout=timeout)

    def watch(self, *, timeout=10):
        stream = self._handle.watch(timeout=timeout)
        try:
            while True:
                try:
                    state = self._session._call(anext, stream)
                except StopAsyncIteration:
                    return
                yield state
        finally:
            if not self._session._closed:
                self._session._call(stream.aclose)

    def cancel(self):
        return self._session._call(self._handle.cancel)


class GoalHandle:
    def __init__(self, session, handle):
        self._session, self._handle = session, handle
        self.request_id = handle.request_id

    def _wrap(self, result):
        return (
            replace(result, task=TaskHandle(self._session, result.task)) if result.task else result
        )

    def status(self):
        return self._wrap(self._session._call(self._handle.status))

    def wait(self, *, timeout=10):
        return self._wrap(self._session._call(self._handle.wait, timeout=timeout))


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

    def skills(self):
        return self._call(self._system.skills)

    def start(self, *steps):
        return TaskHandle(self, self._call(self._system.start, *steps))

    def goal(self, text):
        return GoalHandle(self, self._call(self._system.goal, text))

    def planning(self, request_id):
        return GoalHandle(self, self._system.planning(request_id))

    def send_text(self, text, *, input_id=None, is_final=True, confidence=1.0):
        return self._call(
            self._system.send_text,
            text,
            input_id=input_id,
            is_final=is_final,
            confidence=confidence,
        )

    def listen_begin(self, press_id=None):
        return self._call(self._system.listen_begin, press_id)

    def listen_end(self, press_id):
        return self._call(self._system.listen_end, press_id)

    def listen_result(self, press_id):
        return self._call(self._system.listen_result, press_id)

    def status(self):
        return self._call(self._system.status)

    def task(self, task_id):
        return TaskHandle(self, self._system.task(task_id))

    def action(self, skill, **args):
        return Action(self, self._call(self._system.action, skill, **args))

    def snapshot(self, node=None):
        return self._call(self._system.snapshot, node)

    def allow_actions(self, node=None):
        return self._call(self._system.allow_actions, node)

    def set_robot_mode(self, mode, node=None):
        return self._call(self._system.set_robot_mode, mode, node)

    def replay(self, kind):
        return self._call(self._system.replay, kind)


def launch(
    *, backend="mock", duration=0.4, bindings=None, scene=None, port=0, site="local", env_id=None,
    live_model=False, tts_backend="mock", tts_python=None, tts_prepared_texts=(),
    asr_backend="mock", asr_python=None, asr_script=(), asr_vocabulary=(),
    ui_port=None, ui_viewer=None, ui_viewer_port=None,
):
    """Start configured local nodes; close owned nodes and router on exit."""
    return _session(
        System.launch(
            backend=backend,
            ui_port=ui_port,
            ui_viewer=ui_viewer,
            ui_viewer_port=ui_viewer_port,
            live_model=live_model,
            scene=scene,
            tts_backend=tts_backend,
            tts_python=tts_python,
            tts_prepared_texts=tts_prepared_texts,
            asr_backend=asr_backend,
            asr_python=asr_python,
            asr_script=asr_script,
            asr_vocabulary=asr_vocabulary,
            duration=duration,
            bindings=bindings,
            port=port,
            site=site,
            env_id=env_id,
        )
    )


def connect(
    endpoint="tcp/127.0.0.1:7447", *, site="local", env_id="arm01",
    skill_bindings=None, peers=None, bindings=None,
):
    """Discover running nodes with WRS_AGENT_TOKEN; exit only closes this connection."""
    return _session(System.connect(
        endpoint, site=site, env_id=env_id, skill_bindings=skill_bindings,
        peers=peers, bindings=bindings,
    ))


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
