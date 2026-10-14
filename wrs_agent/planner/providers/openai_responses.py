"""OpenAI Responses: OpenAI's native format, and the one its reasoning models expect.

Stateless on purpose: store is off and no previous_response_id is sent, so every plan
request carries its own context and no conversation lives on the provider.
"""

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

PATH = "responses"
OUTPUT_TYPES = {"reasoning", "message", "function_call"}


def headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


def request_body(request: ModelRequest, config) -> dict:
    body = {
        "model": config.model,
        "stream": False,
        "store": False,
        "max_output_tokens": config.max_tokens,
        "instructions": SYSTEM_PROMPT,
        "input": user_content(request),
        "tools": [
            {
                "type": "function",
                "name": TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "parameters": tool_schema(),
                # Strict mode rewrites optional fields as required; PlanDecision has defaults.
                "strict": False,
            }
        ],
        "tool_choice": config.tool_choice,
        "parallel_tool_calls": False,
    }
    if config.reasoning_effort is not None:
        body["reasoning"] = {"effort": config.reasoning_effort}
    return with_extra(body, config.extra_body)


def parse_reply(data: bytes, config=None) -> ModelReply:
    try:
        body = decode(data)
        status = body["status"]
        output = body["output"]
        if not isinstance(output, list):
            raise ValueError("output_list_required")
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            raise ValueError("invalid_usage")
        metadata = {
            "protocol": "openai_responses",
            "id": body.get("id"),
            "model": body.get("model"),
            "status": status,
            "output": output,
        }
        # Built-in tools were never offered, so an item such as web_search_call is a fault.
        if any(item["type"] not in OUTPUT_TYPES for item in output):
            raise ValueError("unexpected_output")
        messages = [item for item in output if item["type"] == "message"]
        parts = [part for item in messages for part in item["content"]]
        if status != "completed" or any(part["type"] == "refusal" for part in parts):
            return ModelReply("", "incomplete", usage, metadata)
        calls = [item for item in output if item["type"] == "function_call"]
        if calls:
            if len(calls) != 1 or calls[0]["name"] != TOOL_NAME:
                raise ValueError("one_proposal_required")
            arguments = calls[0]["arguments"]
            if not isinstance(arguments, str):
                raise ValueError("json_arguments_required")
            text = arguments
        else:
            texts = [part["text"] for part in parts if part["type"] == "output_text"]
            if not texts or not all(isinstance(t, str) for t in texts):
                raise ValueError("text_required")
            text = answer_text("".join(texts))
        return ModelReply(text, "complete", usage, metadata)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise LLMError("llm_invalid_reply") from None
