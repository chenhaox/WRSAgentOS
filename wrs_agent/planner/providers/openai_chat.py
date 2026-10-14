"""OpenAI Chat Completions: the format most vendors and local servers accept.

GLM, DeepSeek, Qwen (DashScope compatible mode), Gemini's OpenAI endpoint, vLLM, Ollama and
OpenRouter all read this shape; they differ in extra fields, which LLM_EXTRA_BODY carries.
"""

from wrs_agent.planner.providers import ModelReply, ModelRequest
from wrs_agent.planner.providers.wire import (
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    LLMError,
    answer_text,
    decision_schema,
    json_prompt,
    tool_schema,
    user_content,
    with_extra,
)
from wrs_agent.schemas import decode

PATH = "chat/completions"


def headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


def request_body(request: ModelRequest, config) -> dict:
    json_mode = config.output_mode != "tools"
    schema = decision_schema(request) if config.output_mode == "json_schema" else None
    # Even when absent in a mode, these fields belong to the adapter. Vendor extras may
    # not silently change which response channel is interpreted as a plan.
    if {"tools", "tool_choice", "response_format"} & config.extra_body.keys():
        raise LLMError("llm_extra_body_conflict")
    body = {
        "model": config.model,
        "stream": False,
        # max_completion_tokens is OpenAI-only; OpenAI's own reasoning models belong on
        # openai_responses, and every other Chat Completions server reads max_tokens.
        "max_tokens": config.max_tokens,
        "messages": [
            {"role": "system", "content": json_prompt(schema) if json_mode else SYSTEM_PROMPT},
            {"role": "user", "content": user_content(request)},
        ],
    }
    if json_mode:
        body["response_format"] = (
            {"type": "json_schema", "json_schema": {
                "name": "plan_decision", "strict": True, "schema": schema,
            }} if schema is not None else {"type": "json_object"}
        )
    else:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "parameters": tool_schema(),
                },
            }
        ]
        # There is exactly one offered tool. Qwen does not reliably enforce "required";
        # select that function explicitly. Auto stays available for other servers.
        body["tool_choice"] = (
            {"type": "function", "function": {"name": TOOL_NAME}}
            if config.tool_choice == "required" else "auto"
        )
    if config.reasoning_effort is not None:
        # Omitted when unset: servers that predate the field reject it outright.
        body["reasoning_effort"] = config.reasoning_effort
    return with_extra(body, config.extra_body)


def parse_reply(data: bytes, config=None) -> ModelReply:
    """Decode the native protocol; ModelPlanner validates the proposed decision."""
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
            "protocol": "openai_chat",
            "id": body.get("id"),
            "model": body.get("model"),
            "finish_reason": finish,
            "message": message,
        }
        if message.get("refusal") or finish not in {"stop", "tool_calls"}:
            return ModelReply("", "incomplete", usage, metadata)
        # Some servers report finish_reason "stop" even when they return a tool call.
        calls = message.get("tool_calls") or []
        if config is not None and config.output_mode != "tools":
            if calls:
                raise LLMError("llm_unexpected_tool")
            if finish != "stop" or not isinstance(message.get("content"), str):
                raise ValueError("json_content_required")
            text = message["content"]  # Full PlanDecision validation still happens once.
        elif calls:
            if not isinstance(calls, list) or len(calls) != 1:
                raise ValueError("one_tool_required")
            call = calls[0]
            if call["type"] != "function" or call["function"]["name"] != TOOL_NAME:
                raise LLMError("llm_unexpected_tool")
            arguments = call["function"]["arguments"]
            if not isinstance(arguments, str):
                raise ValueError("json_arguments_required")
            text = arguments  # Parse the proposal once, at the Planner boundary.
        else:
            if finish != "stop":
                raise ValueError("missing_tool")
            if not isinstance(message["content"], str):
                raise ValueError("text_required")
            text = answer_text(message["content"])
        return ModelReply(text, "complete", usage, metadata)
    except LLMError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise LLMError("llm_invalid_reply") from None
