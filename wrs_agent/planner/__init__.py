"""Planner input, decisions and model-backed implementation."""

import time
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field, model_validator

from wrs_agent.planner.providers import ModelClient, ModelRequest, ModelTiming
from wrs_agent.schemas import Boundary, Plan


class PlanRequest(Boundary):
    user_goal: str = Field(min_length=1, max_length=1024)
    world: dict
    skills: list[dict]
    # Earlier goals of this Agent, oldest first, with what ran and how it ended.
    # Context only: the world snapshot remains the authority on current state.
    history: list[dict] = Field(default_factory=list, max_length=8)


class PlanDecision(Boundary):
    kind: Literal["answer", "clarify", "execute"]
    text: str = Field(default="", max_length=2048)
    plan: Plan | None = None

    @model_validator(mode="after")
    def executable(self):
        if (self.kind == "execute") != (self.plan is not None):
            raise ValueError("plan_required_only_for_execute")
        return self


class Planner(Protocol):
    async def plan(self, request: PlanRequest) -> PlanDecision: ...


def token_counts(usage: dict) -> dict:
    """Provider-reported counts under one set of names; Chat and Responses/Claude differ.
    The service supplies these numbers, so anything but an integer becomes None."""
    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details")
    counts = {
        "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None,
    }
    return {name: value if type(value) is int else None for name, value in counts.items()}


@dataclass(frozen=True)
class PlanTiming:
    """Spans stay out of PlanDecision: a decision is model-supplied content, so the model
    could otherwise report its own cost. Clients need not measure, hence an optional model."""

    total: float
    validate: float  # Local PlanDecision validation, after a complete reply.
    model: ModelTiming | None


class ModelPlanner:
    def __init__(self, client: ModelClient):
        self.client = client
        self.last_timing = None
        # Provider-reported counts, kept apart from the locally measured spans so that a
        # number the model's own service supplied is never mistaken for one we observed.
        self.last_usage = {}

    async def plan(self, request: PlanRequest) -> PlanDecision:
        # Runtime plans one goal at a time; a failed call must not leave the previous spans.
        self.last_timing = None
        self.last_usage = {}
        started = time.perf_counter()
        context = {"world": request.world, "skills": request.skills}
        if request.history:
            context["recent_turns"] = request.history
        reply = await self.client.complete(
            ModelRequest(goal=request.user_goal, context=context)
        )
        if reply.finish != "complete":
            raise ValueError("incomplete_model_reply")
        replied = time.perf_counter()
        decision = PlanDecision.model_validate_json(reply.text)
        done = time.perf_counter()
        self.last_timing = PlanTiming(done - started, done - replied, reply.timing)
        self.last_usage = reply.usage
        return decision
