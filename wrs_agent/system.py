"""Small local user API. Every call still crosses the existing Zenoh boundary."""

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager

from wrs_agent.bindings import load_bindings
from wrs_agent.nodes.actions import ActionClient, action_request
from wrs_agent.registry import NodeRegistry
from wrs_agent.schemas import (
    TERMINAL,
    ActionReceipt,
    ControlRequest,
    Interaction,
    Plan,
    Step,
    new_id,
)
from wrs_agent.skills import lookup_skills
from wrs_agent.transport import Transport


def step(skill, *, after=(), **args):
    """Steps without dependencies may run concurrently on different resources."""
    dependencies = [after] if isinstance(after, Step) else list(after)
    return Step(
        step_id=new_id(),
        skill=skill,
        args=args,
        depends_on=[item.step_id for item in dependencies],
    )


class Action:
    """A submitted action. wait() returns terminal status; inspect state/verification."""

    def __init__(self, client, request, receipt):
        self.client, self.request, self.receipt = client, request, receipt
        self.id = request.action_id
        self._cancel_request = None

    async def status(self):
        return await self.client.status(self.id)

    async def wait(self, *, timeout=10):  # noqa: ASYNC109 - bounded public wait
        async with asyncio.timeout(timeout):
            while True:
                state = await self.status()
                if state is None:
                    raise RuntimeError("UNKNOWN: action_status_missing")
                if state.state in TERMINAL:
                    return state
                await asyncio.sleep(0.02)

    async def cancel(self):
        """Request cancellation; receipt.phase distinguishes STOPPING from STOPPED."""
        if self._cancel_request is None:
            state = await self.client.snapshot(control=True)
            self._cancel_request = ControlRequest(
                interrupt_id=new_id(),
                boot_id=self.request.boot_id,
                control_epoch=state.control_epoch,
                action_id=self.id,
            )
        return await self.client.cancel(self._cancel_request)


