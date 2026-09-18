"""Reuse a verified plan. Real Zenoh and independent nodes; scripted Mock model."""

import json

from wrs_agent import launch


def main():
    with launch(duration=0.03) as system:
        # 先把 A 放在 B，使第一次任务执行前后条件相同，第二次才有机会命中缓存。
        # 直接调用 action 不经过 Planner，因此不计入后面的模型调用次数。
        assert system.action("pick", object="A").wait().state == "SUCCEEDED"
        assert system.action("place", object="A", target="B").wait().state == "SUCCEEDED"
        assert system.action("verify", object="A", target="B").wait().state == "SUCCEEDED"
        calls = []
        # 四次分别验证：首次规划、相同条件复用、目标改变、回到旧目标但物体位置已变。
        for goal in ["put A in B", "put A in B", "put A in C", "put A in B"]:
            # goal 交给 Runtime 选择缓存或 Planner；缓存只存计划结构，每次重新授权执行。
            system.goal(goal)
            result = system.wait()
            assert result["state"] == "SUCCEEDED", result
            calls.append(result["planner_calls"])
            print(json.dumps({
                "profile": "real_zenoh_scripted_mock",
                "goal": goal,
                "cache_hit": result["cache_hit"],
                "reject_reason": result["cache_reject_reason"],
                "model_calls": result["planner_calls"],
                "object_location": system.snapshot().data.objects["A"],
            }))
        # planner_calls 是累计次数；第二次没有增加，后两次均因不能复用而重新规划。
        assert calls == [1, 1, 2, 3] and result["cache_hits"] == 1


if __name__ == "__main__":
    main()
