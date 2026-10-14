"""启动本组 Router；与 camera 使用同一地址，已有 Router 时直接复用。"""

import asyncio
from pathlib import Path

from examples._session import use_local_token
from wrs_agent.processes import NO_WINDOW, check_router_version, router_path

CONFIG = Path(__file__).resolve().parents[1] / "camera/router.json5"
SESSION = "wrs"


async def main():
    use_local_token(SESSION, create=True)
    executable = router_path()
    await asyncio.to_thread(check_router_version, executable)
    router = await asyncio.create_subprocess_exec(
        str(executable), "-c", str(CONFIG), creationflags=NO_WINDOW,
    )
    try:
        print("Router 地址：tcp/127.0.0.1:7449；继续运行 01_start_camera.py。", flush=True)
        exit_code = await router.wait()
        if exit_code:
            raise SystemExit(exit_code)
    finally:
        if router.returncode is None:
            router.terminate()
        await asyncio.wait_for(router.wait(), timeout=5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Router 已关闭。")
