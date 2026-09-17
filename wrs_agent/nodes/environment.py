"""Register narrow services on the one Mock execution owner."""

from wrs_agent.schemas import ActionRequest, ControlRequest, Empty, IdRequest


def register_actions(transport, environment):
    async def capabilities(payload):
        Empty.model_validate(payload)
        return environment.capabilities().model_dump()

    async def snapshot(payload):
        Empty.model_validate(payload)
        return environment.snapshot().model_dump()

    async def submit(payload):
        return (await environment.submit(ActionRequest.model_validate(payload))).model_dump()

    async def status(payload):
        request = IdRequest.model_validate(payload)
        result = environment.status(request.action_id)
        return result.model_dump() if result else None

    async def health(payload):
        Empty.model_validate(payload)
        return {
            "backend": environment.backend,
            "executions": environment.executions,
            "callbacks_off_loop": bool(transport.callback_threads - {transport.loop_thread}),
            "control_high_water": transport.control.high_water,
            "normal_high_water": transport.normal.high_water,
        }

    transport.register_handler("request/health", health)
    transport.register_handler("request/capabilities", capabilities)
    transport.register_handler("request/snapshot", snapshot)
    transport.register_handler("request/control/snapshot", snapshot, control=True)
    transport.register_handler("request/action/submit", submit)
    transport.register_handler("request/action/status", status)
    for kind in ("hold", "cancel", "resume"):

        async def control(payload, kind=kind):
            return (
                await environment.control(kind, ControlRequest.model_validate(payload))
            ).model_dump()

        transport.register_handler(f"request/control/{kind}", control, control=True)
    environment.on_event = transport.publish
