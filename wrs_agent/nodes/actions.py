"""One action client and service binding; each node exposes only its supported controls."""

from typing import Protocol

from wrs_agent.schemas import (
    ActionContext,
    ActionReceipt,
    ActionRequest,
    ActionStatus,
    CapabilitySnapshot,
    ControlReceipt,
    ControlRequest,
    Empty,
    IdRequest,
    NodeSnapshot,
    new_id,
)


def action_request(context, skill, args, *, task_id, revision=0, version=1):
    """Bind fresh receiver authorization; revision is only for legacy wire callers."""
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
    async def snapshot(self, *, control: bool = False) -> NodeSnapshot: ...
    async def context(self, *, control: bool = False) -> ActionContext: ...
    async def submit(self, request: ActionRequest) -> ActionReceipt: ...
    async def status(self, action_id: str) -> ActionStatus | None: ...
    async def cancel(self, request: ControlRequest) -> ControlReceipt: ...
    async def control(self, kind: str, request: ControlRequest) -> ControlReceipt: ...


class ActionClient:
    def __init__(self, transport):
        self.transport = transport

    async def capabilities(self):
        return CapabilitySnapshot.model_validate(
            await self.transport.request("request/capabilities", {})
        )

    async def snapshot(self, *, control=False):
        suffix = "request/control/snapshot" if control else "request/snapshot"
        return NodeSnapshot.model_validate(
            await self.transport.request(suffix, {}, control=control)
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

    async def control(self, kind, request):
        return ControlReceipt.model_validate(
            await self.transport.request(
                f"request/control/{kind}", request.model_dump(), control=True
            )
        )

    async def cancel(self, request):
        return await self.control("cancel", request)


def register_actions(transport, executor):
    async def capabilities(payload):
        Empty.model_validate(payload)
        return executor.capabilities().model_dump()

    async def snapshot(payload):
        Empty.model_validate(payload)
        return executor.snapshot().model_dump()

    async def context(payload):
        Empty.model_validate(payload)
        return executor.context().model_dump()

    async def submit(payload):
        return (await executor.submit(ActionRequest.model_validate(payload))).model_dump()

    async def status(payload):
        request = IdRequest.model_validate(payload)
        result = executor.status(request.action_id)
        return result.model_dump() if result else None

    async def health(payload):
        Empty.model_validate(payload)
        return {
            "backend": executor.backend,
            "executions": executor.executions,
            "callbacks_off_loop": bool(transport.callback_threads - {transport.loop_thread}),
            "control_high_water": transport.control.high_water,
            "normal_high_water": transport.normal.high_water,
        }

    transport.register_handler("request/health", health)
    transport.register_handler("request/capabilities", capabilities)
    # Observation never grants authority; TTS has no hold/resume service.
    transport.register_handler("request/action/context", context, control=True)
    transport.register_handler("request/snapshot", snapshot)
    transport.register_handler("request/control/snapshot", snapshot, control=True)
    transport.register_handler("request/action/submit", submit)
    transport.register_handler("request/action/status", status)
    controls = (
        ("hold", "cancel", "resume") if executor.capabilities().robot_controls else ("cancel",)
    )
    for kind in controls:

        async def control(payload, kind=kind):
            return (
                await executor.control(kind, ControlRequest.model_validate(payload))
            ).model_dump()

        transport.register_handler(f"request/control/{kind}", control, control=True)
    executor.on_event = transport.publish
