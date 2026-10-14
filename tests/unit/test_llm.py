"""Offline protocol fixtures only. No external HTTP, account or hardware required."""

import asyncio
import copy
import json
import socket
import ssl
import typing
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from wrs_agent.planner import ModelPlanner, PlanRequest, token_counts
from wrs_agent.planner.providers import ModelRequest
from wrs_agent.planner.providers.llm import PROTOCOLS, LLMClient, LLMConfig, LLMError
from wrs_agent.planner.providers.wire import decision_schema, tool_schema
from wrs_agent.schemas import MAX_BYTES

FIXTURES = Path(__file__).parents[1] / "fixtures" / "models"
FIXTURE_FILES = {
    "openai_chat": "openai_chat_tool_call.json",
    "openai_responses": "openai_responses_function_call.json",
    "anthropic_messages": "anthropic_tool_use.json",
}
BASE_URL = "https://model.invalid/v1"
REQUEST = ModelRequest("put A in B", {"world": {}, "skills": []})
PLAN_REQUEST = PlanRequest(user_goal="put A in B", world={}, skills=[])
ENV_NAMES = [
    "LLM_" + name
    for name in (
        "PROTOCOL", "BASE_URL", "MODEL", "API_KEY", "REASONING_EFFORT", "TOOL_CHOICE",
        "OUTPUT_MODE", "MAX_TOKENS", "TIMEOUT_S", "EXTRA_BODY", "EXTRA_HEADERS", "PROXY",
    )
]


