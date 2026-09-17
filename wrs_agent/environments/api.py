"""Replace this boundary to add a real device environment."""

from typing import Protocol

from wrs_agent.schemas import (
    ActionReceipt,
    ActionRequest,
    ActionStatus,
    CapabilitySnapshot,
    ControlReceipt,
    ControlRequest,
    WorldSnapshot,
)


class Environment(Protocol):
    async def capabilities(self) -> CapabilitySnapshot: ...
    async def snapshot(self, *, control: bool = False) -> WorldSnapshot: ...
    async def submit(self, request: ActionRequest) -> ActionReceipt: ...
    async def status(self, action_id: str) -> ActionStatus | None: ...
    async def hold(self, request: ControlRequest) -> ControlReceipt: ...
    async def cancel(self, request: ControlRequest) -> ControlReceipt: ...
    async def resume(self, request: ControlRequest) -> ControlReceipt: ...


class ActionClient:
    def __init__(self, transport):
        self.transport = transport

    async def capabilities(self):
        return CapabilitySnapshot.model_validate(
            await self.transport.request("request/capabilities", {})
        )

    async def snapshot(self, *, control=False):
        suffix = "request/control/snapshot" if control else "request/snapshot"
        return WorldSnapshot.model_validate(
            await self.transport.request(suffix, {}, control=control)
        )

    async def submit(self, request):
        return ActionReceipt.model_validate(
            await self.transport.request("request/action/submit", request.model_dump())
        )

    async def status(self, action_id):
        result = await self.transport.request("request/action/status", {"action_id": action_id})
        return ActionStatus.model_validate(result) if result else None

    async def _control(self, kind, request):
        return ControlReceipt.model_validate(
            await self.transport.request(
                f"request/control/{kind}", request.model_dump(), control=True
            )
        )

    async def hold(self, request):
        return await self._control("hold", request)

    async def cancel(self, request):
        return await self._control("cancel", request)

    async def resume(self, request):
        return await self._control("resume", request)
