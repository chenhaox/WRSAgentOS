"""Model request/reply contract; vendor adapters live beside it."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ModelRequest:
    goal: str
    context: dict


@dataclass(frozen=True)
class ModelReply:
    text: str
    finish: str
    usage: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


class ModelClient(Protocol):
    async def complete(self, request: ModelRequest) -> ModelReply: ...
    async def aclose(self) -> None: ...