@pytest.fixture(autouse=True)
def clean_model_environment(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def config(protocol="openai_chat", **kwargs):
    return LLMConfig(**({"protocol": protocol, "model": "fixture", "base_url": BASE_URL} | kwargs))


def reply_body(protocol="openai_chat"):
    return json.loads((FIXTURES / FIXTURE_FILES[protocol]).read_text(encoding="utf-8"))


def offline(protocol="openai_chat", respond=None, **kwargs):
    respond = respond or (lambda _: httpx.Response(200, json=reply_body(protocol)))
    return LLMClient(config(protocol, **kwargs), transport=httpx.MockTransport(respond))


def body_for(protocol, **kwargs):
    return PROTOCOLS[protocol].request_body(REQUEST, config(protocol, **kwargs))


def parse(protocol, body):
    return PROTOCOLS[protocol].parse_reply(json.dumps(body).encode())


def test_every_protocol_module_is_selectable():
    field = LLMConfig.model_fields["protocol"].annotation
    assert set(typing.get_args(field)) == set(PROTOCOLS) == set(FIXTURE_FILES)
    for module in PROTOCOLS.values():
        assert {"PATH", "headers", "request_body", "parse_reply"} <= set(vars(module))


# --- One native round trip per protocol ------------------------------------------------


async def test_chat_roundtrip_and_reused_client():
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == BASE_URL + "/chat/completions"
        assert request.headers["Authorization"] == "Bearer offline-fixture"
        assert request.headers["User-Agent"] == "WRS-Agent/0.1.0"
        body = json.loads(request.content)
        assert body["model"] == "account-model" and body["max_tokens"] == 2048
        assert body["stream"] is False and body["tool_choice"] == "auto"
        assert body["messages"][0]["role"] == "system"
        assert [t["function"]["name"] for t in body["tools"]] == ["propose_plan"]
        return httpx.Response(200, json=reply_body())

    client = offline(respond=respond, model="account-model")
    try:
        reply = await client.complete(REQUEST)
        assert reply.finish == "complete" and reply.usage["total_tokens"] == 100
        arguments = reply_body()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        assert reply.text == arguments  # Native JSON is not parsed and re-serialized here.
        assert reply.metadata["protocol"] == "openai_chat"
        assert reply.metadata["message"]["tool_calls"][0]["id"] == "proposal-1"
        decision = await ModelPlanner(client).plan(PLAN_REQUEST)
        assert decision.kind == "execute" and decision.plan.steps[-1].skill == "verify"
        assert len(requests) == 2
    finally:
        await client.aclose()
    assert client._http.is_closed


async def test_responses_roundtrip_is_stateless_and_single_call():
    def respond(request):
        assert str(request.url) == BASE_URL + "/responses"
        assert request.headers["Authorization"] == "Bearer offline-fixture"
        body = json.loads(request.content)
        assert body["store"] is False and body["stream"] is False
        assert body["parallel_tool_calls"] is False and body["tool_choice"] == "auto"
        assert body["max_output_tokens"] == 2048 and "max_tokens" not in body
        assert "previous_response_id" not in body
        assert body["instructions"] and json.loads(body["input"])["goal"] == REQUEST.goal
        assert body["tools"][0]["type"] == "function"
        assert body["tools"][0]["name"] == "propose_plan"
        return httpx.Response(200, json=reply_body("openai_responses"))

    client = offline("openai_responses", respond=respond)
    planner = ModelPlanner(client)
    try:
        decision = await planner.plan(PLAN_REQUEST)
        assert decision.kind == "execute" and decision.plan.steps[-1].skill == "verify"
        assert planner.last_usage["output_tokens_details"]["reasoning_tokens"] == 20
    finally:
        await client.aclose()


async def test_anthropic_roundtrip_uses_native_headers_and_tool_use():
    def respond(request):
        assert str(request.url) == BASE_URL + "/v1/messages"
        assert request.headers["x-api-key"] == "offline-fixture"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["system"] and body["max_tokens"] == 2048
        assert [m["role"] for m in body["messages"]] == ["user"]
        assert body["tools"][0]["name"] == "propose_plan"
        assert body["tools"][0]["input_schema"]["type"] == "object"
        assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
        return httpx.Response(200, json=reply_body("anthropic_messages"))

    client = offline("anthropic_messages", respond=respond)
    try:
        decision = await ModelPlanner(client).plan(PLAN_REQUEST)
        assert decision.kind == "execute" and decision.plan.steps[-1].skill == "verify"
    finally:
        await client.aclose()


def test_tool_schema_types_every_argument_without_references():
    """Servers that rebuild each argument from its declared type keep an untyped one as text."""
    schema = tool_schema()
    assert "$defs" not in schema and "$ref" not in json.dumps(schema)
    types = {name: prop["type"] for name, prop in schema["properties"].items()}
    assert types == {"kind": "string", "text": "string", "plan": "object"}
    assert schema["required"] == ["kind"]
    step = schema["properties"]["plan"]["properties"]["steps"]["items"]
    assert step["type"] == "object" and step["required"] == ["step_id", "skill"]


# --- Reasoning effort, vendor fields and headers ------------------------------------------


REASONING_FIELDS = {"reasoning_effort", "reasoning", "output_config", "thinking"}


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_unset_effort_sends_no_reasoning_field(protocol):
    """Older servers reject the fields outright, so an unset config sends none of them."""
    assert not REASONING_FIELDS & body_for(protocol).keys()


@pytest.mark.parametrize(
    "protocol,effort,expected",
    [
        ("openai_chat", "high", {"reasoning_effort": "high"}),
        ("openai_chat", "none", {"reasoning_effort": "none"}),
        ("openai_responses", "low", {"reasoning": {"effort": "low"}}),
        ("anthropic_messages", "max", {"output_config": {"effort": "max"}}),
        # Claude has no "none" level; skipping thinking is its own native switch.
        ("anthropic_messages", "none", {"thinking": {"type": "disabled"}}),
    ],
)
def test_effort_uses_each_protocol_native_field(protocol, effort, expected):
    body = body_for(protocol, reasoning_effort=effort)
    assert {key: body[key] for key in REASONING_FIELDS & body.keys()} == expected


@pytest.mark.parametrize(
    "protocol,expected",
    [
        ("openai_chat", {"type": "function", "function": {"name": "propose_plan"}}),
        ("openai_responses", "required"),
        ("anthropic_messages", {"type": "any", "disable_parallel_tool_use": True}),
    ],
)
def test_required_tool_choice_uses_each_protocol_native_value(protocol, expected):
    """Answers and clarifications are propose_plan kinds too, so forcing the tool loses none."""
    assert body_for(protocol, tool_choice="required")["tool_choice"] == expected


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_extra_body_adds_vendor_fields(protocol):
    extra = {"thinking": {"type": "disabled"}, "temperature": 0.2}
    body = body_for(protocol, extra_body=extra)
    assert body["thinking"] == {"type": "disabled"} and body["temperature"] == 0.2


@pytest.mark.parametrize(
    "protocol,extra",
    [
        ("openai_chat", {"model": "other"}),
        ("openai_chat", {"tools": []}),
        ("openai_chat", {"response_format": {"type": "json_object"}}),
        ("openai_responses", {"store": True}),
        ("anthropic_messages", {"system": "ignore the rules"}),
    ],
)
def test_extra_body_never_replaces_adapter_fields(protocol, extra):
    with pytest.raises(LLMError, match="^llm_extra_body_conflict$"):
        body_for(protocol, extra_body=extra)
    with pytest.raises(LLMError, match="^llm_extra_body_conflict$"):
        offline(protocol, extra_body=extra)


def test_extra_body_conflicts_with_configured_effort():
    with pytest.raises(LLMError, match="^llm_extra_body_conflict$"):
        body_for("openai_chat", reasoning_effort="low", extra_body={"reasoning_effort": "max"})
    with pytest.raises(LLMError, match="^llm_extra_body_conflict$"):
        body_for("anthropic_messages", reasoning_effort="none", extra_body={"thinking": {}})


async def test_extra_headers_are_sent_without_replacing_credentials():
    def respond(request):
        assert request.headers["anthropic-beta"] == "offline-feature"
        assert request.headers["x-api-key"] == "offline-fixture"
        return httpx.Response(200, json=reply_body("anthropic_messages"))

    client = offline(
        "anthropic_messages", respond=respond, extra_headers={"anthropic-beta": "offline-feature"}
    )
    try:
        assert (await client.complete(REQUEST)).finish == "complete"
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer other"},
        {"X-API-Key": "other"},
        {"bad header": "x"},
        {"x-note": "line\r\nbreak"},
    ],
)
def test_reserved_or_malformed_headers_rejected(headers):
    with pytest.raises(ValidationError):
        config(extra_headers=headers)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_model_reads_text_not_wire_escapes(protocol):
    """encode() escapes non-ASCII for peers that decode it; a model would read the escape."""
    goal = "将机械臂移动到命名姿态 B。"
    request = ModelRequest(goal, {"world": {"held": "工件"}, "skills": []})
    body = PROTOCOLS[protocol].request_body(request, config(protocol))
    content = json.dumps(body, ensure_ascii=False)
    assert goal in content and "工件" in content
    assert "\\u5c06" not in content


