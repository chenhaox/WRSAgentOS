"""An explicit endpoint table plus bounded, fresh Zenoh node queries. No auto routing."""

import asyncio
import time

from wrs_agent.schemas import Empty, NodeInfo, new_id
from wrs_agent.skills import SPECS


def register_node(bus, node_id, node_type, provider=None):
    boot_id = provider.boot_id if provider else new_id()

    async def info(payload):
        Empty.model_validate(payload)
        cap = provider.capabilities() if provider else None
        ready = provider.admission == "OPEN" if provider else True
        return NodeInfo(
            node_id=node_id,
            node_type=node_type,
            boot_id=boot_id,
            ready=ready,
            health="ready" if ready else provider.admission.lower(),
            skills=cap.skills if cap else [],
            capabilities=sorted({c for s in cap.skills for c in SPECS[s].required_capabilities})
            if cap
            else ["task.coordinate" if node_type == "agent" else "interaction.replay"],
            resources=cap.resources if cap else [],
        ).model_dump()

    bus.register_handler(f"request/node/{node_id}", info)
    return info


class NodeRegistry:
    def __init__(self, buses, definitions, bindings):
        self.buses, self.definitions, self.bindings = buses, definitions, bindings
        self.entries = {}
        self.local = {}
        self.checked = {}

    async def refresh(self):
        async def query(name, definition):
            info = NodeInfo(node_id=name, node_type=definition["type"])
            if not definition["enabled"]:
                info = info.model_copy(update={"health": "unsupported"})
            elif name in self.local:
                info = NodeInfo.model_validate(await self.local[name]({}))
            elif name in self.buses:
                try:
                    info = NodeInfo.model_validate(
                        await self.buses[name].request(f"request/node/{name}", {}, timeout=0.3)
                    )
                    if (
                        info.node_id != name
                        or info.node_type != definition["type"]
                        or not info.boot_id
                    ):
                        raise ValueError("node_identity_mismatch")
                except (TimeoutError, ValueError, RuntimeError):
                    info = NodeInfo(node_id=name, node_type=definition["type"])
            self.entries[name] = info
            self.checked[name] = time.monotonic()

        await asyncio.gather(*(query(n, d) for n, d in self.definitions.items()))
        return self.snapshot()

    def snapshot(self):
        result = {}
        for name, entry in self.entries.items():
            if entry.boot_id and time.monotonic() - self.checked[name] > 2:
                entry = entry.model_copy(update={"ready": False, "health": "stale"})
            result[name] = entry.model_dump()
        return result

    def provider(self, skill):
        name = self.bindings.get(skill)
        entry = self.snapshot().get(name)
        if not entry or not entry["ready"]:
            raise ValueError("provider_not_ready")
        if skill not in entry["skills"] or not set(SPECS[skill].required_capabilities).issubset(
            entry["capabilities"]
        ):
            raise ValueError("unsupported_skill_on_current_node")
        return name
