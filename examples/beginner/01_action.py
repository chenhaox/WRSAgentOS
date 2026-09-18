"""First action: ordinary Python, Mock robot, no model or hardware calls."""

from wrs_agent import launch

# launch 会启动本机通信和独立节点，退出 with 时清理这些进程；默认使用 Mock。
with launch() as system:
    # action 返回这次技能调用的句柄，节点已受理，执行结果需要随后查询。
    action = system.action("move_named_pose", pose="B")
    print("已接收", action.id)
    print("当前状态", action.status().state)
    # wait 等到成功、失败或取消等终态；等待超时不会自动停止动作。
    result = action.wait()
    print("执行结果", result.state)
    print("机器人位置", system.snapshot().data.pose)
    # 同时检查执行状态与后置条件验证；本例验证的是 Mock 状态。
    assert result.state == "SUCCEEDED" and result.verification == "PASS"