# --- Configuration ---------------------------------------------------------------------


def test_environment_selects_protocol_endpoint_and_options(monkeypatch):
    values = {
        "LLM_PROTOCOL": " Anthropic_Messages ",
        "LLM_BASE_URL": "https://api.anthropic.com/",
        "LLM_MODEL": "account-model",
        "LLM_REASONING_EFFORT": "HIGH",
        "LLM_TOOL_CHOICE": "Required",
        "LLM_MAX_TOKENS": "8192",
        "LLM_TIMEOUT_S": "45",
        "LLM_EXTRA_BODY": '{"temperature": 0}',
        "LLM_EXTRA_HEADERS": '{"anthropic-beta": "feature"}',
        "LLM_PROXY": "http://127.0.0.1:7890",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    loaded = LLMConfig.from_env()
    assert loaded.protocol == "anthropic_messages"
    assert loaded.base_url == "https://api.anthropic.com"
    assert (loaded.model, loaded.reasoning_effort) == ("account-model", "high")
    assert loaded.tool_choice == "required"
    assert (loaded.max_tokens, loaded.timeout_s) == (8192, 45.0)
    assert loaded.extra_body == {"temperature": 0}
    assert loaded.extra_headers == {"anthropic-beta": "feature"}
    assert loaded.proxy == "http://127.0.0.1:7890"


def test_environment_defaults_to_chat_and_provider_defaults(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("LLM_MODEL", "fixture")
    loaded = LLMConfig.from_env()
    assert loaded.protocol == "openai_chat" and loaded.reasoning_effort is None
    assert loaded.tool_choice == "auto"
    assert loaded.output_mode == "tools"
    assert loaded.extra_body == {} and loaded.proxy is None


@pytest.mark.parametrize("missing", ["LLM_MODEL", "LLM_BASE_URL"])
def test_model_and_endpoint_are_both_required(monkeypatch, missing):
    monkeypatch.setenv("LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("LLM_MODEL", "fixture")
    monkeypatch.delenv(missing)
    with pytest.raises(LLMError, match=f"^{missing.lower()}_missing$") as error:
        LLMConfig.from_env()
    assert missing in error.value.error.message


@pytest.mark.parametrize(
    "name,value",
    [
        ("LLM_PROTOCOL", "claude"),
        ("LLM_REASONING_EFFORT", "off"),  # The superseded GLM_THINKING value fails loudly.
        ("LLM_REASONING_EFFORT", "true"),
        ("LLM_TOOL_CHOICE", "none"),  # Would forbid the only way to propose a plan.
        ("LLM_OUTPUT_MODE", "guess"),
        ("LLM_MAX_TOKENS", "many"),
        ("LLM_MAX_TOKENS", "1"),
        ("LLM_TIMEOUT_S", "inf"),
        ("LLM_EXTRA_BODY", "[1, 2]"),
        ("LLM_EXTRA_BODY", "{not json"),
        ("LLM_EXTRA_HEADERS", '{"Authorization": "private"}'),
        ("LLM_PROXY", "socks5://127.0.0.1:1080"),
        ("LLM_BASE_URL", "http://model.invalid/v1"),
    ],
)
def test_invalid_environment_is_named_and_safe(monkeypatch, name, value):
    monkeypatch.setenv("LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("LLM_MODEL", "fixture")
    monkeypatch.setenv(name, value)
    field = name.removeprefix("LLM_").lower()
    with pytest.raises(LLMError, match=f"^llm_{field}_invalid$") as error:
        LLMConfig.from_env()
    assert name in error.value.error.message
    assert "private" not in error.value.error.model_dump_json()


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://open.bigmodel.cn/api/coding/paas/v4",
        "https://api.anthropic.com",
        "http://127.0.0.1:11434/v1",
        "http://localhost:8000/v1",
    ],
)
def test_https_or_loopback_endpoints_accepted(url):
    assert config(base_url=url).base_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://model.invalid/v1",  # Plaintext would expose the key off this machine.
        "https://user:secret@model.invalid/v1",
        "https://model.invalid/v1?key=secret",
        "https://model.invalid/v1#fragment",
        "https://model.invalid:notaport/v1",
        "ftp://model.invalid/v1",
        "https://model invalid/v1",
        "",
    ],
)
def test_unsafe_endpoints_rejected(url):
    with pytest.raises(ValidationError):
        config(base_url=url)


