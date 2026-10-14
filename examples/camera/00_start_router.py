"""终端一：启动相机示例的 Router；保持运行，再运行 01 和 02。Ctrl+C 关闭。"""

import asyncio
from pathlib import Path

from examples._session import use_local_token
from wrs_agent.processes import NO_WINDOW, check_router_version, router_path

CONFIG = Path(__file__).with_name("router.json5")


async def main():
    executable = router_path()
    await asyncio.to_thread(check_router_version, executable)
    router = await asyncio.create_subprocess_exec(
        str(executable), "-c", str(CONFIG), creationflags=NO_WINDOW
    )
    try:
        print("Router 地址：tcp/127.0.0.1:7451；请继续运行 01_start_camera.py。", flush=True)
        exit_code = await router.wait()
        if exit_code:
            raise SystemExit(exit_code)
    finally:
        if router.returncode is None:
            router.terminate()
        await asyncio.wait_for(router.wait(), timeout=5)


if __name__ == "__main__":
    use_local_token("wrs", create=True)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("相机示例 Router 已关闭。")
