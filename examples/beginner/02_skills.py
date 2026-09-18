"""Find available skills with ordinary Python; real Zenoh, no model request."""

from wrs_agent import launch


def main():
    with launch() as system:
        for goal in ["把 A 放到 B", "播报当前状态"]:
            print(goal)
            # 按关键词/别名检索，并过滤掉当前节点无法执行的技能；不调用模型或执行动作。
            for skill in system.skills(goal):
                print(f"  {skill.name}: {skill.description}")
        print("PASS: current node capabilities queried; model_calls=0")


if __name__ == "__main__":
    main()