@pytest.mark.parametrize("model", ["glm-5.2", "qwen3:8b", "Qwen/Qwen3-8B", "org/model@v1"])
def test_vendor_model_ids_accepted(model):
    assert config(model=model).model == model


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stream": True},
        {"thinking": {"type": "enabled"}},
        {"protocol": "anthropic"},
        {"timeout_s": float("inf")},
        {"timeout_s": 301},
        {"max_tokens": 100},
        {"model": ""},
        {"model": "-leading-dash"},
        {"reasoning_effort": "adaptive"},
        {"extra_body": {"blob": "x" * 5000}},
    ],
)
def test_unsupported_configuration_rejected(kwargs):
    with pytest.raises(ValidationError):
        config(**kwargs)


def test_live_opt_in_and_key_requirements(monkeypatch):
    with pytest.raises(LLMError, match="^llm_live_model_opt_in_required$"):
        LLMClient(config())
    with pytest.raises(LLMError, match="^llm_api_key_missing_or_invalid$") as error:
        LLMClient(config(), live_model=True)
    assert "LLM_API_KEY" in error.value.error.message
    for invalid in ("with space", "tab\tkey", "密钥", "x" * 513):
        monkeypatch.setenv("LLM_API_KEY", invalid)
        with pytest.raises(LLMError, match="^llm_api_key_missing_or_invalid$"):
            LLMClient(config(), live_model=True)
    with pytest.raises(LLMError, match="^llm_offline_transport_required$"):
        LLMClient(config(), transport=httpx.AsyncHTTPTransport())


async def test_loopback_server_may_run_without_key():
    """vLLM and Ollama run keyless on this machine; no empty credential header is sent."""
    for protocol in PROTOCOLS:
        client = LLMClient(config(protocol, base_url="http://127.0.0.1:8000/v1"), live_model=True)
        try:
            assert "authorization" not in client._http.headers
            assert "x-api-key" not in client._http.headers
        finally:
            await client.aclose()


# --- Timing ----------------------------------------------------------------------------


async def test_timing_splits_one_call_into_spans():
    """Spans are observation, so assert structure and ordering, never wall-clock values."""
    client = offline()
    planner = ModelPlanner(client)
    try:
        await planner.plan(PLAN_REQUEST)
        timing, model = planner.last_timing, planner.last_timing.model
        assert min(model.total, model.wait, model.receive, model.parse) >= 0
        assert model.wait + model.receive + model.parse == pytest.approx(model.total)
        assert timing.total >= model.total and timing.validate >= 0
        # Counts come from the reply, so they stay out of the locally measured spans.
        assert planner.last_usage == reply_body()["usage"]
        assert not hasattr(timing, "usage")
    finally:
        await client.aclose()


async def test_failed_planning_keeps_no_stale_spans():
    def fail(request):
        raise httpx.ConnectError("private", request=request)

    client = offline(respond=fail)
    planner = ModelPlanner(client)
    try:
        with pytest.raises(LLMError):
            await planner.plan(PlanRequest(user_goal="goal", world={}, skills=[]))
        assert planner.last_timing is None and planner.last_usage == {}
    finally:
        await client.aclose()


