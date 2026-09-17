"""Real WRS virtual FK through the same small API; never connects hardware."""

import argparse
import asyncio

from wrs_agent import System, step


async def run(cancel):
    async with System.local(backend="wrs_virtual", duration=1.0) as system:
        print("nodes", await system.nodes())
        motion = await system.action("move_named_pose", pose="B")
        print("receipt", motion.receipt.model_dump())
        async with asyncio.timeout(5):
            while True:
                status = await motion.status()
                print("status", status.model_dump())
                if cancel and status.progress > 0.1:
                    print("cancel", (await motion.cancel()).model_dump())
                    break
                if status.state == "SUCCEEDED":
                    break
                await asyncio.sleep(0.1)
        result = await motion.wait()
        assert result.state == ("CANCELLED" if cancel else "SUCCEEDED")
        print("snapshot", (await system.snapshot()).model_dump())
        if cancel:
            assert (await system.resume()).accepted
        await system.start(step("move_named_pose", pose="home"))
        assert (await system.wait())["state"] == "SUCCEEDED"
        print("PASS: real WRS virtual FK; pick/place unsupported; hardware disabled")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cancel", action="store_true")
    asyncio.run(run(parser.parse_args().cancel))
