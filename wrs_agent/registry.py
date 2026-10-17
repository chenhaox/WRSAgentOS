"""Environment-scoped Zenoh discovery and receiver-queried node readiness."""

import asyncio
import re
import threading

import zenoh

from wrs_agent.errors import AgentError, error_info, from_exception
from wrs_agent.schemas import Empty, ErrorInfo, NodeInfo, new_id
from wrs_agent.skills import require_contract
from wrs_agent.transport import Transport

MAX_NODE_RECORDS = 64
MAX_PRESENCE = 1024
MAX_SKILL_OWNERS = MAX_NODE_RECORDS * 64
PRESENCE_REFRESH_SECONDS = 2.0


def _update_presence(presence, name, address, present):
    """Apply one bounded presence change; return whether information was lost."""
    if present:
        if name not in presence:
            if len(presence) >= MAX_PRESENCE:
                return True
            presence[name] = set()
        live = presence[name]
        if live is not None:
            live.add(address)
            if len(live) > 16:
                presence[name] = None
                return True
    elif name in presence:
        live = presence[name]
        if live is not None:
            live.discard(address)
            if not live:
                del presence[name]
    return False


def discovery_prefix(bus, env_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", env_id) or env_id in {".", ".."}:
        raise ValueError("invalid_namespace")
    return f"wrs/v4/{bus.site}/{env_id}/discovery/"


def register_node(bus, node_id, node_type, executor=None, *, features=(), env_id=None):
    boot_id = executor.boot_id if executor else new_id()
    bus.node_id = node_id
    if executor is not None:
        executor.node_id = node_id

    async def info(payload):
        Empty.model_validate(payload)
        cap = executor.features() if executor else None
        ready = executor.admission == "OPEN" if executor else True
        return NodeInfo(
            node_id=node_id,
            node_type=node_type,
            target=bus.env_id,
            actions=executor is not None,
            robot_controls=cap.robot_controls if cap else False,
            boot_id=boot_id,
            ready=ready,
            health="ready" if ready else executor.admission.lower(),
            skills=cap.skills if cap else {},
            skill_revision=cap.skill_revision if cap else 0,
            features=list(features),
            resources=cap.resources if cap else [],
            error=None
            if ready
            else error_info("node_not_ready", node_id=node_id, stage="discovery"),
        ).model_dump()

    bus.register_handler(f"request/node/{node_id}", info)
    # Discovery and services share the same session lifetime, even with distinct namespaces.
    prefix = discovery_prefix(bus, env_id or bus.env_id)
    token = bus.session.liveliness().declare_token(f"{prefix}{node_id}/{bus.env_id}/{boot_id}")
    bus.handles.append(token)
    return info


class NodeRegistry:
    def __init__(self, bus, *, env_id=None, bindings=None):
        self.bus, self.bindings = bus, dict(bindings or {})
        self.buses, self.clients = {}, {}
        self.entries, self.local, self._caps, self._known = {}, {}, {}, {}
        # Remember observed owners across disconnects: absence never selects a replacement.
        self._owners = {}  # skill -> original provider, or None once ambiguous
        self._transports = {bus.env_id: bus}
        self._lock = threading.Lock()
        self._live = {}
        self._overflow = self._closed = self._notified = False
        self._touched = set()
        self._initialized = False
        self._last_presence = float("-inf")
        self._queried = None
        self._query_overflow = False
        self._initial_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        self.env_id = env_id or bus.env_id
        self._prefix = discovery_prefix(bus, self.env_id)
        self._cancel_initial = None
        self._subscription = bus.session.liveliness().declare_subscriber(
            self._prefix + "*/*/*",
            zenoh.handlers.Callback(self._change, indirect=False),
            history=False,
        )

    def _notify(self):
        with self._lock:
            self._notified = False
        self._changed.set()

    def _change(self, sample, *, historical=False, query=None):
        key = str(sample.key_expr)
        if not key.startswith(self._prefix):
            return
        parts = key[len(self._prefix):].split("/")
        if len(parts) != 3 or any(
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", part) or part in {".", ".."}
            for part in parts
        ):
            return
        name, target, boot_id = parts
        identity = tuple(parts)
        with self._lock:
            if self._closed:
                return
            present = sample.kind == zenoh.SampleKind.PUT
            address = (target, boot_id)
            if historical:
                if self._queried is None or self._queried is not query or identity in self._touched:
                    return
                self._query_overflow |= _update_presence(self._queried, name, address, present)
                return
            self._overflow |= _update_presence(self._live, name, address, present)
            if self._queried is not None:
                if len(self._touched) >= MAX_PRESENCE and identity not in self._touched:
                    self._query_overflow = True
                else:
                    self._touched.add(identity)
                self._query_overflow |= _update_presence(self._queried, name, address, present)
            if not self._notified:
                self._notified = True
                self._loop.call_soon_threadsafe(self._notify)

    async def _initial_presence(self):
        """Initialize, then reconcile on refresh at a bounded frequency, off the control path."""
        async with self._initial_lock:
            if self._closed:
                raise RuntimeError("registry_closed")
            if (
                self._initialized and not self._overflow
                and self._loop.time() - self._last_presence < PRESENCE_REFRESH_SECONDS
            ):
                return
            with self._lock:
                # Keep the existing view usable while the snapshot is in flight. Live
                # changes update both views and take precedence over historical replies.
                queried = self._queried = {}
                self._touched.clear()
                self._query_overflow = False
            done = asyncio.Event()

            def reply(value):
                if value.ok is not None:
                    self._change(value.ok, historical=True, query=queried)

            def finished():
                self._loop.call_soon_threadsafe(done.set)

            cancel = zenoh.CancellationToken()
            self._cancel_initial = cancel
            try:
                self.bus.session.liveliness().get(
                    self._prefix + "*/*/*",
                    zenoh.handlers.Callback(reply, finished, indirect=False),
                    timeout=1.0,
                    cancellation_token=cancel,
                )
                await done.wait()
                with self._lock:
                    self._live = self._queried
                    self._queried = None
                    self._overflow = self._query_overflow
                    self._initialized = True
                    self._last_presence = self._loop.time()
                    self._touched.clear()
                self._changed.set()
            finally:
                cancel.cancel()
                with self._lock:
                    self._queried = None
                    self._touched.clear()
                self._cancel_initial = None

    def _presence(self):
        with self._lock:
            presence = {
                name: None if live is None else set(live) for name, live in self._live.items()
            }
        for name in tuple(self._caps):
            known = self._known.get(name)
            if known is None or presence.get(name) != {(known.target, known.boot_id)}:
                self._caps.pop(name, None)
        return presence

    def _endpoint_conflicts(self, presence):
        owners = {}
        for name, live in presence.items():
            if live is not None:
                for target, _ in live:
                    owners.setdefault(target, set()).add(name)
        conflicts = {}
        for name, info in self.entries.items():
            if (
                not info.actions or not self._valid(info)
                or presence.get(name) != {(info.target, info.boot_id)}
            ):
                continue
            for other in owners.get(info.target, set()) - {name}:
                candidate = self.entries.get(other)
                if (
                    candidate is None or not self._valid(candidate)
                    or presence.get(other) != {(candidate.target, candidate.boot_id)}
                ):
                    # Even a node refused by the description cache can conflict on this address.
                    conflicts.setdefault(name, "node_not_ready")
                elif candidate.actions:
                    conflicts[name] = conflicts[other] = "node_endpoint_ambiguous"
        return conflicts

    @staticmethod
    def _valid(info):
        return info.error is None or info.error.code == "node_not_ready"

    def check_instance(self, name, boot_id):
        presence = self._presence()
        live = presence.get(name, set())
        if self._overflow:
            raise AgentError("discovery_capacity_exceeded", node_id=name, stage="discovery")
        if live is None or len(live) > 1:
            raise AgentError("node_ambiguous", node_id=name, stage="discovery")
        if not live:
            raise AgentError("node_unavailable", node_id=name, stage="discovery")
        known = self._known.get(name)
        if (
            not boot_id or next(iter(live))[1] != boot_id
            or known is not None and known.boot_id == boot_id and live != {(known.target, boot_id)}
        ):
            raise AgentError("node_instance_changed", node_id=name, stage="discovery")
        conflicts = self._endpoint_conflicts(presence)
        if name in conflicts:
            raise AgentError(conflicts[name], node_id=name, stage="discovery")

    def _connection(self, target):
        if target not in self._transports:
            if len(self._transports) >= 128:
                raise AgentError("discovery_capacity_exceeded", stage="discovery")
            self._transports[target] = Transport(
                self.bus.endpoint, self.bus.site, target, self.bus.token, self.bus.source,
            )
        return self._transports[target]

    async def refresh(self, names=None):
        from wrs_agent.nodes.action_rpc import ActionClient

        if self._closed:
            raise RuntimeError("registry_closed")
        await self._initial_presence()
        async with self._refresh_lock:
            if self._closed:
                raise RuntimeError("registry_closed")
            presence = self._presence()
            selected = tuple(presence if names is None else names)
            for name in selected:
                if name in self.entries or name not in presence:
                    continue
                if len(self.entries) >= MAX_NODE_RECORDS:
                    departed = next((n for n in self.entries if n not in presence), None)
                    if departed is None:
                        continue
                    # Tasks own their frozen clients. Only directory records are reclaimed.
                    for records in (
                        self.entries, self._known, self._caps, self.buses, self.clients,
                    ):
                        records.pop(departed, None)
                self.entries[name] = NodeInfo(node_id=name, node_type="custom")

            async def query(name):
                live = presence.get(name)
                if self._overflow or live is None or len(live) != 1 or name not in self.entries:
                    return
                target, boot_id = next(iter(live))
                try:
                    bus = self._connection(target)
                    if name in self.local:
                        raw = await self.local[name]({})
                    else:
                        try:
                            raw = await bus.request(f"request/node/{name}", {}, timeout=0.3)
                        except TimeoutError:
                            known = self._known.get(name)
                            if known and (known.target, known.boot_id) == (target, boot_id):
                                raise
                            if self._presence().get(name) != {(target, boot_id)}:
                                raise AgentError(
                                    "node_instance_changed", node_id=name, stage="discovery",
                                ) from None
                            # A new session can need >0.6 s to learn the router's service
                            # routes. Only this first descriptor read gets a longer retry;
                            # known instances and all action/control deadlines stay unchanged.
                            raw = await bus.request(f"request/node/{name}", {}, timeout=2.0)
                    info = NodeInfo.model_validate(raw)
                    if (info.node_id, info.target, info.boot_id) != (name, target, boot_id):
                        raise AgentError("node_identity_mismatch", node_id=name, stage="discovery")
                    known = self._known.get(name)
                    if known and known.boot_id == boot_id and known.target != target:
                        raise AgentError("node_instance_changed", node_id=name, stage="discovery")
                    current = self._presence().get(name)
                    if current != {(target, boot_id)}:
                        raise AgentError("node_instance_changed", node_id=name, stage="discovery")
                    if self._valid(info):
                        if len(self._owners.keys() | info.skills.keys()) > MAX_SKILL_OWNERS:
                            raise AgentError("discovery_capacity_exceeded", stage="discovery")
                        self._known[name] = info
                        self.buses[name] = bus
                        if info.actions:
                            current_client = self.clients.get(name)
                            if current_client is None or current_client.transport is not bus:
                                self.clients[name] = ActionClient(bus, node_id=name)
                            for skill in info.skills:
                                owner = self._owners.setdefault(skill, name)
                                if owner != name:
                                    self._owners[skill] = None
                        else:
                            self.clients.pop(name, None)
                except (TimeoutError, ValueError, RuntimeError) as exc:
                    known = self._known.get(name)
                    info = NodeInfo(
                        node_id=name,
                        node_type=known.node_type if known else "custom",
                        target=target,
                        boot_id=boot_id,
                        health="unknown",
                        error=from_exception(
                            exc, validation_code="invalid_reply", node_id=name, stage="discovery",
                        ),
                    )
                self.entries[name] = info

            await asyncio.gather(*(query(name) for name in selected))
            return self.snapshot()

    def snapshot(self):
        presence = self._presence()
        conflicts = self._endpoint_conflicts(presence)
        result = {}
        for name in self.entries:
            live = presence.get(name, set())
            known = self._known.get(name)
            info = NodeInfo(
                node_id=name,
                node_type=known.node_type if known else "custom",
                target=known.target if known else None,
                actions=known.actions if known else False,
                robot_controls=known.robot_controls if known else False,
                error=error_info("node_unavailable", node_id=name, stage="discovery"),
            )
            if self._overflow or live is None or len(live) > 1:
                info = info.model_copy(update={
                    "health": "unknown",
                    "error": error_info(
                        "discovery_capacity_exceeded" if self._overflow else "node_ambiguous",
                        node_id=name, stage="discovery",
                    ),
                })
            elif live:
                target, boot_id = next(iter(live))
                cached = self.entries.get(name)
                info = (
                    cached
                    if cached and (cached.target, cached.boot_id) == (target, boot_id)
                    else info.model_copy(update={
                        "target": target, "boot_id": boot_id, "health": "unknown",
                        "error": error_info("node_not_ready", node_id=name, stage="discovery"),
                    })
                )
                if name in conflicts:
                    info = info.model_copy(update={
                        "ready": False, "health": "unknown",
                        "error": error_info(
                            conflicts[name], node_id=name, stage="discovery",
                        ),
                    })
            result[name] = info.model_dump()
        return result

    def _missing_record_error(self, name, presence):
        if name not in presence:
            return "node_unavailable"
        if len(self.entries) >= MAX_NODE_RECORDS and all(n in presence for n in self.entries):
            return "discovery_capacity_exceeded"
        return "node_not_ready"

    def transport(self, name):
        presence = self._presence()
        live = presence.get(name)
        boot_id = next(iter(live))[1] if live is not None and len(live) == 1 else None
        self.check_instance(name, boot_id)
        info = self.entries.get(name)
        if info is None:
            raise AgentError(
                self._missing_record_error(name, presence), node_id=name, stage="discovery",
            )
        if (info.target, info.boot_id) not in live:
            raise AgentError("node_unavailable", node_id=name, stage="discovery")
        if not self._valid(info):
            raise AgentError(info.error)
        return self.buses[name]

    def node_for_role(self, role):
        matches = [
            name for name, info in self.snapshot().items()
            if info["node_type"] == role and name in self.entries
            and (info["error"] is None or info["error"]["code"] == "node_not_ready")
            and self._valid(self.entries[name])
        ]
        if len(matches) != 1:
            raise AgentError(
                "node_role_ambiguous" if matches else "node_unavailable", stage="discovery",
            )
        self.transport(matches[0])
        return matches[0]

    async def wait_for(self, node_id=None, *, role=None, timeout=10):  # noqa: ASYNC109
        if node_id is None and role is None:
            raise ValueError("choose_node_id_or_role")
        async with asyncio.timeout(timeout):
            while True:
                self._changed.clear()
                await self.refresh()
                try:
                    name = node_id if node_id is not None else self.node_for_role(role)
                    self.transport(name)
                    if role is not None and self.entries[name].node_type != role:
                        raise AgentError("node_role_mismatch", node_id=name, stage="discovery")
                    return name
                except AgentError as exc:
                    if exc.error.code not in {
                        "node_unavailable", "node_not_ready", "request_timeout",
                    }:
                        raise
                # Arrivals wake immediately; retry unanswered descriptors without inventing absence.
                try:
                    async with asyncio.timeout(0.1):
                        await self._changed.wait()
                except TimeoutError:
                    pass

    async def close(self):
        # Finish active descriptor queries before closing their connections. Queued refreshes
        # recheck _closed after acquiring this same lock and cannot reopen a transport.
        async with self._refresh_lock:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
            if self._cancel_initial is not None:
                self._cancel_initial.cancel()
            self._subscription.undeclare()
            await asyncio.gather(*(
                transport.close()
                for transport in self._transports.values() if transport is not self.bus
            ))

    async def features(self, name, client, *, require_ready=True):
        for _ in range(2):
            info = self._ready(name) if require_ready else self.snapshot()[name]
            if not require_ready:
                self.check_instance(name, info["boot_id"])
                if info["error"] and info["error"]["code"] != "node_not_ready":
                    raise AgentError(ErrorInfo.model_validate(info["error"]))
            key = (info["boot_id"], info["skill_revision"])
            cached = self._caps.get(name)
            if cached is not None and cached[0] == key:
                return cached[1].model_copy(deep=True)
            cap = await client.features()
            self.check_instance(name, key[0])
            if cap.boot_id != key[0]:
                raise AgentError("node_instance_changed", node_id=name, stage="discovery")
            if cap.skill_revision != key[1]:
                await self.refresh([name])
                continue
            if cap.skills != {n: s.version for n, s in cap.specs.items()} or any(
                n != s.name for n, s in cap.specs.items()
            ):
                raise AgentError("invalid_reply", node_id=name, stage="discovery")
            self._caps[name] = (key, cap)
            return cap.model_copy(deep=True)
        raise AgentError("skill_catalog_changed", node_id=name, stage="discovery")

    def routes(self):
        routes = {skill: owner for skill, owner in self._owners.items() if owner is not None}
        routes.update(self.bindings)
        ambiguous = {skill for skill, owner in self._owners.items() if owner is None}
        return routes, ambiguous - self.bindings.keys()

    def _ready(self, name):
        entry = self.snapshot().get(name)
        if entry is None:
            raise AgentError(
                self._missing_record_error(name, self._presence()), node_id=name, stage="discovery",
            )
        if not entry["ready"]:
            error = entry.get("error")
            raise AgentError(
                ErrorInfo.model_validate(error)
                if error
                else error_info("node_not_ready", node_id=name, stage="discovery"),
            )
        return entry

    def node_for(self, skill, version=None):
        routes, ambiguous = self.routes()
        if skill in ambiguous:
            raise AgentError("skill_provider_ambiguous", stage="discovery")
        name = routes.get(skill)
        if name is None:
            raise AgentError("provider_not_found", stage="discovery")
        entry = self._ready(name)
        require_contract(skill, version, entry["skills"], node_id=name, stage="discovery")
        return name
