from wrs_agent.planner.api import PlanDecision, PlanRequest
from wrs_agent.planner.providers.api import ModelClient, ModelRequest


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