class System:
    """A connection to configured nodes; local() additionally owns local processes."""

    def __init__(self, transports, definitions, bindings, *, endpoint, site, env_id):
        self.endpoint, self.site, self.env_id = endpoint, site, env_id
        self.definitions = definitions
        self.bindings = bindings
        self._transports = transports
        self._local_stack = None  # Only for local process diagnostics, never needed to connect.
        self.roles = {}
        for name, definition in definitions.items():
            if definition["enabled"]:
                role = definition["type"]
                if role in self.roles:
                    raise ValueError("one_node_per_role_in_v1")
                self.roles[role] = name
        self.registry = NodeRegistry(transports, definitions, bindings)
        self.clients = {
            name: ActionClient(bus)
            for name, bus in transports.items()
            if definitions[name]["actions"]
        }

    def _role(self, role):
        if role not in self.roles:
            raise ValueError(f"node_role_not_configured: {role}")
        return self.roles[role]

    @property
    def agent(self):
        return self._transports[self._role("agent")]

    @classmethod
    @asynccontextmanager
    async def connect(
        cls,
        endpoint="tcp/127.0.0.1:7447",
        *,
        site="local",
        env_id="arm01",
        bindings=None,
        _token=None,
        _config=None,
    ):
        """Connect without starting/stopping nodes; credentials come from WRS_AGENT_TOKEN.

        _token/_config are the launcher's already-resolved session, not a second user config.
        Online/readiness checks happen when nodes/skills/actions are queried.
        """
        definitions, skill_bindings = _config if _config is not None else load_bindings(bindings)
        definitions = {name: dict(node) for name, node in definitions.items()}
        skill_bindings = dict(skill_bindings)
        token = os.environ.get("WRS_AGENT_TOKEN", "") if _token is None else _token
        if not 16 <= len(token) <= 128:
            raise ValueError("Set WRS_AGENT_TOKEN to a session credential of 16 to 128 characters")
        async with AsyncExitStack() as cleanup:
            transports, by_suffix = {}, {}
            for name, definition in definitions.items():
                if not definition["enabled"]:
                    continue
                suffix = definition["suffix"]
                if suffix not in by_suffix:
                    bus = Transport(endpoint, site, env_id + suffix, token, "input")
                    cleanup.push_async_callback(bus.close)
                    by_suffix[suffix] = bus
                transports[name] = by_suffix[suffix]
            yield cls(
                transports, definitions, skill_bindings, endpoint=endpoint, site=site, env_id=env_id
            )

    @classmethod
    @asynccontextmanager
    async def local(
        cls, *, backend="mock", duration=0.4, bindings=None, port=0, site="local", env_id=None
    ):
        from wrs_agent.processes import LocalStack

        async with LocalStack(
            backend=backend,
            duration=duration,
            bindings=bindings,
            port=port,
            site=site,
            env_id=env_id,
        ) as stack:
            yield stack.system

    async def nodes(self):
        """Fresh node observations, queried directly, even if Agent is unavailable."""
        return await self.registry.refresh()

    async def skills(self, query=""):
        """Find skills available on ready nodes; this does not invoke Planner."""
        online = await self.nodes()
        names = [name for name in self.clients if online.get(name, {}).get("ready")]
        caps = await asyncio.gather(
            *(self.registry.capabilities(name, self.clients[name]) for name in names)
        )
        return lookup_skills(query, dict(zip(names, caps, strict=True)), self.bindings)

    async def start(self, *steps):
        """Return the accepted task snapshot; physical execution can start later."""
        plan = Plan(steps=list(steps))
        return await self.agent.request(
            "request/task/start", {"request_id": new_id(), "plan": plan.model_dump()}
        )

    async def goal(self, text):
        """Return a planning request ID; status exposes a task ID after plan validation."""
        return await self.agent.request("request/task/goal", {"request_id": new_id(), "goal": text})

    async def status(self):
        return await self.agent.request("request/task/status", {})

    async def watch(self, *, timeout=10):  # noqa: ASYNC109 - bounded observation
        """Read task progress without involving Planner or stopping any action."""
        deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
        while True:
            # Never leave a task-bound timeout active while yielding to caller code.
            async with asyncio.timeout_at(deadline):
                state = await self.status()
            yield state
            if state["planning"] != "WAITING" and state["state"] in TERMINAL:
                return
            if state["planning"] in {"ANSWER", "CLARIFY", "FAILED", "STALE"}:
                return
            async with asyncio.timeout_at(deadline):
                await asyncio.sleep(0.02)

    async def wait(self, *, timeout=10):  # noqa: ASYNC109 - bounded public wait
        async for state in self.watch(timeout=timeout):
            result = state
        return result

    async def action(self, skill, **args):
        """Explicit direct action; choose provider from configuration, never from model text."""
        if skill not in self.bindings:
            raise ValueError("unknown_skill")
        name = self.bindings[skill]
        await self.registry.refresh([name])
        client = self.clients[self.registry.node_for(skill)]
        context = await client.context()
        self.registry.check_instance(name, context.boot_id)
        request = action_request(
            context,
            skill,
            args,
            task_id=new_id(),
        )
        try:
            receipt = await client.submit(request)
        except TimeoutError:
            status = await client.status(request.action_id)
            if status is None:
                raise RuntimeError(
                    f"UNKNOWN: submit_unconfirmed action_id={request.action_id}"
                ) from None
            receipt = ActionReceipt(accepted=True, status=status)
        if not receipt.accepted:
            raise ValueError(receipt.reason)
        return Action(client, request, receipt)

    async def snapshot(self, node=None):
        """Read one action node directly; defaults to the robot, never aggregates nodes."""
        node = node or self._role("wrs")
        if node not in self.clients:
            raise ValueError("node_snapshot_unsupported")
        return await self.clients[node].snapshot()

    async def resume(self, node=None):
        """Explicit admission only. This never resumes an old action."""
        node = node or self._role("wrs")
        if self.definitions.get(node, {}).get("type") != "wrs" or node not in self.clients:
            raise ValueError("robot_resume_unsupported")
        client = self.clients[node]
        state = await client.snapshot(control=True)
        return await client.control(
            "resume",
            ControlRequest(
                interrupt_id=new_id(),
                boot_id=state.boot_id,
                control_epoch=state.control_epoch,
                world_version=state.world_version,
            ),
        )

    async def replay(self, kind):
        """Replay a verified intent through Voice, not ASR or a language classifier."""
        event = Interaction(event_id=new_id(), kind=kind)
        control = kind in {"stop", "barge_in"}
        return await self._transports[self._role("voice")].request(
            "request/voice/control" if control else "request/voice/event",
            event.model_dump(),
            control=control,
        )
