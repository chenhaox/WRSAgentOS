"""Register narrow services on the one execution owner."""

from wrs_agent.schemas import ActionRequest, ControlRequest, Empty, IdRequest


def register_actions(transport, provider):
    async def capabilities(payload):
        Empty.model_validate(payload)
        return provider.capabilities().model_dump()

    async def snapshot(payload):
        Empty.model_validate(payload)
        return provider.snapshot().model_dump()

    async def context(payload):
        Empty.model_validate(payload)
        return provider.context().model_dump()

    async def submit(payload):
        return (await provider.submit(ActionRequest.model_validate(payload))).model_dump()

    async def status(payload):
        request = IdRequest.model_validate(payload)
        result = provider.status(request.action_id)
        return result.model_dump() if result else None

    async def health(payload):
        Empty.model_validate(payload)
        return {
            "backend": provider.backend,
            "executions": provider.executions,
            "callbacks_off_loop": bool(transport.callback_threads - {transport.loop_thread}),
            "control_high_water": transport.control.high_water,
            "normal_high_water": transport.normal.high_water,
        }

    transport.register_handler("request/health", health)
    transport.register_handler("request/capabilities", capabilities)
    # Legacy snapshots remain readable; TTS has no hold/resume service.
    transport.register_handler("request/action/context", context, control=True)
    if hasattr(provider, "snapshot"):
        transport.register_handler("request/snapshot", snapshot)
        transport.register_handler("request/control/snapshot", snapshot, control=True)
    transport.register_handler("request/action/submit", submit)
    transport.register_handler("request/action/status", status)
    controls = (
        ("hold", "cancel", "resume") if provider.capabilities().robot_controls else ("cancel",)
    )
    for kind in controls:

        async def control(payload, kind=kind):
            return (
                await provider.control(kind, ControlRequest.model_validate(payload))
            ).model_dump()

        transport.register_handler(f"request/control/{kind}", control, control=True)
    provider.on_event = transport.publish
