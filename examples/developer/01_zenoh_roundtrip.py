"""看两个程序怎样问答、发通知：本机 Zenoh + Mock 机器人，无模型或硬件。"""

import asyncio
import json

from wrs_agent.nodes.actions import action_request
from wrs_agent.processes import LocalStack
from wrs_agent.transport import decode


async def main():
    # LocalStack 是本机启动工具。进入 async with 时，它启动 router 和 Mock 机器人，
    # 等它们就绪；退出时关闭连接并清理自己启动的进程。本例不启动 Agent、TTS 或 Voice。
    async with LocalStack(bindings="configs/robot.toml") as stack:
        print("1. 已启动消息转送程序和 Mock 机器人；当前脚本负责发请求、收消息。")
        # bus 是当前脚本里的通信对象；client 用它向另一个进程中的机器人发送请求。
        # 此例专门演示协议消息；普通动作脚本使用 system.action 即可。
        bus = stack.system.clients["wrs"].transport
        client = stack.system.clients["wrs"]

        # 先做最简单的一问一答：机器人现在在哪里？这只是查询，不会移动机器人。
        snapshot = await client.snapshot()
        print("2. 脚本问：你在哪里？机器人答：", snapshot.data.pose)

        # 订阅就是登记“有动作消息时也发给我”。两个接收者都在当前脚本中。
        # 每个接收者最多暂存 8 条事件；先登记，才能接住接下来动作产生的通知。
        subscriber_a = bus.subscribe("events/action", capacity=8)
        subscriber_b = bus.subscribe("events/action", capacity=8)
        print("3. 两个订阅者已登记，都准备接收机器人发出的动作通知。")

        # observe 是让 Mock 更新观察状态，本例不要求机械臂运动。
        # context 取得当前状态和短期执行凭证；action_request 将它们装进动作请求。
        context = await client.context()
        request = action_request(context, "observe", {}, task_id="roundtrip")
        receipt = await client.submit(request)
        assert receipt.accepted
        print("4. 脚本请求：观察一次。机器人已接收，动作编号：", request.action_id)

        # 回执是“收到请求了”；下面另收动作通知。各检查一条，不据此断言动作最终成功。
        for number, subscriber in enumerate((subscriber_a, subscriber_b), start=1):
            async with asyncio.timeout(2):
                while True:
                    sample = subscriber.try_recv()  # 暂时没有消息时返回 None。
                    if sample is not None:
                        payload = sample.payload.to_bytes()  # 取出消息携带的字节。
                        event = decode(payload)  # 把 JSON 字节转换成 Python 字典。
                        assert event["action_id"] == request.action_id
                        print(f"5. 订阅者 {number} 收到这次动作的通知：{event['state']}")
                        break
                    await asyncio.sleep(0.01)  # 稍等再看，把运行机会让给其他异步工作。

        try:
            # 再故意问一个无人提供的服务：确认脚本会报超时，而不是一直等下去。
            await bus.request("request/absent", {}, timeout=0.15)
        except TimeoutError:
            timed_out = True
            print("6. 故意查询不存在的服务：未收到回复，按预期结束等待。")
        else:
            raise AssertionError("Absent service did not time out")

        health = await bus.request("request/health", {})
        print("7. 再查询机器人健康状态，仍能收到回复。下面输出检查汇总：")
        print(
            json.dumps(
                {
                    "profile": "real_zenoh_loopback",
                    "query": "PASS",
                    "pubsub_fanout": 2,
                    "timeout": timed_out,
                    "health": health,
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