def test_token_counts_share_names_across_protocols():
    chat = {
        "prompt_tokens": 2497,
        "completion_tokens": 158,
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    counts = token_counts(chat)
    assert counts == {"input_tokens": 2497, "output_tokens": 158, "reasoning_tokens": 0}
    responses = {
        "input_tokens": 30,
        "output_tokens": 70,
        "output_tokens_details": {"reasoning_tokens": 20},
    }
    counts = token_counts(responses)
    assert counts == {"input_tokens": 30, "output_tokens": 70, "reasoning_tokens": 20}
    # Counts the service does not report, or reports as non-integers, are not displayed.
    empty = {"input_tokens": None, "output_tokens": None, "reasoning_tokens": None}
    assert token_counts({}) == empty
    garbled = {"prompt_tokens": "9", "completion_tokens": True, "completion_tokens_details": []}
    assert token_counts(garbled) == empty


# --- Replies that must never become an executable decision ------------------------------


def chat_text(content):
    body = reply_body()
    message = {"role": "assistant", "content": content}
    body["choices"][0] = {"finish_reason": "stop", "message": message}
    return body


def responses_text(content):
    body = reply_body("openai_responses")
    parts = [{"type": "output_text", "text": content}]
    body["output"] = [{"type": "message", "role": "assistant", "content": parts}]
    return body


@pytest.mark.parametrize("mode,kind", [
    ("tools", "answer"), ("json_object", "execute"), ("json_schema", "execute"),
])
async def test_chat_content_is_a_plan_only_in_explicit_json_mode(mode, kind):
    proposal = reply_body()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if mode != "tools":
            assert body["response_format"]["type"] == mode
            if mode == "json_schema":
                assert body["response_format"]["json_schema"] == {
                    "name": "plan_decision", "strict": True, "schema": decision_schema(REQUEST),
                }
            assert not {"tools", "tool_choice"} & body.keys()
            assert "propose_plan" not in body["messages"][0]["content"]
            assert body["enable_thinking"] is False
        return httpx.Response(200, json=chat_text(proposal))

    client = offline(respond=respond, output_mode=mode, extra_body={"enable_thinking": False})
    try:
        decision = await ModelPlanner(client).plan(PLAN_REQUEST)
        assert decision.kind == kind
        assert (decision.plan is not None) == (mode != "tools")
        assert len(requests) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["answer", "clarify"])
@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
async def test_json_mode_preserves_non_executable_decisions(kind, mode):
    body = chat_text(json.dumps({"kind": kind, "text": "请说明目标。"}))
    client = offline(output_mode=mode, respond=lambda _: httpx.Response(200, json=body))
    try:
        decision = await ModelPlanner(client).plan(PLAN_REQUEST)
        assert decision.kind == kind and decision.plan is None
        assert decision.text == "请说明目标。"
    finally:
        await client.aclose()


@pytest.mark.parametrize("fault", [
    "length", "refusal", "tool_call", "missing_content", "fence", "trailing", "partial",
    "authority", "cycle", "missing_plan",
    "nested_text",
])
@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
async def test_json_mode_rejects_invalid_decisions_without_retry(fault, mode):
    proposal = json.loads(
        reply_body()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    )
    if fault == "authority":
        proposal["control_epoch"] = 999
    elif fault == "cycle":
        proposal["plan"]["steps"][0]["depends_on"] = ["verify"]
    elif fault == "missing_plan":
        del proposal["plan"]
    elif fault == "nested_text":
        # Actual qwen-flash JSON mode failure: valid syntax, text in the wrong object.
        proposal["plan"]["text"] = ""
    content = json.dumps(proposal)
    content = {"fence": "```json\n" + content + "\n```", "trailing": content + " notes",
               "partial": content[:-1]}.get(fault, content)
    body = chat_text(content)
    if fault == "length":
        body["choices"][0]["finish_reason"] = "length"
    elif fault == "refusal":
        body["choices"][0]["message"]["refusal"] = "Declined"
    elif fault == "tool_call":
        body = reply_body()
    elif fault == "missing_content":
        body["choices"][0]["message"]["content"] = None
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    client = offline(output_mode=mode, respond=respond)
    error = (
        LLMError if fault in {"tool_call", "missing_content"} else
        ValueError if fault in {"length", "refusal"} else ValidationError
    )
    try:
        with pytest.raises(error):
            await ModelPlanner(client).plan(PLAN_REQUEST)
        assert len(requests) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
def test_json_mode_environment_checks_protocol(monkeypatch, protocol, mode):
    monkeypatch.setenv("LLM_BASE_URL", BASE_URL)
    monkeypatch.setenv("LLM_MODEL", "fixture")
    monkeypatch.setenv("LLM_PROTOCOL", protocol)
    monkeypatch.setenv("LLM_OUTPUT_MODE", " " + mode.upper() + " ")
    if protocol == "openai_chat":
        assert LLMConfig.from_env().output_mode == mode
    else:
        with pytest.raises(LLMError, match="^llm_output_mode_invalid$"):
            LLMConfig.from_env()


@pytest.mark.parametrize("extra", [{"tools": []}, {"tool_choice": "auto"},
                                    {"response_format": {"type": "text"}}])
@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
def test_json_mode_rejects_conflicting_extra_fields_at_startup(extra, mode):
    with pytest.raises(LLMError, match="^llm_extra_body_conflict$"):
        offline(output_mode=mode, extra_body=extra)


