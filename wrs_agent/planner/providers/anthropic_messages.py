"""Anthropic Messages: Claude's native format, also served by some vendors' Claude endpoints.

The base URL is the host root (https://api.anthropic.com), as ANTHROPIC_BASE_URL is for the
official SDK; the adapter appends v1/messages.
"""

import json

from wrs_agent.planner.providers import ModelReply, ModelRequest
from wrs_agent.planner.providers.wire import (
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    LLMError,
    answer_text,
    tool_schema,
    user_content,
    with_extra,
)
from wrs_agent.schemas import decode

PATH = "v1/messages"
API_VERSION = "2023-06-01"
CONTENT_TYPES = {"text", "tool_use", "thinking", "redacted_thinking"}


def headers(key: str) -> dict:
    result = {"anthropic-version": API_VERSION}
    if key:
        result["x-api-key"] = key
    return result


def request_body(request: ModelRequest, config) -> dict:
    body = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content(request)}],
        "tools": [
            {"name": TOOL_NAME, "description": TOOL_DESCRIPTION, "input_schema": tool_schema()}
        ],
        # Forcing a tool ("any") is incompatible with thinking; auto also lets the model answer.
        "tool_choice": {
            "type": "any" if config.tool_choice == "required" else "auto",
            "disable_parallel_tool_use": True,
        },
    }
    effort = config.reasoning_effort
    if effort == "none":
        # Claude has no "none" effort level; the native way to skip thinking is this switch.
        body["thinking"] = {"type": "disabled"}
    elif effort is not None:
        body["output_config"] = {"effort": effort}
    return with_extra(body, config.extra_body)


def parse_reply(data: bytes, config=None) -> ModelReply:
    try:
        body = decode(data)
        if body.get("type") != "message" or body.get("role") != "assistant":
            raise ValueError("assistant_message_required")
        content = body["content"]
        if not isinstance(content, list):
            raise ValueError("content_list_required")
        stop = body["stop_reason"]
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            raise ValueError("invalid_usage")
        metadata = {
            "protocol": "anthropic_messages",
            "id": body.get("id"),
            "model": body.get("model"),
            "stop_reason": stop,
            "content": content,
        }
        # Server tools were never offered, so a block such as server_tool_use is a fault.
        if any(block["type"] not in CONTENT_TYPES for block in content):
            raise ValueError("unexpected_content")
        # max_tokens, refusal and pause_turn all leave the proposal unfinished.
        if stop not in {"end_turn", "tool_use"}:
            return ModelReply("", "incomplete", usage, metadata)
        calls = [block for block in content if block["type"] == "tool_use"]
        if calls:
            if len(calls) != 1 or calls[0]["name"] != TOOL_NAME:
                raise ValueError("one_proposal_required")
            arguments = calls[0]["input"]
            if not isinstance(arguments, dict):
                raise ValueError("object_arguments_required")
            text = json.dumps(arguments, allow_nan=False, ensure_ascii=False)
        else:
            if stop != "end_turn":
                raise ValueError("missing_tool")
            texts = [block["text"] for block in content if block["type"] == "text"]
            if not texts or not all(isinstance(t, str) for t in texts):
                raise ValueError("text_required")
            text = answer_text("".join(texts))
        return ModelReply(text, "complete", usage, metadata)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise LLMError("llm_invalid_reply") from None
