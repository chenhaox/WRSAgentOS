"""GLM Chat Completions adapter. Proposes plans; never executes provider tools."""

import asyncio
import os
from typing import Literal

import httpx
from pydantic import Field

from wrs_agent.planner.api import PlanDecision
from wrs_agent.planner.providers.api import ModelReply, ModelRequest
from wrs_agent.schemas import MAX_BYTES, Boundary, decode, encode

CODING_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
STANDARD_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


class GLMConfig(Boundary):
    model: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    base_url: Literal[
        "https://open.bigmodel.cn/api/coding/paas/v4",
        "https://open.bigmodel.cn/api/paas/v4",
    ] = CODING_BASE_URL
    protocol: Literal["chat_completions"] = "chat_completions"
    tool_calling: Literal[True] = True
    stream: Literal[False] = False
    timeout_s: float = Field(default=20.0, gt=0, le=120)
    max_tokens: int = Field(default=2048, ge=128, le=8192)

    @classmethod
    def from_env(cls):
        # Empty model is a configuration error, never an account capability guess.
        return cls(
            model=os.environ.get("GLM_MODEL", ""),
            base_url=os.environ.get("GLM_BASE_URL", CODING_BASE_URL).rstrip("/"),
        )


class GLMError(ValueError):
    """Safe public error code; excludes HTTP bodies, headers and credentials."""


def request_body(request: ModelRequest, config: GLMConfig) -> dict:
    return {
        "model": config.model,
        "stream": config.stream,
        "max_tokens": config.max_tokens,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You propose plans for WRS-Agent. You cannot execute actions. "
                    "Use only the supplied skills and observed objects; never invent coordinates. "
                    "Submit at most one propose_plan call, or answer a question as plain text. "
                    "Dependencies must be acyclic. Include verification after manipulation. "
                    "Unknown or ambiguous goals require clarification. "
                    "Never supply execution IDs, permissions, boot IDs or control epochs."
                ),
            },
            {
                "role": "user",
                "content": encode({"goal": request.goal, "context": request.context}).decode(),
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "propose_plan",
                    "description": "Propose an answer, clarification or bounded task plan.",
                    "parameters": PlanDecision.model_json_schema(),
                },
            }
        ],
        "tool_choice": "auto",
    }


def parse_reply(data: bytes) -> ModelReply:
    """Keep native message/usage in memory; expose one validated internal decision."""
    try:
        body = decode(data)
        choices = body["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("one_choice_required")
        choice = choices[0]
        message = choice["message"]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("assistant_message_required")
        finish = choice["finish_reason"]
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            raise ValueError("invalid_usage")
        metadata = {
            "provider": "glm",
            "protocol": "chat_completions",
            "id": body.get("id"),
            "model": body.get("model"),
            "finish_reason": finish,
            "message": message,
        }
        if message.get("refusal") or finish not in {"stop", "tool_calls"}:
            return ModelReply("", "incomplete", usage, metadata)
        calls = message.get("tool_calls") or []
        if calls:
            if not isinstance(calls, list) or len(calls) != 1:
                raise ValueError("one_tool_required")
            call = calls[0]
            if call["type"] != "function" or call["function"]["name"] != "propose_plan":
                raise ValueError("unexpected_tool")
            arguments = call["function"]["arguments"]
            if not isinstance(arguments, str):
                raise ValueError("json_arguments_required")
            decision = PlanDecision.model_validate(decode(arguments.encode()))
        else:
            if finish != "stop":
                raise ValueError("missing_tool")
            # Text that resembles JSON is still an answer, never executable.
            decision = PlanDecision(kind="answer", text=message["content"])
        return ModelReply(decision.model_dump_json(), "complete", usage, metadata)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise GLMError("glm_invalid_reply") from None


class GLMClient:
    def __init__(
        self,
        config: GLMConfig,
        *,
        live_model: bool = False,
        transport: httpx.MockTransport | None = None,
    ):
        # Only an explicit offline HTTP fixture bypasses the live opt-in.
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise GLMError("glm_offline_transport_required")
        if transport is None and not live_model:
            raise GLMError("glm_live_model_opt_in_required")
        key = "offline-fixture" if transport is not None else os.environ.get("GLM_API_KEY", "")
        if not key or len(key) > 512 or not key.isascii() or any(c.isspace() for c in key):
            raise GLMError("glm_api_key_missing_or_invalid")
        self.config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url + "/",
            headers={"Authorization": f"Bearer {key}", "User-Agent": "WRS-Agent/0.1.0"},
            timeout=config.timeout_s,
            follow_redirects=False,
            trust_env=False,
            # A single connection retry; never retry received errors or partial responses.
            transport=transport or httpx.AsyncHTTPTransport(retries=1),
        )

    async def complete(self, request: ModelRequest) -> ModelReply:
        body = request_body(request, self.config)
        try:
            async with asyncio.timeout(self.config.timeout_s):
                async with self._http.stream("POST", "chat/completions", json=body) as response:
                    if response.status_code != 200:
                        raise GLMError(f"glm_http_{response.status_code}")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > MAX_BYTES:
                            raise GLMError("glm_reply_too_large")
                        data.extend(chunk)
            return parse_reply(bytes(data))
        except (httpx.TimeoutException, TimeoutError):
            raise GLMError("glm_timeout") from None
        except httpx.HTTPError:
            raise GLMError("glm_transport_error") from None

    async def aclose(self) -> None:
        await self._http.aclose()
