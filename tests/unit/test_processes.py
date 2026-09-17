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
    probe.assert_called_once_with(
        [str(ROUTER), "--version"], timeout=5, creationflags=NO_WINDOW
    )


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
