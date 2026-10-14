"""Finite task artifacts; references identify data, never grant robot permission."""

import hashlib
import time
from collections import OrderedDict
from pathlib import Path

from pydantic import Field

from wrs_agent.errors import AgentError
from wrs_agent.schemas import ActionState, Boundary, Name


class ArtifactOwner(Boundary):
    node_id: Name
    boot_id: Name
    control_epoch: int = Field(ge=0)
    task_id: Name
    action_id: Name


class ArtifactRequest(Boundary):
    artifact_id: Name
    task_id: Name


def artifact_owner(executor):
    request = executor.active_request()
    return ArtifactOwner(node_id=executor.node_id, boot_id=request.boot_id,
                         control_epoch=request.control_epoch, task_id=request.task_id,
                         action_id=request.action_id)


class ArtifactCache:
    """At most two results and 64 MiB, including their retained complete observations."""

    def __init__(self, *, capacity=2, max_bytes=64 * 1024 * 1024, ttl=30.0):
        if capacity < 1 or max_bytes < 1 or ttl <= 0:
            raise ValueError("invalid_artifact_limits")
        self.capacity, self.max_bytes, self.ttl = capacity, max_bytes, ttl
        self.entries = OrderedDict()
        self.nbytes = 0

    def _remove(self, key):
        self.nbytes -= self.entries.pop(key)[2]

    def expire(self):
        now = time.monotonic()
        for key, (_, _, _, observed_at) in list(self.entries.items()):
            if now - observed_at > self.ttl:
                self._remove(key)

    def put(self, key, owner, value, *, nbytes, observed_at):
        self.expire()
        if nbytes > self.max_bytes:
            raise AgentError("artifact_too_large")
        if time.monotonic() - observed_at > self.ttl:
            raise AgentError("observation_stale")
        if key in self.entries:
            raise AgentError("duplicate_artifact")
        while self.entries and (len(self.entries) >= self.capacity
                                or self.nbytes + nbytes > self.max_bytes):
            self._remove(next(iter(self.entries)))
        self.entries[key] = (owner.model_copy(deep=True), value, nbytes, observed_at)
        self.nbytes += nbytes

    def get(self, request, executor):
        self.expire()
        entry = self.entries.get(request.artifact_id)
        if entry is None:
            raise AgentError("artifact_expired")
        owner, value, _, observed_at = entry
        if owner.task_id != request.task_id:
            raise AgentError("artifact_task_mismatch")
        status = executor.status(owner.action_id)
        if (owner.boot_id != executor.boot_id or owner.control_epoch != executor.epoch
                or status is None or status.state != ActionState.SUCCEEDED):
            raise AgentError("artifact_invalidated")
        return value, time.monotonic() - observed_at


def checkpoint_path(weights, sha256):
    """Verify before importing a model SDK; this function never downloads anything."""
    path = Path(weights).resolve(strict=True)
    if not path.is_file():
        raise ValueError("local_checkpoint_required")
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != sha256:
        raise ValueError("checkpoint_checksum_mismatch")
    return path