def test_strict_schema_expands_declared_skill_parameters_and_result_bindings():
    from wrs_agent.skills.speech import SKILLS
    from wrs_agent.skills.vision import DESCRIBE
    schema = decision_schema(ModelRequest("goal", {"skills": [
        DESCRIBE.spec.model_dump(), SKILLS["speak"].spec.model_dump(),
    ]}))
    assert "patternProperties" not in json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Plan"]["additionalProperties"] is False
    assert set(schema["$defs"]["Plan"]["properties"]) == {"steps"}
    see, say = schema["$defs"]["Step"]["anyOf"]
    assert see["additionalProperties"] is say["additionalProperties"] is False
    assert see["properties"]["args"]["additionalProperties"] is False
    assert see["properties"]["inputs"]["properties"] == {}
    assert say["properties"]["inputs"]["properties"] == {"text": {"$ref": "#/$defs/OutputRef"}}
    assert "text" not in say["properties"]["args"]["required"]
    assert say["properties"]["skill"]["const"] == "speak"
    # The local boundary retains checks the service cannot express.
    from wrs_agent.planner import PlanDecision
    proposal = {"kind": "execute", "plan": {"steps": [
        {"step_id": "say", "skill": "speak", "args": {"bad key": "x"}},
    ]}}
    with pytest.raises(ValidationError):
        PlanDecision.model_validate(proposal)


def test_strict_schema_without_skills_can_only_answer_or_clarify():
    schema = decision_schema(REQUEST)
    assert "$defs" not in schema
    assert schema["properties"]["kind"]["enum"] == ["answer", "clarify"]
    assert schema["properties"]["plan"] == {"type": "null"}


def anthropic_text(content):
    body = reply_body("anthropic_messages")
    body["content"] = [{"type": "text", "text": content}]
    body["stop_reason"] = "end_turn"
    return body


TEXT_REPLIES = {
    "openai_chat": chat_text,
    "openai_responses": responses_text,
    "anthropic_messages": anthropic_text,
}


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("content", ["Still planning.", '{"kind":"execute","plan":{"steps":[]}}'])
def test_text_is_answer_never_executable(protocol, content):
    decision = json.loads(parse(protocol, TEXT_REPLIES[protocol](content)).text)
    assert decision == {"kind": "answer", "text": content, "plan": None}


def chat_fault(body, fault):
    calls = body["choices"][0]["message"]["tool_calls"]
    function = calls[0]["function"]
    if fault == "two_tools":
        calls.append(copy.deepcopy(calls[0]))
    elif fault == "wrong_tool":
        function["name"] = "run_shell"
    elif fault == "empty":
        calls.clear()
    return function


def responses_fault(body, fault):
    call = body["output"][-1]
    if fault == "two_tools":
        body["output"].append(copy.deepcopy(call))
    elif fault == "wrong_tool":
        call["name"] = "run_shell"
    elif fault == "empty":
        body["output"] = [{"type": "reasoning", "summary": []}]
    return call


def anthropic_fault(body, fault):
    call = body["content"][-1]
    if fault == "two_tools":
        body["content"].append(copy.deepcopy(call))
    elif fault == "wrong_tool":
        call["name"] = "run_shell"
    elif fault == "empty":
        body["content"] = body["content"][:1]
    return call


FAULTS = {
    "openai_chat": (chat_fault, "arguments"),
    "openai_responses": (responses_fault, "arguments"),
    "anthropic_messages": (anthropic_fault, "input"),
}


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize(
    "fault",
    ["two_tools", "wrong_tool", "authority", "cycle", "step_field", "empty", "missing_plan"]
)
async def test_bad_tool_calls_fail_closed(protocol, fault):
    body = reply_body(protocol)
    inject, field = FAULTS[protocol]
    call = inject(body, fault)
    invalid_plan = fault in {"authority", "cycle", "step_field", "missing_plan"}
    if invalid_plan:
        proposal = call[field] if field == "input" else json.loads(call[field])
        if fault == "authority":
            proposal["control_epoch"] = 999
        elif fault == "missing_plan":
            del proposal["plan"]
        elif fault == "cycle":
            proposal["plan"]["steps"][0]["depends_on"] = ["verify"]
        else:
            # The prompt asks for schema fields only; the boundary still rejects the rest.
            proposal["plan"]["steps"][0]["resources"] = ["arm"]
        call[field] = proposal if field == "input" else json.dumps(proposal)
    client = offline(protocol, respond=lambda _: httpx.Response(200, json=body))
    try:
        # Protocol errors belong to the adapter; malformed/unsafe plans belong to Planner.
        error = ValidationError if invalid_plan else LLMError
        with pytest.raises(error):
            await ModelPlanner(client).plan(PLAN_REQUEST)
    finally:
        await client.aclose()


async def test_partial_json_arguments_fail_closed():
    body = reply_body()
    body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"kind":'
    client = offline(respond=lambda _: httpx.Response(200, json=body))
    try:
        with pytest.raises(ValidationError):
            await ModelPlanner(client).plan(PLAN_REQUEST)
    finally:
        await client.aclose()


