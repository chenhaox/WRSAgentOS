"""Planner input, decisions and model-backed implementation."""

from typing import Literal, Protocol

from pydantic import Field, model_validator

from wrs_agent.planner.providers import ModelClient, ModelRequest
from wrs_agent.schemas import Boundary, Plan


class PlanRequest(Boundary):
    user_goal: str = Field(min_length=1, max_length=1024)
    world: dict
    skills: list[dict]


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


class ModelPlanner:
    def __init__(self, client: ModelClient):
        self.client = client

    async def plan(self, request: PlanRequest) -> PlanDecision:
        reply = await self.client.complete(
            ModelRequest(
                goal=request.user_goal,
                context={"world": request.world, "skills": request.skills},
            )
        )
        if reply.finish != "complete":
            raise ValueError("incomplete_model_reply")
        return PlanDecision.model_validate_json(reply.text)
