"""Launch only owned processes; readiness uses TCP and authenticated RPC probes."""

import asyncio
import contextlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from wrs_agent.bindings import load_bindings
from wrs_agent.schemas import new_id
from wrs_agent.transport import Transport

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(r"D:\code\venv312\.venv\Scripts\python.exe")
ROUTER_VERSION = "1.9.0"
ROUTER = ROOT / f".local/zenoh-{ROUTER_VERSION}/zenohd.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class InstanceLock:
    def __init__(self, name):
        import msvcrt

        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", name):
            raise ValueError("invalid_instance_name")
        path = Path(tempfile.gettempdir()) / "wrs-agent-locks" / f"{name}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        if path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            raise RuntimeError("environment_already_running") from None

    def close(self):
        self.file.close()


def python_command(*args):
    if Path(sys.executable).resolve() != PYTHON.resolve():
        raise RuntimeError(f"Required Python: {PYTHON}")
    return [str(PYTHON), "-X", "utf8", "-S", str(ROOT / "scripts/run.py"), *map(str, args)]


def check_router_version():
    output = subprocess.check_output(
        [str(ROUTER), "--version"], timeout=5, creationflags=NO_WINDOW
    ).decode("utf-8", errors="replace")
    # RUST_LOG=info/debug adds a timestamped log before the standalone version line.
    match = re.search(r"(?m)^zenohd v?(\S+)", output)
    if match is None or match.group(1) != ROUTER_VERSION:
        raise RuntimeError(
            f"zenohd_version_mismatch: expected {ROUTER_VERSION}; "
            f"router={ROUTER}; output={output.strip()!r}"
        )


class LocalStack:
    def __init__(
        self,
        *,
        runtime=True,
        duration=0.4,
        fault=None,
        port=0,
        tts=True,
        voice=False,
        deferred=False,
    ):
        self.with_runtime, self.duration, self.fault = runtime, duration, fault
        self.with_tts, self.with_voice, self.deferred = tts, voice, deferred
        self.node_transports = {}
        self.node_suffixes, self.skill_bindings = load_bindings()
        self.port = port
        self.site = "local"
        self.env_id = "mock-" + new_id()[:12]
        self.token = secrets.token_urlsafe(32)
        self.processes = []
        self.logs = []
        self.transport = None
        self.directory = ROOT / ".local/runs" / self.env_id

    def _spawn(self, name, command):
        log = (self.directory / f"{name}.log").open("wb")
        self.logs.append(log)
        env = os.environ.copy()
        env["WRS_AGENT_TOKEN"] = self.token
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW,
        )
        self.processes.append(process)
        return process

    def start_router(self):
        # Refuse occupied ports; never attach to an unknown existing service.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", self.port))
            self.port = probe.getsockname()[1]
        self.endpoint = f"tcp/127.0.0.1:{self.port}"
        config = {
            "mode": "router",
            "listen": {"endpoints": [self.endpoint]},
            "scouting": {"multicast": {"enabled": False}, "gossip": {"enabled": False}},
            "adminspace": {"enabled": False},
            "plugins_loading": {"enabled": False},
        }
        config_path = self.directory / "router.json5"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        check_router_version()
        process = self._spawn("router", [str(ROUTER), "-c", str(config_path)])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Router exited; see {self.directory}")
            with socket.socket() as probe:
                probe.settimeout(0.05)
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return process
            time.sleep(0.02)
        raise TimeoutError("router_ready_timeout")

    def node_command(self, role):
        result = python_command(
            "-m",
            "wrs_agent",
            role,
            "--endpoint",
            self.endpoint,
            "--site",
            self.site,
            "--env-id",
            self.env_id,
            "--journal",
            self.directory / f"{role}.sqlite3",
            "--duration",
            self.duration,
        )
        if self.deferred and role == "runtime":
            result.append("--deferred-planner")
        if self.fault:
            result.extend(["--fault", self.fault])
        return result

    async def _ready(self, suffix, bus=None):
        async with asyncio.timeout(10):
            while True:
                if any(p.poll() is not None for p in self.processes):
                    raise RuntimeError(f"Node exited; see {self.directory}")
                try:
                    return await (bus or self.transport).request(suffix, {}, timeout=0.5)
                except TimeoutError:
                    await asyncio.sleep(0.02)

    async def __aenter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(self.start_router)
            self._spawn("environment", self.node_command("environment"))
            self.transport = Transport(self.endpoint, self.site, self.env_id, self.token, "input")
            await self._ready("request/capabilities")
            self.node_transports["wrs"] = self.transport
            if self.with_tts:
                self._spawn("tts", self.node_command("tts"))
                tts_bus = Transport(
                    self.endpoint,
                    self.site,
                    self.env_id + self.node_suffixes["tts"],
                    self.token,
                    "input",
                )
                self.node_transports["tts"] = tts_bus
                await self._ready("request/capabilities", tts_bus)
            if self.with_runtime:
                self._spawn("runtime", self.node_command("runtime"))
                await self._ready("request/task/status")
            if self.with_voice:
                self._spawn("voice", self.node_command("voice"))
                voice_bus = Transport(
                    self.endpoint, self.site, self.env_id + "-voice", self.token, "input"
                )
                self.node_transports["voice"] = voice_bus
                await self._ready("request/health", voice_bus)
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *exc):
        if self.transport:
            # Graceful node shutdown uses the independent control lane.
            for role, bus in [
                ("voice", self.node_transports.get("voice")),
                ("runtime", self.transport if self.with_runtime else None),
                ("tts", self.node_transports.get("tts")),
                ("environment", self.transport),
            ]:
                if bus is not None:
                    with contextlib.suppress(Exception):
                        await bus.request(f"request/{role}/shutdown", {}, timeout=1, control=True)
            for bus in set(self.node_transports.values()) | {self.transport}:
                await bus.close()
        await asyncio.to_thread(self._reap)

    def _reap(self):
        for process in reversed(self.processes):
            if process is self.processes[0] and process.poll() is None:
                process.terminate()
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
        for log in self.logs:
            log.close()
