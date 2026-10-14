"""Pieces every wire protocol shares: the prompt, the one proposal tool and the error type.

A protocol module (openai_chat.py, openai_responses.py, anthropic_messages.py) exposes
PATH, headers(key), request_body(request, config) and parse_reply(data). It only formats:
PlanDecision validation stays in ModelPlanner, execution authority stays in Runtime.
"""

import json

from wrs_agent.errors import AgentError
from wrs_agent.planner.providers import ModelRequest
from wrs_agent.schemas import MAX_BYTES, encode

TOOL_NAME = "propose_plan"
TOOL_DESCRIPTION = (
    "Propose an answer, clarification or bounded task plan. "
    "Put skill names in plan.steps[].skill; skills are not callable tools."
)
SYSTEM_PROMPT = (
    "You propose plans for WRS-Agent. You cannot execute actions. "
    "Use only the supplied skills and observed objects; never invent coordinates. "
    "Your ONLY callable tool is propose_plan. The supplied skills are NOT tools you can call. "
    "To use any skill, submit ONE propose_plan call with kind=execute and all steps inside "
    "plan.steps, naming each skill in the step's skill field. Never call a skill directly. "
    "Tool arguments must be one complete valid JSON object with double-quoted keys and strings, "
    "without Markdown, code fences, comments or trailing text. "
    "For questions needing no observation or action, propose an answer or use plain text. "
    "Dependencies must be acyclic, and steps sharing a resource must be ordered. "
    "A step can bind an argument from a preceding step with inputs: "
    "{argument: {step_id: preceding_step_id, field: output_field}}. "
    "Declare that dependency in depends_on and omit the bound argument from args. "
    "Only parameters listed in the skill's result_parameters accept such bindings. "
    "Questions about currently visible objects require a fresh observation; never invent "
    "what the camera sees from a snapshot count. When describe_objects and speak are supplied, "
    "answer such questions by proposing this two-step plan, including for '桌子上有哪些物体？': "
    '{"kind":"execute","text":"","plan":{"steps":['
    '{"step_id":"see","skill":"describe_objects","args":{}},'
    '{"step_id":"say","skill":"speak","args":{},"depends_on":["see"],'
    '"inputs":{"text":{"step_id":"see","field":"text"}}}]}}. '
    "This entire object is the arguments of ONE propose_plan call. "
    # Skills list their resources, and weaker models copy them into steps as new fields.
    "Steps contain only the fields in the propose_plan schema; skills already declare "
    "their resources, so never add resources, notes or other fields to a step. "
    "Include verification after manipulation, depending on the step it verifies. "
    # Runtime discards prose on executable plans; generating it still costs time.
    "Leave text empty when the plan is executable; the steps are the answer. "
    "Unknown or ambiguous goals require clarification. "
    # Replies are displayed and may be spoken; every output token delays the reply.
    "Answers and clarifications use the user's language in coherent sentences; "
    "a clarification asks only the one question that resolves the goal. "
    "Never supply execution IDs, permissions, boot IDs or control epochs."
)


class LLMError(AgentError):
    """Safe provider code survives Runtime/RPC without HTTP bodies or credentials."""

    def __init__(self, code):
        super().__init__(code, stage="planning")

    def __str__(self):
        return self.code


def tool_schema() -> dict:
    """PlanDecision's schema with every $ref inlined and a concrete type on each argument.

    Some servers (Qwen's among them) rebuild each tool argument from the "type" on its own
    property and return an untyped one ($ref, anyOf) as a raw JSON string.
    """
    from wrs_agent.planner import PlanDecision

    schema = PlanDecision.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, list):
            return [inline(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return inline(defs[node["$ref"].rsplit("/", 1)[-1]])
        return {key: inline(value) for key, value in node.items()}

    schema = inline(schema)
    # plan is optional, so omitting it already means none; allowing null would need anyOf.
    schema["properties"]["plan"] = schema["properties"]["plan"]["anyOf"][0]
    return schema


def encode_for_model(value: dict) -> str:
    """Serialize for a tokenizer, not for decode(). The wire codec escapes non-ASCII, which
    a peer decodes back but a model reads as literal backslash-u text at twice the tokens."""
    result = json.dumps(value, allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    if len(result.encode()) > MAX_BYTES:
        raise ValueError("payload_too_large")
    return result


def user_content(request: ModelRequest) -> str:
    return encode_for_model({"goal": request.goal, "context": request.context})


def answer_text(text: str) -> str:
    """Prose is always an answer: text that resembles a plan never becomes executable."""
    return encode({"kind": "answer", "text": text, "plan": None}).decode()


def with_extra(body: dict, extra: dict) -> dict:
    """Vendor fields may add to a request, never replace what the adapter decided."""
    if extra.keys() & body.keys():
        raise LLMError("llm_extra_body_conflict")
    return body | extra
