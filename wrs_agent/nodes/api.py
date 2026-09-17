"""Action protocol for any provider; robot controls are a separate extension."""

from typing import Protocol

from wrs_agent.schemas import (
    ActionContext,
    ActionReceipt,
    ActionRequest,
    ActionStatus,
    CapabilitySnapshot,
    ControlReceipt,
    ControlRequest,
    WorldSnapshot,
    new_id,
)


def action_request(context, skill, args, *, task_id, revision, version=1):
    """Bind a proposal to freshly observed receiver-issued authorization."""
    return ActionRequest(
        action_id=new_id(),
        task_id=task_id,
        task_revision=revision,
        boot_id=context.boot_id,
        control_epoch=context.control_epoch,
        lease_id=context.lease_id,
        world_version=context.world_version,
        skill=skill,
        version=version,
        args=args,
    )


class ActionProvider(Protocol):
    async def capabilities(self) -> CapabilitySnapshot: ...
    async def context(self, *, control: bool = False) -> ActionContext: ...
    async def submit(self, request: ActionRequest) -> ActionReceipt: ...
    async def status(self, action_id: str) -> ActionStatus | None: ...
    async def cancel(self, request: ControlRequest) -> ControlReceipt: ...


class ActionClient:
    def __init__(self, transport):
        self.transport = transport

    async def capabilities(self):
        return CapabilitySnapshot.model_validate(
            await self.transport.request("request/capabilities", {})
        )

    async def context(self, *, control=False):
        return ActionContext.model_validate(
            await self.transport.request("request/action/context", {}, control=control)
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

    async def cancel(self, request):
        return await self._control("cancel", request)


class RobotClient(ActionClient):
    """Robot-only world state and controlled hold/resume admission."""

    async def snapshot(self, *, control=False):
        suffix = "request/control/snapshot" if control else "request/snapshot"
        return WorldSnapshot.model_validate(
            await self.transport.request(suffix, {}, control=control)
        )

    async def context(self, *, control=False):
        return await self.snapshot(control=control)

    async def hold(self, request):
        return await self._control("hold", request)

    async def resume(self, request):
        return await self._control("resume", request)
