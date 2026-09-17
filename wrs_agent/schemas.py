"""Strict, bounded JSON contracts. No device or provider objects cross this boundary."""

import json
from graphlib import TopologicalSorter
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_BYTES = 65536
Name = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")]
Counter = Annotated[int, Field(ge=0, le=2**53)]
Scalar = str | int | float | bool | None
State = Literal[
    "ACCEPTED",
    "RUNNING",
    "VERIFYING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLING",
    "CANCELLED",
    "UNKNOWN",
]
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "UNKNOWN"}


def new_id() -> str:
    return uuid4().hex


class Boundary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)


class Envelope(Boundary):
    schema_version: Literal[1] = 1
    message_id: Name = Field(default_factory=new_id)
    trace_id: Name = Field(default_factory=new_id)
    source: Name
    category: Literal["control", "interactive", "background"] = "interactive"
    session: Name
    env_id: Name
    auth: Annotated[str, Field(min_length=16, max_length=128, repr=False)]
    payload: dict


def encode(value: dict | Boundary) -> bytes:
    if isinstance(value, Boundary):
        value = value.model_dump(mode="json")
    result = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(result) > MAX_BYTES:
        raise ValueError("payload_too_large")
    return result


def decode(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("payload_too_large")
    result = json.loads(
        data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite"))
    )
    if not isinstance(result, dict):
        raise ValueError("object_required")
    return result


class ActionRequest(Boundary):
    action_id: Name
    task_id: Name
    task_revision: Counter
    boot_id: Name
    control_epoch: Counter
    lease_id: Name
    skill: Name
    version: Literal[1] = 1
    args: Annotated[dict[Name, Scalar], Field(max_length=8)] = Field(default_factory=dict)
    world_version: Counter


class ActionStatus(Boundary):
    action_id: Name
    state: State
    sequence: Counter = 0
    reason: Annotated[str, Field(max_length=240)] = ""
    verification: Literal["PENDING", "PASS", "FAIL", "INCONCLUSIVE"] = "PENDING"


class ActionReceipt(Boundary):
    accepted: bool
    reason: str = ""
    status: ActionStatus | None = None


class ControlRequest(Boundary):
    interrupt_id: Name
    boot_id: Name
    control_epoch: Counter
    action_id: Name | None = None
    world_version: Counter | None = None


class ControlReceipt(Boundary):
    accepted: bool
    reason: str = ""
    control_epoch: Counter
    phase: Literal["REJECTED", "STOPPING", "STOPPED", "UNKNOWN", "RESUMED"]


class WorldSnapshot(Boundary):
    boot_id: Name
    control_epoch: Counter
    world_version: Counter
    lease_id: Name
    admission: Literal["OPEN", "HELD", "UNKNOWN"]
    held_object: Name | None = None
    objects: dict[Name, Name] = Field(default_factory=dict)
    pose: Name | None = None
    facts: dict[Name, Scalar] = Field(default_factory=dict)
    active_action: Name | None
    stop_confirmed: bool


class CapabilitySnapshot(Boundary):
    backend: Literal["mock", "mock_tts"] = "mock"
    resources: list[Name] = Field(default_factory=list)
    skills: list[Name]
    hardware: Literal[False] = False
    controlled_stop: bool = True
    controller_flush: bool = True
    verification: Literal["virtual_state"] = "virtual_state"


class Step(Boundary):
    step_id: Name
    skill: Name
    version: Literal[1] = 1
    args: Annotated[dict[Name, Scalar], Field(max_length=8)] = Field(default_factory=dict)
    category: Literal["interactive", "background"] = "interactive"
    depends_on: Annotated[list[Name], Field(max_length=12)] = Field(default_factory=list)


class Plan(Boundary):
    steps: Annotated[list[Step], Field(min_length=1, max_length=12)]

    @model_validator(mode="after")
    def valid_dag(self):
        ids = {s.step_id for s in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError("duplicate_step")
        if any(d not in ids for s in self.steps for d in s.depends_on):
            raise ValueError("missing_dependency")
        tuple(TopologicalSorter({s.step_id: s.depends_on for s in self.steps}).static_order())
        return self


class TaskRequest(Boundary):
    request_id: Name
    plan: Plan


class TaskControl(Boundary):
    request_id: Name
    replacement: Plan | None = None


class IdRequest(Boundary):
    action_id: Name


class Empty(Boundary):
    pass


class GoalRequest(Boundary):
    request_id: Name
    goal: str = Field(min_length=1, max_length=1024)


class Interaction(Boundary):
    event_id: Name
    kind: Literal["vad", "ack", "query", "append", "revise", "stop", "barge_in"]
    confidence: float = Field(default=1.0, ge=0, le=1)
    quoted: bool = False
    negated: bool = False
    plan: Plan | None = None
