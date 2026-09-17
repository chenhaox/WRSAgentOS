"""Agent coordinates; Voice directly controls TTS/WRS. Replay, not real audio."""

from wrs_agent import launch, step


def main():
    with launch(duration=2.0) as system:
        print("nodes", {n: s["health"] for n, s in system.nodes().items()})
        print("用户：把 A 放到 B，并告诉我你正在做什么。")
        pick = step("pick", object="A")
        place = step("place", object="A", target="B", after=pick)
        system.start(
            step("speak", text="我正在处理。"),
            pick,
            place,
            step("verify", object="A", target="B", after=place),
        )
        for state in system.watch():
            if len(state["active_actions"]) == 2:
                break
        before = system.snapshot()
        print("并行 Action", state["active_actions"])
        print("用户：你做到哪一步了？")
        answer = system.replay("query")
        print("状态", answer["task"]["state"])
        assert system.snapshot().control_epoch == before.control_epoch
        print("用户：别说了。")
        print("Voice → TTS", system.replay("barge_in"))
        assert system.snapshot().active_action == before.active_action
        assert system.snapshot().control_epoch == before.control_epoch
        print("用户：停一下。")
        print("Voice → WRS", system.replay("stop"))
        result = system.wait()
        assert system.snapshot().stop_confirmed
        assert result["planner_calls"] == 0
        print("结果", result["state"], "Planner 调用", result["planner_calls"])
        print("PASS: real Zenoh, four independent nodes; Mock actions and voice replay")


if __name__ == "__main__":
    main()
