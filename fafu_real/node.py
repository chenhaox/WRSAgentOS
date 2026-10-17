"""node 模式：把真机作为 WRSAgentOS 的机器人节点（node_type="wrs"）挂到 Zenoh 上。

起这些东西：
1. zenohd 路由器（fafu_config.ENDPOINT，端口被占就拒绝，不连来路不明的服务）；
2. 本进程里的 FafuNode，15 个动作技能都注册在它的 ActionExecutor 上；
3. Agent 节点（python -m wrs_agent agent，子进程）：看板点技能、以后的语音指令都经过它下发任务；
4. 看板（python -m wrs_agent ui，子进程），浏览器开 http://127.0.0.1:UI_PORT。
3、4 由 NODE_START_AGENT / NODE_START_UI 开关。

    python fafu_real/main.py --mode node              # dry-run
    python fafu_real/main.py --mode node --execute    # 真机

令牌（WRS_AGENT_TOKEN）没设置时随机生成，只放在子进程环境变量里，不写文件、不打印。
Agent / TTS / STT 节点以后接同一个 ENDPOINT 和 ENV_ID 即可（令牌要一致）。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import _paths  # noqa: F401
import fafu_config as C
from skills import Robot, make_executor

from wrs_agent.nodes import Node
from wrs_agent.nodes.serve import serve_node
from wrs_agent.processes import NO_WINDOW, check_router_version, router_path

RUN_DIR = Path(_paths.AGENTOS_ROOT) / ".local" / "runs" / "fafu-real"


def _endpoint_port() -> int:
    host_port = C.ENDPOINT.split("/", 1)[1]
    host, port = host_port.rsplit(":", 1)
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit(f"ENDPOINT 只支持本机地址，现在是 {C.ENDPOINT}")
    return int(port)


def start_router(log_file):
    port = _endpoint_port()
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(
                f"端口 {port} 已被占用。关掉占用它的程序，或改 fafu_config.ENDPOINT"
            ) from None
    config = {
        "mode": "router",
        "listen": {"endpoints": [C.ENDPOINT]},
        "scouting": {"multicast": {"enabled": False}, "gossip": {"enabled": False}},
        "adminspace": {"enabled": False},
        "plugins_loading": {"enabled": False},
    }
    path = RUN_DIR / "router.json5"
    path.write_text(json.dumps(config), encoding="utf-8")
    router = router_path()
    check_router_version(router)
    process = subprocess.Popen(
        [str(router), "-c", str(path)],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=NO_WINDOW,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"路由器退出了，看 {RUN_DIR / 'router.log'}")
        with socket.socket() as probe:
            probe.settimeout(0.05)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return process
        time.sleep(0.02)
    process.terminate()
    raise SystemExit("路由器 5 秒内没起来")


def start_role(role, options, log_file, env):
    """用本仓库自带的节点（agent / ui）起一个子进程，连同一个路由器和 env_id。"""
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "wrs_agent",
        role,
        "--endpoint",
        C.ENDPOINT,
        "--env-id",
        C.ENV_ID,
        "--journal",
        str(RUN_DIR / f"{role}.sqlite3"),
        "--options",
        json.dumps(options),
    ]
    return subprocess.Popen(
        command,
        cwd=_paths.AGENTOS_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=NO_WINDOW,
    )


def node_class(robot: Robot):
    class FafuNode(Node):
        node_type = "wrs"
        action_service = True

        async def setup(self):
            self.actions(make_executor(robot, self.journal))

    return FafuNode


async def _serve(robot: Robot):
    await serve_node(
        node_class(robot),
        node_id="wrs",
        endpoint=C.ENDPOINT,
        env_id=C.ENV_ID,
        journal=str(RUN_DIR / "wrs.sqlite3"),
    )


def run(robot: Robot, *, with_ui: bool = True) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("WRS_AGENT_TOKEN", secrets.token_urlsafe(32))
    # 子进程只需要本仓库的 wrs_agent，不要继承比赛仓库那份 wrs。
    env = {**os.environ, "PYTHONPATH": _paths.AGENTOS_ROOT}
    logs = {name: open(RUN_DIR / f"{name}.log", "wb") for name in ("router", "agent", "ui")}
    children = []
    try:
        children.append(start_router(logs["router"]))
        if C.NODE_START_AGENT:
            children.append(start_role("agent", {"live_model": False}, logs["agent"], env))
            print(f"[node] Agent 节点已启动（日志 {RUN_DIR / 'agent.log'}）")
        if with_ui and C.NODE_START_UI:
            children.append(start_role("ui", {"port": C.UI_PORT, "viewer": False}, logs["ui"], env))
            print(f"[node] 看板：http://127.0.0.1:{C.UI_PORT}  （日志 {RUN_DIR / 'ui.log'}）")
        mode = "真机执行" if robot.execute else "dry-run"
        print(f"[node] {mode}，env_id={C.ENV_ID}，endpoint={C.ENDPOINT}；Ctrl+C 退出")
        try:
            asyncio.run(_serve(robot))
        except KeyboardInterrupt:
            print("\n[node] 收到 Ctrl+C")
    finally:
        for child in reversed(children):
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        for log in logs.values():
            log.close()
    return 0
