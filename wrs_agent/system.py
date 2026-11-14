"""Small local user API. Every call still crosses the existing Zenoh boundary."""

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager

from wrs_agent.bindings import load_bindings
from wrs_agent.errors import AgentError
from wrs_agent.handles import GoalHandle, TaskHandle
from wrs_agent.policy import text_intent
from wrs_agent.registry import NodeRegistry
from wrs_agent.schemas import (
    AsrPress,
    AsrResult,
    ControlRequest,
    Interaction,
    ModeRequest,
    Plan,
    Step,
    TextInput,
    TextReceipt,
    new_id,
)
from wrs_agent.skills import lookup_skills, skill_specs
from wrs_agent.transport import Transport


def step(skill, *, after=(), inputs=None, **args):
    """Steps without dependencies may run concurrently on different resources."""
    dependencies = [after] if isinstance(after, Step) else list(after)
    return Step(
        step_id=new_id(),
        skill=skill,
        version=None,  # Resolved from the provider before creating an execution.
        args=args,
        depends_on=[item.step_id for item in dependencies],
        inputs=inputs or {},
    )


class System:
    """A live node directory; launch() additionally owns local processes."""

    def __init__(self, bus, *, skill_bindings=None, peers=None, registry=None):
        """A Node passes its own discover() directory; its caller owns closing it."""
        self.bindings = dict(skill_bindings or {})
        self.roles = dict(peers or {})
        self.registry = registry or NodeRegistry(bus, bindings=self.bindings)
        self.endpoint, self.site, self.env_id = bus.endpoint, bus.site, self.registry.env_id
        self._transports, self.clients = self.registry.buses, self.registry.clients
        self._bound_buses = {}  # Selected control peers outlive evicted directory descriptions.
        self._local_stack = None  # Only for diagnostics, never needed to discover nodes.

    def _role(self, role):
        # Bind once: a disappearing peer must not silently select another device.
        if role not in self.roles:
            self.roles[role] = self.registry.node_for_role(role)
        name = self.roles[role]
        info = self.registry.entries.get(name)
        if (
            info is not None and self.registry._valid(info) and info.node_type != role
        ):
            raise AgentError("node_role_mismatch", node_id=name, stage="discovery")
        return name

    async def _resolve(self, role):
        try:
            bus = self.registry.transport(self._role(role))
            self._bound_buses[role] = bus
            return bus
        except AgentError as exc:
            if exc.code not in {
                "node_unavailable", "node_not_ready", "node_instance_changed", "request_timeout",
            }:
                raise
        # Ordinary first use/reconnect can wait for discovery; stop paths never call this.
        try:
            name = await self.registry.wait_for(self.roles.get(role), role=role, timeout=2)
        except TimeoutError:
            raise AgentError(
                "node_unavailable", node_id=self.roles.get(role), stage="discovery",
            ) from None
        self.roles[role] = name
        bus = self.registry.transport(name)
        self._bound_buses[role] = bus
        return bus

    def _bound(self, role):
        name = self._role(role)
        if name in self._transports:
            self._bound_buses[role] = self._transports[name]
        if role not in self._bound_buses:
            raise AgentError("node_unavailable", node_id=name, stage="control")
        return self._bound_buses[role]

    async def _action_client(self, node):
        if node is None:
            await self._resolve("wrs")
            node = self._role("wrs")
        else:
            await self.registry.refresh([node])
        self.registry.transport(node)  # Includes capacity rejection for unadmitted nodes.
        if node not in self.clients:
            raise ValueError("node_snapshot_unsupported")
        return node, self.clients[node]

    @property
    def agent(self):
        # Task cancellation uses the bound channel even if an ordinary descriptor timed out.
        return self._bound("agent")

    @classmethod
    @asynccontextmanager
    async def connect(
        cls,
        endpoint="tcp/127.0.0.1:7447",
        *,
        site="local",
        env_id="arm01",
        skill_bindings=None,
        peers=None,
        bindings=None,
        _token=None,
    ):
        """Discover running nodes; closing this connection never stops their processes.

        A supplied deployment file is only a shortcut for explicit peer/skill choices.
        Without it, no deployment file is read and new nodes can join at any time.
        """
        selected, routes = {}, {}
        if bindings is not None:
            definitions, routes = load_bindings(bindings)
            for name, definition in definitions.items():
                if definition["enabled"] and definition["type"] != "custom":
                    role = definition["type"]
                    if role in selected:
                        raise ValueError("node_role_ambiguous: " + role)
                    selected[role] = name
        selected.update(peers or {})
        routes.update(skill_bindings or {})
        token = os.environ.get("WRS_AGENT_TOKEN", "") if _token is None else _token
        if not 16 <= len(token) <= 128:
            raise ValueError(
                "Set WRS_AGENT_TOKEN in this terminal or IDE run configuration to the same "
                "session credential used by the running nodes (16 to 128 characters). "
                "See examples/README.md for connect/nodes setup; .env is not loaded automatically."
            )
        async with AsyncExitStack() as cleanup:
            bus = Transport(endpoint, site, env_id, token, "input")
            cleanup.push_async_callback(bus.close)
            system = cls(bus, skill_bindings=routes, peers=selected)
            cleanup.push_async_callback(system.registry.close)
            await system.nodes()
            yield system

    @classmethod
    @asynccontextmanager
    async def launch(
        cls, *, backend="mock", duration=0.4, bindings=None, scene=None,
        port=0, site="local", env_id=None, live_model=False,
        tts_backend="mock", tts_python=None, tts_prepared_texts=(),
        asr_backend="mock", asr_python=None, asr_script=(), asr_vocabulary=(),
        ui_port=None, ui_viewer=None, ui_viewer_port=None,
    ):
        from wrs_agent.processes import LocalStack

        async with LocalStack(
            backend=backend,
            live_model=live_model,
            scene=scene,
            tts_backend=tts_backend,
            tts_python=tts_python,
            tts_prepared_texts=tts_prepared_texts,
            asr_backend=asr_backend,
            asr_python=asr_python,
            asr_script=asr_script,
            asr_vocabulary=asr_vocabulary,
            ui_port=ui_port,
            ui_viewer=ui_viewer,
            ui_viewer_port=ui_viewer_port,
            duration=duration,
            bindings=bindings,
            port=port,
            site=site,
            env_id=env_id,
        ) as stack:
            yield stack.system

    async def nodes(self):
        """Fresh node observations, queried directly, even if Agent is unavailable."""
        rows = await self.registry.refresh()
        for role in {row["node_type"] for row in rows.values()} - {"custom"}:
            if role not in self.roles:
                try:
                    self.roles[role] = self.registry.node_for_role(role)
                except AgentError:
                    pass  # Ambiguous/unknown roles require an explicit selection.
        for role, name in self.roles.items():
            if name in self._transports:
                self._bound_buses[role] = self._transports[name]
        return rows

    async def skills(self):
        """Skills available on ready nodes; this does not invoke Planner."""
        online = await self.nodes()
        names = [name for name in self.clients if online.get(name, {}).get("ready")]
        caps = await asyncio.gather(
            *(self.registry.features(name, self.clients[name]) for name in names)
        )
        routes, _ = self.registry.routes()
        return lookup_skills(dict(zip(names, caps, strict=True)), routes)

    def task(self, task_id):
        """Reconnect to a task identity; status() reports task_not_found for unknown IDs."""
        return TaskHandle(self, task_id)

    async def start(self, *steps):
        """Accept an explicit plan and return its immutable execution identity."""
        agent = await self._resolve("agent")
        result = await agent.request(
            "request/task/start",
            {"request_id": new_id(), "plan": Plan(steps=list(steps)).model_dump()},
        )
        return self.task(result["task_id"])

    def planning(self, request_id):
        """Attach to a planning request, including one accepted through Voice."""
        return GoalHandle(self, request_id)

    async def send_text(self, text, *, input_id=None, is_final=True, confidence=1.0):
        """Submit recognized text. ASR/UI adapters own capture and call this off their callback."""
        event = TextInput(
            input_id=input_id if input_id is not None else new_id(),
            text=text,
            is_final=is_final,
            confidence=confidence,
        )
        control = text_intent(event)[0] in {"stop", "cancel_tts"}
        bus = (
            self._bound("voice") if control else await self._resolve("voice")
        )
        raw = await bus.request(
            "request/voice/control_text" if control else "request/voice/text",
            event.model_dump(),
            control=control,
            timeout=5.0,
        )
        return TextReceipt.model_validate(raw)

    async def listen_begin(self, press_id=None):
        """Start one push-to-talk capture. The UI owns press and release, never a timer."""
        press = AsrPress() if press_id is None else AsrPress(press_id=press_id)
        bus = await self._resolve("asr")
        raw = await bus.request(
            "request/asr/begin", press.model_dump(), control=True
        )
        return AsrResult.model_validate(raw)

    async def listen_end(self, press_id):
        """Release the button. Recognition and routing to Voice continue on the ASR node."""
        raw = await self._bound("asr").request(
            "request/asr/end", AsrPress(press_id=press_id).model_dump(), control=True
        )
        return AsrResult.model_validate(raw)

    async def listen_result(self, press_id):
        """Poll one capture; the transcript goes only to the caller that held the button."""
        raw = await self._bound("asr").request(
            "request/asr/result", AsrPress(press_id=press_id).model_dump()
        )
        return AsrResult.model_validate(raw)

    async def goal(self, text):
        request_id = new_id()
        agent = await self._resolve("agent")
        await agent.request("request/task/goal", {"request_id": request_id, "goal": text})
        return GoalHandle(self, request_id)

    async def status(self):
        """Runtime overview; use task(id).status() to query a particular execution."""
        agent = await self._resolve("agent")
        return await agent.request("request/task/status", {})

    async def action(self, skill, **args):
        """Resolve a discovered provider's advertised contract, then submit once."""
        await self.registry.refresh()
        name = self.registry.node_for(skill)
        client = self.clients[name]
        cap = await self.registry.features(name, client)
        spec = skill_specs({name: cap}, {skill: name}).get(skill)
        if spec is None:
            raise AgentError("skill_not_on_node", node_id=name, stage="discovery")
        context = await client.context()
        self.registry.check_instance(name, context.boot_id)
        if context.boot_id != cap.boot_id:
            raise AgentError("node_instance_changed", node_id=name, stage="preflight")
        return await client.submit(
            skill, args, context=context, task_id=new_id(), version=spec.version
        )

    async def snapshot(self, node=None):
        """Read one action node directly; defaults to the robot, never aggregates nodes."""
        _, client = await self._action_client(node)
        return await client.snapshot()

    async def allow_actions(self, node=None):
        """Allow new robot actions after confirmed stop; never continue an old action."""
        node, client = await self._action_client(node)
        if not self.registry.snapshot()[node]["robot_controls"]:
            raise ValueError("robot_allow_actions_unsupported")
        state = await client.snapshot(control=True)
        self.registry.check_instance(node, state.boot_id)
        return await client.control(
            "allow_actions",
            ControlRequest(
                interrupt_id=new_id(),
                boot_id=state.boot_id,
                control_epoch=state.control_epoch,
                state_version=state.state_version,
            ),
        )

    async def set_robot_mode(self, mode, node=None):
        """Drive the model only ("virtual") or the real robot ("real"); leaves actions held."""
        node, client = await self._action_client(node)
        if not self.registry.snapshot()[node]["robot_controls"]:
            raise ValueError("robot_mode_unsupported")
        state = await client.snapshot(control=True)
        self.registry.check_instance(node, state.boot_id)
        return await client.set_mode(
            ModeRequest(
                interrupt_id=new_id(),
                boot_id=state.boot_id,
                control_epoch=state.control_epoch,
                mode=mode,
            )
        )

    async def replay(self, kind):
        """Replay a verified intent through Voice, not ASR or a language classifier."""
        event = Interaction(event_id=new_id(), kind=kind)
        control = kind in {"stop", "barge_in"}
        bus = (
            self._bound("voice") if control else await self._resolve("voice")
        )
        return await bus.request(
            "request/voice/control" if control else "request/voice/event",
            event.model_dump(),
            control=control,
        )
