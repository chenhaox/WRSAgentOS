"""Agent coordinates; Voice directly controls TTS/WRS. Replay, not real audio."""

from wrs_agent import launch, step


def main():
    with launch(duration=2.0) as system:
        print("nodes", {n: s["health"] for n, s in system.nodes().items()})
        print("用户：把 A 放到 B，并告诉我你正在做什么。")
        # step 只描述步骤；after 表示前一步成功后才能执行，不按代码书写顺序调度。
        pick = step("pick", object="A")
        place = step("place", object="A", target="B", after=pick)
        # 显式计划不需要 Planner。播报与抓取没有依赖且使用不同资源，可以并行。
        system.start(
            step("speak", text="我正在处理。"),
            pick,
            place,
            # 放置完成后另行核对物体位置，不能只凭动作返回就认定目标达成。
            step("verify", object="A", target="B", after=place),
        )
        for state in system.watch():
            if len(state["active_actions"]) == 2:
                break
        before = system.snapshot()
        print("并行 Action", state["active_actions"])
        print("用户：你做到哪一步了？")
        # replay 输入已分类的交互意图，演示 Voice 路由；这里没有麦克风或语音识别。
        answer = system.replay("query")
        print("状态", answer["task"]["state"])
        # control_epoch 是节点的控制版本；只读查询不应撤销已有动作的授权。
        assert system.snapshot().control_epoch == before.control_epoch
        print("用户：别说了。")
        # barge_in 只取消 TTS 播报，机械臂动作及其控制版本保持不变。
        print("Voice → TTS", system.replay("barge_in"))
        assert system.snapshot().active_action == before.active_action
        assert system.snapshot().control_epoch == before.control_epoch
        print("用户：停一下。")
        # 明确停止由 Voice 直达机器人节点，不等 Planner；随后检查停止是否已确认。
        print("Voice → WRS", system.replay("stop"))
        result = system.wait()
        assert system.snapshot().stop_confirmed
        assert result["planner_calls"] == 0
        print("结果", result["state"], "Planner 调用", result["planner_calls"])
        print("PASS: real Zenoh, four independent nodes; Mock actions and voice replay")


if __name__ == "__main__":
    main()
