"""Real WRS virtual FK through the same small API; never connects hardware."""

import argparse
import time

from wrs_agent import launch, step


def run(cancel):
    with launch(backend="wrs_virtual", duration=1.0) as system:
        print("nodes", system.nodes())
        motion = system.action("move_named_pose", pose="B")
        print("receipt", motion.receipt.model_dump())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = motion.status()
            print("status", status.model_dump())
            if cancel and status.progress > 0.1:
                print("cancel", motion.cancel().model_dump())
                break
            if status.state == "SUCCEEDED":
                break
            time.sleep(0.1)
        else:
            raise TimeoutError("motion_progress_timeout")
        result = motion.wait()
        assert result.state == ("CANCELLED" if cancel else "SUCCEEDED")
        print("snapshot", system.snapshot().model_dump())
        if cancel:
            assert system.resume().accepted
        system.start(step("move_named_pose", pose="home"))
        assert system.wait()["state"] == "SUCCEEDED"
        print("PASS: real WRS virtual FK; pick/place unsupported; hardware disabled")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cancel", action="store_true")
    run(parser.parse_args().cancel)
