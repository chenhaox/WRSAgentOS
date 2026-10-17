"""Agent node: own the model and Runtime, bind their existing RPC contract."""

from typing import Literal

from pydantic import Field, ValidationError

from wrs_agent.errors import AgentError
from wrs_agent.nodes.agent.rpc import register_runtime
from wrs_agent.nodes.node import Node
from wrs_agent.planner import ModelPlanner
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import Boundary, Empty, GoalState


class AgentOptions(Boundary):
    live_model: bool = False


class PlannerSettings(Boundary):
    """Runtime model choice; omitted fields fall back to this process's LLM_* variables."""

    enabled: bool
    protocol: Literal["openai_chat", "openai_responses", "anthropic_messages"] | None = None
    base_url: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)
    # Never stored or returned; an empty value uses this process's LLM_API_KEY.
    api_key: str | None = Field(default=None, max_length=512)
    proxy: str | None = Field(default=None, max_length=512)


class AgentNode(Node):
    node_type = "agent"
    launch_options = {"live_model": "live_model"}
    options_type = AgentOptions
    features = ("task.coordinate", "planner.configure")

    async def setup(self):
        self.model, self.key_source = None, "none"
        planner = None
        if self.options.live_model:
            from wrs_agent.planner.providers.llm import LLMClient, LLMConfig

            self.model = LLMClient(LLMConfig.from_env(), live_model=True)
            self.key_source = "env"
            planner = ModelPlanner(self.model)
        self.on_close(self._close_model)

        registry = self.discover()
        registry.local[self.node_id] = self.info
        self.runtime = Runtime(
            registry.clients,
            self.skill_bindings,
            planner=planner,
            registry=registry,
        )
        self.on_close(self.runtime.close)
        register_runtime(self.transport, self.runtime)
        self.transport.register_handler("request/agent/planner", self.planner_status)
        self.transport.register_handler("request/agent/planner/configure", self.configure)

    async def _close_model(self):
        if self.model is not None:
            await self.model.aclose()

    def _status(self):
        config = self.model.config if self.model else None
        defaults = {}
        if config is None:
            from wrs_agent.planner.providers.llm import LLMConfig, LLMError

            try:
                config = LLMConfig.from_env()
            except LLMError:
                config = None
        if config is not None:
            defaults = {
                "protocol": config.protocol, "base_url": config.base_url,
                "model": config.model, "proxy": config.proxy,
            }
        return {"enabled": self.model is not None, "key_source": self.key_source, **defaults}

    async def planner_status(self, payload):
        Empty.model_validate(payload)
        return self._status()

    async def configure(self, payload):
        from wrs_agent.planner.providers.llm import LLMClient, LLMConfig, LLMError

        settings = PlannerSettings.model_validate(payload)
        # The planning worker outlives the model call while its task runs; only WAITING
        # still reads runtime.planner.
        if self.runtime.planning.state == GoalState.WAITING:
            raise AgentError("planner_unavailable_or_busy")
        model = None
        if settings.enabled:
            try:
                base = LLMConfig.from_env().model_dump()
            except LLMError:
                base = {}
            chosen = {
                name: value
                for name in ("protocol", "base_url", "model", "proxy")
                if (value := getattr(settings, name))
            }
            try:
                config = LLMConfig(**{**base, **chosen})
            except ValidationError as exc:
                error = exc.errors(include_input=False)[0]
                suffix = "missing" if error["type"] == "missing" else "invalid"
                raise LLMError(f"llm_{error['loc'][0]}_{suffix}") from None
            model = LLMClient(config, live_model=True, api_key=settings.api_key or None)
        previous, self.model = self.model, model
        self.key_source = "none" if model is None else "ui" if settings.api_key else "env"
        self.runtime.planner = None if model is None else ModelPlanner(model)
        if previous is not None:
            await previous.aclose()
        return self._status()
