"""Real WRS virtual FK through the same small API; never connects hardware."""

import argparse
import time

from wrs_agent import launch, step


def run(cancel):
    # 用真实 WRS 模型做虚拟正运动学（FK：由关节角求位姿），不连接控制器或实机。
    with launch(backend="wrs_virtual", duration=1.0) as system:
        print("nodes", system.nodes())
        motion = system.action("move_named_pose", pose="B")
        print("receipt", motion.receipt.model_dump())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = motion.status()
            print("status", status.model_dump())
            if cancel and status.progress > 0.1:
                # cancel 返回取消受理结果；在途 FK 调用结束后才能确认停止。
                print("cancel", motion.cancel().model_dump())
                break
            if status.state == "SUCCEEDED":
                break
            # 节点在独立进程执行，脚本休眠不会暂停虚拟运动。
            time.sleep(0.1)
        else:
            raise TimeoutError("motion_progress_timeout")
        # 无论正常完成还是取消，都通过终态确认结果，而非只看提交/取消回执。
        result = motion.wait()
        assert result.state == ("CANCELLED" if cancel else "SUCCEEDED")
        print("snapshot", system.snapshot().model_dump())
        if cancel:
            # resume 只恢复接收新动作的资格，不续跑已取消动作；下面另建回 home 的任务。
            assert system.resume().accepted
        system.start(step("move_named_pose", pose="home"))
        assert system.wait()["state"] == "SUCCEEDED"
        print("PASS: real WRS virtual FK; pick/place unsupported; hardware disabled")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cancel", action="store_true")
    run(parser.parse_args().cancel)
