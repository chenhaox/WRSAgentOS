"""Local skill definitions; the wire contract is derived from the argument model."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from wrs_agent.schemas import Boundary, SkillSpec


@dataclass(frozen=True, kw_only=True)
class Skill:
    """Describe a skill and optionally supply its implementation in the same declaration."""

    name: str
    arguments: type[Boundary]
    description: str
    resources: tuple[str, ...]
    verification: str
    handler: Callable[..., bool | Boundary | Awaitable[bool | Boundary]] | None = None
    output: type[Boundary] | None = None
    result_parameters: tuple[str, ...] = ()
    version: int = 1
    instructions: str = ""
    preconditions: tuple[str, ...] = ()
    timeout: float = 10.0
    recovery: tuple[str, ...] = ()
    _spec: SkillSpec = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if not isinstance(self.arguments, type) or not issubclass(self.arguments, Boundary):
            raise ValueError("invalid_skill_arguments")
        if self.handler is not None and not callable(self.handler):
            raise ValueError("invalid_skill_handler")
        if self.output is not None and (
            not isinstance(self.output, type) or not issubclass(self.output, Boundary)
        ):
            raise ValueError("invalid_skill_output")
        if not set(self.result_parameters).issubset(self.arguments.model_fields):
            raise ValueError("invalid_result_parameters")
        if any(
            isinstance(getattr(self, name), (str, bytes))
            for name in ("resources", "preconditions", "recovery", "result_parameters")
        ):
            raise ValueError("invalid_skill_metadata")
        spec = SkillSpec(
            name=self.name, version=self.version, description=self.description,
            instructions=self.instructions, parameters=self.arguments.model_json_schema(),
            output=self.output.model_json_schema() if self.output else None,
            result_parameters=list(self.result_parameters),
            resources=list(self.resources), preconditions=list(self.preconditions),
            verification=self.verification, timeout=self.timeout, recovery=list(self.recovery),
        )
        for name in ("resources", "preconditions", "recovery", "result_parameters"):
            object.__setattr__(self, name, tuple(getattr(spec, name)))
        object.__setattr__(self, "_spec", spec)

    @property
    def spec(self):
        """A detached wire description; callers cannot mutate the executable contract."""
        return self._spec.model_copy(deep=True)

    def bind(self, handler):
        """Reuse one contract with a backend-specific implementation."""
        if not callable(handler):
            raise ValueError("invalid_skill_handler")
        return replace(self, handler=handler)
