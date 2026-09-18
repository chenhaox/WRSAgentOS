import pytest

from wrs_agent.processes import LocalStack

pytestmark = pytest.mark.zenoh


@pytest.mark.parametrize("log_level", ["info", "debug"])
async def test_router_start_with_inherited_logging(monkeypatch, log_level):
    monkeypatch.setenv("RUST_LOG", log_level)
    async with LocalStack(bindings="configs/robot.toml") as stack:
        health = await stack.system.clients["wrs"].transport.request("request/health", {})
        assert health["executions"] == 0
    assert all(process.poll() is not None for process in stack.processes)
