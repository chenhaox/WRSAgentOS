"""Launch local nodes or connect to running nodes; use step() for task dependencies."""

from wrs_agent.sync import connect, launch
from wrs_agent.system import System, step

__all__ = ["System", "connect", "launch", "step"]
