"""Runtime services return quickly; task execution runs independently."""

from wrs_agent.schemas import Empty, GoalRequest, TaskControl, TaskRequest


def register_runtime(transport, runtime):
    async def start(payload):
        return await runtime.start(TaskRequest.model_validate(payload))

    async def status(payload):
        Empty.model_validate(payload)
        return runtime.snapshot()

    async def hold(payload):
        return await runtime.hold(TaskControl.model_validate(payload))

    async def replace(payload):
        return await runtime.replace(TaskControl.model_validate(payload))

    async def goal(payload):
        return await runtime.goal(GoalRequest.model_validate(payload))

    async def enqueue(payload):
        return await runtime.enqueue(TaskRequest.model_validate(payload))

    transport.register_handler("request/task/enqueue", enqueue)
    transport.register_handler("request/task/goal", goal)
    transport.register_handler("request/task/start", start)
    transport.register_handler("request/task/status", status)
    transport.register_handler("request/task/hold", hold, control=True)
    transport.register_handler("request/task/replace", replace, control=True)
