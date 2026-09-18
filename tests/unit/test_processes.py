import subprocess
from unittest.mock import patch

import pytest

from wrs_agent.processes import NO_WINDOW, ROUTER, check_router_version

VERSION_LINE = b"zenohd v1.9.0 built with rustc 1.93.0 (254b59607 2026-01-19)\n"
LOG_LINE = b"2026-09-17T03:12:20.445133Z  INFO main ThreadId(01) zenohd: " + VERSION_LINE


@pytest.mark.parametrize(
    "output",
    [
        VERSION_LINE,
        LOG_LINE + VERSION_LINE,
        LOG_LINE + VERSION_LINE.replace(b"\n", b"\r\n"),
        b"zenohd v1.9.0\n",
        b"zenohd 1.9.0\r\n",
    ],
    ids=["official", "log-prefix", "windows-newlines", "bare-version", "no-v-prefix"],
)
def test_router_version_accepts_pinned_version_with_logging(output):
    with patch("wrs_agent.processes.subprocess.check_output", return_value=output) as probe:
        check_router_version()
    probe.assert_called_once_with([str(ROUTER), "--version"], timeout=5, creationflags=NO_WINDOW)


@pytest.mark.parametrize(
    "output",
    [
        b"zenohd v1.10.1 built with rustc\n",
        b"zenohd v1.9.00 built with rustc\n",
        b"zenohd v1.9.0-dev built with rustc\n",
        LOG_LINE + b"zenohd v1.10.1 built with rustc\n",
        LOG_LINE,
        b"",
    ],
    ids=["different", "longer-version", "prerelease", "misleading-log", "log-only", "empty"],
)
def test_router_version_rejects_mismatch_with_diagnostics(output):
    with (
        patch("wrs_agent.processes.subprocess.check_output", return_value=output),
        pytest.raises(RuntimeError, match="zenohd_version_mismatch") as error,
    ):
        check_router_version()
    assert "expected 1.9.0" in str(error.value)
    assert str(ROUTER) in str(error.value)
    assert repr(output.decode().strip()) in str(error.value)


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["zenohd", "--version"], output=VERSION_LINE),
        subprocess.TimeoutExpired(["zenohd", "--version"], 5),
    ],
    ids=["nonzero-exit", "timeout"],
)
def test_router_version_probe_failure_is_not_accepted(error):
    with (
        patch("wrs_agent.processes.subprocess.check_output", side_effect=error),
        pytest.raises(type(error)),
    ):
        check_router_version()


@pytest.mark.parametrize(
    "arguments",
    [
        ["agent", "--model-provider", "glm"],
        ["agent", "--model-provider", "glm", "--live-model", "--deferred-planner"],
        ["wrs", "--duration", "0"],
        ["wrs", "--backend", "hardware"],
    ],
)
async def test_cli_rejects_unsafe_configuration_before_starting(monkeypatch, arguments):
    import sys

    from wrs_agent import __main__ as cli

    async def must_not_start(args):
        pytest.fail("Invalid command must not start a node or contact a model")

    monkeypatch.setattr(sys, "argv", ["wrs_agent", *arguments])
    monkeypatch.setattr(cli, "run_node", must_not_start)
    with pytest.raises(SystemExit) as error:
        await cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize("token", [None, "short", "x" * 129])
async def test_connect_rejects_missing_or_invalid_credential_before_open(monkeypatch, token):
    from wrs_agent import System

    if token is None:
        monkeypatch.delenv("WRS_AGENT_TOKEN", raising=False)
    else:
        monkeypatch.setenv("WRS_AGENT_TOKEN", token)

    def unexpected(*args):
        pytest.fail("Invalid credential must not open a transport")

    monkeypatch.setattr("wrs_agent.system.Transport", unexpected)
    with pytest.raises(ValueError, match="WRS_AGENT_TOKEN"):
        async with System.connect():
            pytest.fail("Connection must not be admitted")


async def test_partial_connection_failure_closes_previously_opened_sessions(monkeypatch):
    from wrs_agent import System

    monkeypatch.setenv("WRS_AGENT_TOKEN", "unit-credential-only")
    opened, closed = [], []

    class ConnectionFixture:
        def __init__(self, *args):
            if opened:
                raise OSError("second session failed")
            opened.append(self)

        async def close(self):
            closed.append(self)

    monkeypatch.setattr("wrs_agent.system.Transport", ConnectionFixture)
    with pytest.raises(OSError, match="second session"):
        async with System.connect():
            pytest.fail("Partial connection must not be admitted")
    assert len(opened) == 1 and closed == opened


@pytest.mark.parametrize("profile", ["vision", "voice"])
def test_launch_rejects_unimplemented_or_incomplete_profile(tmp_path, profile):
    from wrs_agent.processes import LocalStack

    config = tmp_path / "bindings.toml"
    config.write_text(
        f'[nodes.{profile}]\ntype="{profile}"\nsuffix="-{profile}"\n'
        "actions=false\nenabled=true\n[skills]\n",
        encoding="utf-8",
    )
    reason = "node_type_not_implemented" if profile == "vision" else "voice_requires"
    with pytest.raises(ValueError, match=reason):
        LocalStack(bindings=config)


@pytest.mark.parametrize("env_id", ["../outside", "..", ".", "a/b", "a" * 81])
def test_local_namespace_rejected_before_creating_files_or_processes(env_id):
    from wrs_agent.processes import LocalStack

    with pytest.raises(ValueError, match="invalid_namespace"):
        LocalStack(env_id=env_id)