def incomplete_chat(finish):
    body = reply_body()
    body["choices"][0]["finish_reason"] = finish
    return body


def incomplete_responses(status):
    body = reply_body("openai_responses")
    body["status"] = status
    return body


def incomplete_anthropic(stop):
    body = reply_body("anthropic_messages")
    body["stop_reason"] = stop
    return body


@pytest.mark.parametrize(
    "protocol,body",
    [
        *[("openai_chat", incomplete_chat(f)) for f in ["length", "sensitive", "unknown", None]],
        *[("openai_responses", incomplete_responses(s)) for s in ["incomplete", "failed"]],
        *[
            ("anthropic_messages", incomplete_anthropic(s))
            for s in ["max_tokens", "refusal", "pause_turn", None]
        ],
    ],
)
async def test_incomplete_cannot_become_decision(protocol, body):
    client = offline(protocol, respond=lambda _: httpx.Response(200, json=body))
    try:
        with pytest.raises(ValueError, match="incomplete_model_reply"):
            await ModelPlanner(client).plan(PLAN_REQUEST)
    finally:
        await client.aclose()


def test_refusal_cannot_become_decision():
    body = reply_body()
    body["choices"][0]["message"]["refusal"] = "Declined"
    assert parse("openai_chat", body).finish != "complete"
    body = responses_text("")
    body["output"][0]["content"] = [{"type": "refusal", "refusal": "Declined"}]
    assert parse("openai_responses", body).finish != "complete"


@pytest.mark.parametrize(
    "protocol,mutate",
    [
        # Built-in/server tools were never offered, so their output is a protocol fault.
        ("openai_responses", lambda b: b["output"].append({"type": "web_search_call"})),
        ("anthropic_messages", lambda b: b["content"].append({"type": "server_tool_use"})),
        ("anthropic_messages", lambda b: b["content"][-1].update(input="not an object")),
        ("anthropic_messages", lambda b: b.update(role="user")),
        ("openai_responses", lambda b: b["output"][-1].update(arguments={"kind": "answer"})),
    ],
)
def test_unexpected_reply_shapes_rejected(protocol, mutate):
    body = reply_body(protocol)
    mutate(body)
    with pytest.raises(LLMError, match="^llm_invalid_reply$"):
        parse(protocol, body)


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize(
    "data",
    [b"null", b"{", b'{"choices":[]}', b"{}", b" " * (MAX_BYTES + 1)],
    ids=["null", "partial", "no_choices", "empty", "oversize"],
)
def test_malformed_response(protocol, data):
    with pytest.raises(LLMError, match="^llm_invalid_reply$"):
        PROTOCOLS[protocol].parse_reply(data)


# --- HTTP, network and cancellation ---------------------------------------------------------


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("status", [301, 401, 429, 503])
async def test_errors_no_body_leak_no_retry_or_paid_fallback(protocol, status):
    urls = []

    def respond(request):
        urls.append(str(request.url))
        return httpx.Response(
            status,
            text="secret-provider-diagnostic",
            headers={"location": "https://other.invalid/v1/chat/completions"},
        )

    client = offline(protocol, respond=respond)
    try:
        with pytest.raises(LLMError, match=f"^llm_http_{status}$") as error:
            await client.complete(REQUEST)
        assert urls == [BASE_URL + "/" + PROTOCOLS[protocol].PATH]
        assert "secret" not in error.value.error.model_dump_json()
    finally:
        await client.aclose()


async def test_total_timeout_and_cancellation():
    entered, closed = asyncio.Event(), asyncio.Event()

    async def never_returns(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    client = offline(respond=never_returns, timeout_s=0.05)
    try:
        with pytest.raises(LLMError, match="^llm_timeout$"):
            await client.complete(REQUEST)
        assert closed.is_set()
        entered.clear()
        closed.clear()
        pending = asyncio.create_task(client.complete(REQUEST))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert closed.is_set()
    finally:
        await client.aclose()


async def test_oversized_reply_bounded_and_closed():
    client = offline(respond=lambda _: httpx.Response(200, content=b"x" * (MAX_BYTES + 1)))
    try:
        with pytest.raises(LLMError, match="^llm_reply_too_large$"):
            await client.complete(REQUEST)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "error_type,cause_type,expected",
    [
        (httpx.ConnectError, socket.gaierror, "llm_dns_error"),
        (httpx.ConnectError, ssl.SSLCertVerificationError, "llm_tls_certificate_error"),
        (httpx.ConnectError, ssl.SSLEOFError, "llm_tls_error"),
        (httpx.ConnectError, None, "llm_connect_error"),
        (httpx.ProxyError, None, "llm_proxy_error"),
        (httpx.RemoteProtocolError, None, "llm_protocol_error"),
        (httpx.ReadError, None, "llm_transport_error"),
        (httpx.ReadTimeout, None, "llm_timeout"),
    ],
)
async def test_network_causes_are_classified_without_disclosing_values(
    error_type, cause_type, expected
):
    def fail(request):
        error = error_type("private-key-and-proxy-details", request=request)
        if cause_type is not None:
            raise error from cause_type(1, "private-underlying-diagnostic")
        raise error

    client = offline(respond=fail)
    try:
        with pytest.raises(LLMError, match=f"^{expected}$") as error:
            await client.complete(REQUEST)
        assert "private-" not in error.value.error.model_dump_json()
        assert error.value.__suppress_context__
    finally:
        await client.aclose()


