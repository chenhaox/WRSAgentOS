"""Parallel WRS/TTS, hung mock planner, scoped interruption, then A->C."""

import asyncio
import json

from wrs_agent.processes import LocalStack
from wrs_agent.schemas import ActionRequest, Plan, Step, new_id


async def wait_for(call, predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while True:
            value = await call()
            if predicate(value):
                return value
            await asyncio.sleep(0.01)


async def main():
    # deferred 让 Mock Planner 挂起，验证等待模型时仍可查询和停止；不发送真实 API 请求。
    async with LocalStack(duration=0.8, deferred=True) as stack:
        system = stack.system
        robot, tts = system.clients["wrs"], system.clients["tts"]
        # depends_on 定义先后关系；speech 与 pick 无依赖且资源不同，因此可以同时执行。
        plan = Plan(
            steps=[
                Step(step_id="speech", skill="speak", args={"text": "Moving A to B"}),
                Step(step_id="pick", skill="pick", args={"object": "A"}),
                Step(
                    step_id="place",
                    skill="place",
                    args={"object": "A", "target": "B"},
                    depends_on=["pick"],
                ),
                Step(
                    step_id="verify",
                    skill="verify",
                    args={"object": "A", "target": "B"},
                    depends_on=["place"],
                ),
            ]
        )
        # request_id 用于请求去重；确定计划后由 Runtime 生成不可变的 task_id。
        await system.start(*plan.steps)
        running = await wait_for(
            system.status,
            lambda s: len(s["active_actions"]) == 2,
        )
        speech_id = running["active_actions"]["speech"]
        await wait_for(lambda: tts.status(speech_id), lambda s: s.state == "RUNNING")
        await system.goal("next task")
        await wait_for(system.status, lambda s: s["planner_calls"] == 1)
        # Voice 直接取消 TTS，不等挂起的 Planner。
        await system.replay("barge_in")
        await wait_for(lambda: tts.status(speech_id), lambda s: s.state == "CANCELLED")
        world = await wait_for(
            robot.context, lambda w: w.data.held_object == "A" and w.active_action is not None
        )
        query = await system.replay("query")
        assert query["task"]["state"] == "RUNNING"
        assert (await robot.snapshot()).control_epoch == world.control_epoch
        # 任务级控制必须指明 task_id，避免迟到请求误停替换后的任务。
        held = await system.agent.request(
            "request/task/hold",
            {"request_id": new_id(), "task_id": running["task_id"]},
            control=True,
        )
        assert held["accepted"]
        # 受理停止不代表已经停稳；确认停止后仍保持持物，取消不会撤销已发生的抓取。
        stopped = await wait_for(robot.snapshot, lambda w: w.stop_confirmed)
        assert stopped.data.held_object == "A"
        # 故意使用停止前的凭证：hold 已更新 control_epoch，换新 action_id 也无法重获授权。
        stale = ActionRequest(
            action_id=new_id(),
            task_id=running["task_id"],
            task_revision=running["revision"],
            boot_id=world.boot_id,
            control_epoch=world.control_epoch,
            lease_id=world.lease_id,
            world_version=world.world_version,
            skill="place",
            args={"object": "A", "target": "B"},
        )
        rejected = await robot.submit(stale)
        assert not rejected.accepted and rejected.reason == "stale_epoch"
        # 放行迟到的模型输出，验证旧规划已失效，不能重新启动动作。
        await system.agent.request("request/test/planner/release", {}, control=True)
        await wait_for(system.status, lambda s: s["planning"] == "STALE")
        # 按停止后的实际状态规划剩余动作：A 已在手中，无需再 pick。
        remaining = Plan(
            steps=[
                Step(step_id="place", skill="place", args={"object": "A", "target": "C"}),
                Step(
                    step_id="verify",
                    skill="verify",
                    args={"object": "A", "target": "C"},
                    depends_on=["place"],
                ),
            ]
        )
        # 替换创建新任务；旧 task_id 指定被替换对象，supersedes 保留两次执行的关联。
        await system.agent.request(
            "request/task/replace",
            {
                "request_id": new_id(),
                "task_id": running["task_id"],
                "replacement": remaining.model_dump(),
            },
            control=True,
        )
        final = await wait_for(
            system.status,
            lambda s: s["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN"},
        )
        state = await robot.snapshot()
        assert final["task_id"] != running["task_id"]
        assert final["supersedes"] == running["task_id"]
        assert final["state"] == "SUCCEEDED", final
        assert state.data.objects["A"] == "C" and state.data.held_object is None
        print(
            json.dumps(
                {
                    "profile": "real_zenoh_mock_nodes",
                    "parallel_nodes": ["wrs", "tts"],
                    "model": "deferred_mock_no_API_call",
                    "tts_cancelled_only": True,
                    "query_during_motion": True,
                    "held_after_stop": stopped.data.held_object,
                    "old_action_rejected": rejected.reason,
                    "late_model": "rejected_before_replacement",
                    "replacement_has_new_id": final["task_id"] != running["task_id"],
                    "final_task": final["state"],
                    "final_A_location": state.data.objects["A"],
                    "verification": "virtual_state",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
