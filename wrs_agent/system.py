"""Small local user API. Every call still crosses the existing Zenoh boundary."""

import asyncio
from contextlib import asynccontextmanager

from wrs_agent.bindings import load_nodes
from wrs_agent.nodes.api import ActionClient, RobotClient, action_request
from wrs_agent.processes import LocalStack
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
    """A submitted action: receipt is acceptance; wait/status report verified completion."""

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
        if self._cancel_request is None:
            state = await self.client.context(control=True)
            self._cancel_request = ControlRequest(
                interrupt_id=new_id(),
                boot_id=self.request.boot_id,
                control_epoch=state.control_epoch,
                action_id=self.id,
            )
        return await self.client.cancel(self._cancel_request)


class System:
    """Connect to an owned LocalStack; local() supervises and cleans up its processes."""

    def __init__(self, stack):
        self.stack = stack
        definitions, bindings = load_nodes(stack.bindings_path)
        buses = stack.node_transports
        self.agent = buses[stack.roles["agent"]]
        self.registry = NodeRegistry(buses, definitions, bindings)
        self.clients = {
            name: (RobotClient(bus) if definitions[name]["type"] == "wrs" else ActionClient(bus))
            for name, bus in stack.node_transports.items()
            if definitions[name]["actions"]
        }
        self.bindings = bindings

    @classmethod
    @asynccontextmanager
    async def local(cls, *, backend="mock", duration=0.4, bindings=None):
        async with LocalStack(
            backend=backend,
            duration=duration,
            voice=True,
            bindings=bindings,
        ) as stack:
            yield cls(stack)

    async def nodes(self):
        """Fresh node observations, queried directly, even if Agent is unavailable."""
        return await self.registry.refresh()

    async def start(self, *steps):
        """Submit a structured task to Agent and return immediately."""
        plan = Plan(steps=list(steps))
        return await self.agent.request(
            "request/task/start", {"request_id": new_id(), "plan": plan.model_dump()}
        )

    async def goal(self, text):
        """Ask the configured Planner; the default local profile uses Mock."""
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
        await self.nodes()
        client = self.clients[self.registry.provider(skill)]
        request = action_request(
            await client.context(),
            skill,
            args,
            task_id=new_id(),
            revision=0,
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
        """Robot-specific observation; not required of TTS or other providers."""
        node = node or self.stack.roles["wrs"]
        if not isinstance(self.clients[node], RobotClient):
            raise ValueError("robot_snapshot_unsupported")
        return await self.clients[node].snapshot()

    async def resume(self, node=None):
        """Explicit admission only. This never resumes an old action."""
        client = self.clients[node or self.stack.roles["wrs"]]
        if not isinstance(client, RobotClient):
            raise ValueError("robot_resume_unsupported")
        state = await client.snapshot(control=True)
        return await client.resume(
            ControlRequest(
                interrupt_id=new_id(),
                boot_id=state.boot_id,
                control_epoch=state.control_epoch,
                world_version=state.world_version,
            )
        )

    async def replay(self, kind):
        """Replay a verified intent through Voice, not ASR or a language classifier."""
        event = Interaction(event_id=new_id(), kind=kind)
        control = kind in {"stop", "barge_in"}
        return await self.stack.node_transports[self.stack.roles["voice"]].request(
            "request/voice/control" if control else "request/voice/event",
            event.model_dump(),
            control=control,
        )