def test_network_error_classification_handles_context_and_cycles():
    from wrs_agent.planner.providers.llm import transport_error_code

    error = httpx.ConnectError("private")
    error.__context__ = socket.gaierror(11001, "private")
    assert transport_error_code(error) == "llm_dns_error"
    error.__context__ = error
    assert transport_error_code(error) == "llm_connect_error"


async def test_live_client_never_uses_environment_proxy_without_sending_requests(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "offline-key-no-request")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    client = LLMClient(config(), live_model=True)
    try:
        # A proxy in the environment must never silently carry robot planning requests.
        selected = client._http._transport_for_url(httpx.URL(BASE_URL))
        assert selected is client._http._transport
        assert type(selected._pool).__name__ == "AsyncConnectionPool"
    finally:
        await client.aclose()


async def test_explicit_proxy_is_the_only_proxy(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "offline-key-no-request")
    client = LLMClient(config(proxy="http://127.0.0.1:7890"), live_model=True)
    try:
        assert type(client._http._transport._pool).__name__ == "AsyncHTTPProxy"
    finally:
        await client.aclose()


async def test_offline_fixture_never_uses_environment_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("SSL_CERT_FILE", "missing-test-certificate-file")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=reply_body())

    client = offline(respond=respond)
    try:
        assert (await client.complete(REQUEST)).finish == "complete"
        assert len(requests) == 1
    finally:
        await client.aclose()


# --- Scripts that use the adapter ----------------------------------------------------------


async def test_planning_example_reports_network_failure_and_cleans_up(monkeypatch):
    import runpy
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from wrs_agent.processes import ROOT

    monkeypatch.setenv("LLM_MODEL", "fixture")
    monkeypatch.setenv("LLM_BASE_URL", BASE_URL)
    state = {"closed": False}

    def fail(request):
        raise httpx.ConnectError("private") from socket.gaierror(11001, "private")

    client = offline(respond=fail)

    async def snapshot():
        return SimpleNamespace(data=SimpleNamespace(model_dump=lambda: {}))

    async def skills():
        return []

    @asynccontextmanager
    async def stack(**kwargs):
        try:
            yield SimpleNamespace(system=SimpleNamespace(snapshot=snapshot, skills=skills))
        finally:
            state["closed"] = True

    monkeypatch.setattr("wrs_agent.planner.providers.llm.LLMClient", lambda *a, **kw: client)
    monkeypatch.setattr("wrs_agent.processes.LocalStack", stack)
    entry = runpy.run_path(str(ROOT / "examples/models/01_plan.py"))
    with pytest.raises(SystemExit, match="llm_dns_error") as error:
        await entry["main"]()
    assert "无法解析" in str(error.value) and "private" not in str(error.value)
    assert state["closed"] and client._http.is_closed


async def test_connection_check_sends_no_key_and_accepts_http_error_as_reachable(
    monkeypatch, capsys
):
    import runpy

    from wrs_agent.processes import ROOT

    requests = []

    def respond(request):
        requests.append(request)
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        assert request.method == "GET" and not request.content
        return httpx.Response(401)

    monkeypatch.setenv("LLM_API_KEY", "private-key-that-must-not-be-sent")
    entry = runpy.run_path(str(ROOT / "scripts/check_llm_connection.py"))
    monkeypatch.setitem(
        entry["check"].__globals__, "http_transport", lambda _: httpx.MockTransport(respond)
    )
    assert await entry["check"](config()) == 0
    assert [str(r.url) for r in requests] == [BASE_URL + "/"]
    assert "private" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("url", "code"),
    [("http://private-remote.invalid/v1", "llm_base_url_invalid"), ("", "llm_base_url_missing")],
)
def test_connection_check_rejects_invalid_endpoint_before_network(monkeypatch, capsys, url, code):
    import runpy

    from wrs_agent.processes import ROOT

    monkeypatch.setenv("LLM_BASE_URL", url)
    entry = runpy.run_path(str(ROOT / "scripts/check_llm_connection.py"))
    monkeypatch.setitem(entry["main"].__globals__, "asyncio", None)  # Any network use fails.
    monkeypatch.setattr("sys.argv", ["check_llm_connection.py"])
    assert entry["main"]() == 1
    assert code in capsys.readouterr().out
