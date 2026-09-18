"""Explicit bindings, Zenoh presence, and receiver-queried readiness."""

import asyncio
import re
import threading

import zenoh

from wrs_agent.schemas import Empty, NodeInfo, new_id
from wrs_agent.skills import SKILLS


def register_node(bus, node_id, node_type, executor=None):
    boot_id = executor.boot_id if executor else new_id()
    if executor is not None:
        executor.node_id = node_id

    async def info(payload):
        Empty.model_validate(payload)
        cap = executor.capabilities() if executor else None
        ready = executor.admission == "OPEN" if executor else True
        return NodeInfo(
            node_id=node_id,
            node_type=node_type,
            boot_id=boot_id,
            ready=ready,
            health="ready" if ready else executor.admission.lower(),
            skills=cap.skills if cap else [],
            capabilities=sorted(
                {c for entry in executor.skills.values() for c in entry.spec.required_capabilities}
            )
            if cap
            else ["task.coordinate" if node_type == "agent" else "interaction.replay"],
            resources=cap.resources if cap else [],
        ).model_dump()

    bus.register_handler(f"request/node/{node_id}", info)
    # This token is owned by the same session as the services, not a separate heartbeat.
    token = bus.session.liveliness().declare_token(bus.key(f"presence/{node_id}/{boot_id}"))
    bus.handles.append(token)
    return info


class NodeRegistry:
    def __init__(self, buses, definitions, bindings):
        self.buses, self.definitions, self.bindings = buses, definitions, bindings
        self.entries, self.local, self._caps = {}, {}, {}
        self._lock = threading.Lock()
        self._live = {name: set() for name in definitions}
        self._dirty = set()
        self._arrived = {name: asyncio.Event() for name in definitions}
        self._seen, self._initialized = set(), set()
        loop = asyncio.get_running_loop()
        for name, definition in definitions.items():
            if not definition["enabled"] or name not in buses:
                continue
            bus = buses[name]
            prefix = bus.key(f"presence/{name}") + "/"

            def changed(sample, name=name, prefix=prefix):
                key = str(sample.key_expr)
                boot = key.removeprefix(prefix)
                if not key.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", boot):
                    return
                present = sample.kind == zenoh.SampleKind.PUT
                with self._lock:
                    live = self._live[name]
                    # Bound malformed deployments too. Overflow requires a new view;
                    # never choose an arbitrary instance.
                    if live is not None:
                        live.add(boot) if present else live.discard(boot)
                        if len(live) > 16:
                            self._live[name] = None
                    if name not in self._seen:
                        self._seen.add(name)
                        loop.call_soon_threadsafe(self._arrived[name].set)
                    self._dirty.add(name)

            handle = bus.session.liveliness().declare_subscriber(
                prefix + "*", zenoh.handlers.Callback(changed, indirect=False), history=True
            )
            bus.handles.append(handle)

    def _presence(self):
        with self._lock:
            for name in self._dirty:
                self.entries.pop(name, None)
                self._caps.pop(name, None)
            self._dirty.clear()
            return {name: None if live is None else set(live) for name, live in self._live.items()}

    def check_instance(self, name, boot_id):
        if not boot_id or self._presence().get(name) != {boot_id}:
            raise ValueError("node_instance_changed_or_ambiguous")

    async def refresh(self, names=None):
        async def query(name):
            definition = self.definitions[name]
            if not definition["enabled"] or name not in self.buses:
                return
            if name not in self._initialized:
                # history=True asks Zenoh for existing tokens. Bound only the initial wait;
                # later absence/presence is driven by native events, never a local expiry.
                try:
                    async with asyncio.timeout(0.3):
                        await self._arrived[name].wait()
                except TimeoutError:
                    pass
                self._initialized.add(name)
            live = self._presence()[name]
            if live is None or len(live) != 1:
                return
            boot_id = next(iter(live))
            try:
                raw = (
                    await self.local[name]({})
                    if name in self.local
                    else await self.buses[name].request(f"request/node/{name}", {}, timeout=0.3)
                )
                info = NodeInfo.model_validate(raw)
                if (info.node_id, info.node_type, info.boot_id) != (
                    name,
                    definition["type"],
                    boot_id,
                ):
                    raise ValueError("node_identity_mismatch")
                self.check_instance(name, boot_id)
            except (TimeoutError, ValueError, RuntimeError):
                info = NodeInfo(
                    node_id=name, node_type=definition["type"], boot_id=boot_id, health="unknown"
                )
            self.entries[name] = info

        await asyncio.gather(*(query(n) for n in (self.definitions if names is None else names)))
        return self.snapshot()

    def snapshot(self):
        presence = self._presence()
        result = {}
        for name, definition in self.definitions.items():
            live = presence[name]
            info = NodeInfo(node_id=name, node_type=definition["type"])
            if not definition["enabled"]:
                info = info.model_copy(update={"health": "unsupported"})
            elif live is None or len(live) > 1:
                info = info.model_copy(update={"health": "unknown"})
            elif live:
                boot_id = next(iter(live))
                cached = self.entries.get(name)
                info = (
                    cached
                    if cached and cached.boot_id == boot_id
                    else info.model_copy(update={"boot_id": boot_id, "health": "unknown"})
                )
            result[name] = info.model_dump()
        return result

    async def capabilities(self, name, client):
        info = self.snapshot()[name]
        if not info["ready"]:
            raise ValueError("node_not_ready")
        boot_id = info["boot_id"]
        cached = self._caps.get(name)
        if cached is None or cached[0] != boot_id:
            cap = await client.capabilities()
            self.check_instance(name, boot_id)
            self._caps[name] = (boot_id, cap)
        return self._caps[name][1]

    def node_for(self, skill):
        name = self.bindings.get(skill)
        entry = self.snapshot().get(name)
        if not entry or not entry["ready"]:
            raise ValueError("node_not_ready")
        if skill not in entry["skills"] or not set(
            SKILLS[skill].spec.required_capabilities
        ).issubset(entry["capabilities"]):
            raise ValueError("unsupported_skill_on_current_node")
        return name
