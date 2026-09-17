"""Native GLM fixtures only. No external HTTP, account or hardware required."""

import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from wrs_agent.planner.api import PlanRequest
from wrs_agent.planner.model import ModelPlanner
from wrs_agent.planner.providers.api import ModelRequest
from wrs_agent.planner.providers.glm import (
    CODING_BASE_URL,
    GLMClient,
    GLMConfig,
    GLMError,
    parse_reply,
)
from wrs_agent.schemas import MAX_BYTES

FIXTURE = Path(__file__).parents[2] / "examples" / "fixtures" / "glm_tool_call.json"
REQUEST = ModelRequest("put A in B", {"world": {}, "skills": []})


def reply_body():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def as_bytes(body):
    return json.dumps(body).encode()


async def test_native_tool_roundtrip_and_reused_client(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "must-not-leave-the-process")
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == CODING_BASE_URL + "/chat/completions"
        assert request.headers["Authorization"] == "Bearer offline-fixture"
        assert request.headers["User-Agent"] == "WRS-Agent/0.1.0"
        body = json.loads(request.content)
        assert body["model"] == "account-model"
        assert body["stream"] is False and body["tool_choice"] == "auto"
        assert [t["function"]["name"] for t in body["tools"]] == ["propose_plan"]
        return httpx.Response(200, json=reply_body())

    client = GLMClient(GLMConfig(model="account-model"), transport=httpx.MockTransport(respond))
    try:
        reply = await client.complete(REQUEST)
        assert reply.finish == "complete" and reply.usage["total_tokens"] == 100
        assert reply.metadata["message"]["tool_calls"][0]["id"] == "proposal-1"
        decision = await ModelPlanner(client).plan(
            PlanRequest(user_goal=REQUEST.goal, world={}, skills=[])
        )
        assert decision.kind == "execute"
        assert decision.plan.steps[-1].skill == "verify"
        assert len(requests) == 2
    finally:
        await client.aclose()
    assert client._http.is_closed


@pytest.mark.parametrize("content", ["Still planning.", '{"kind":"execute","plan":{"steps":[]}}'])
def test_text_is_answer_never_executable(content):
    body = reply_body()
    body["choices"][0] = {
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": content},
    }
    reply = parse_reply(as_bytes(body))
    decision = json.loads(reply.text)
    assert decision["kind"] == "answer" and decision["plan"] is None
    assert decision["text"] == content


@pytest.mark.parametrize(
    "fault", ["two_tools", "wrong_tool", "partial", "authority", "cycle", "empty"]
)
def test_bad_tool_calls_fail_closed(fault):
    body = reply_body()
    calls = body["choices"][0]["message"]["tool_calls"]
    if fault == "two_tools":
        calls.append(copy.deepcopy(calls[0]))
    elif fault == "wrong_tool":
        calls[0]["function"]["name"] = "run_shell"
    elif fault == "partial":
        calls[0]["function"]["arguments"] = '{"kind":"execute","plan":'
    elif fault == "authority":
        arguments = json.loads(calls[0]["function"]["arguments"])
        arguments["control_epoch"] = 999
        calls[0]["function"]["arguments"] = json.dumps(arguments)
    elif fault == "cycle":
        arguments = json.loads(calls[0]["function"]["arguments"])
        arguments["plan"]["steps"][0]["depends_on"] = ["verify"]
        calls[0]["function"]["arguments"] = json.dumps(arguments)
    else:
        calls.clear()
    with pytest.raises(GLMError, match="^glm_invalid_reply$"):
        parse_reply(as_bytes(body))


@pytest.mark.parametrize("finish", ["length", "sensitive", "network_error", "unknown", None])
async def test_incomplete_cannot_become_decision(finish):
    body = reply_body()
    body["choices"][0]["finish_reason"] = finish
    client = GLMClient(
        GLMConfig(model="fixture"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    try:
        with pytest.raises(ValueError, match="incomplete_model_reply"):
            await ModelPlanner(client).plan(PlanRequest(user_goal="goal", world={}, skills=[]))
    finally:
        await client.aclose()


def test_refusal_cannot_become_decision():
    body = reply_body()
    body["choices"][0]["message"]["refusal"] = "Declined"
    assert parse_reply(as_bytes(body)).finish != "complete"


@pytest.mark.parametrize(
    "data",
    [b"null", b"{", b'{"choices":[]}', b'{"choices":[{}]}', b" " * (MAX_BYTES + 1)],
    ids=["null", "partial", "no_choices", "no_message", "oversize"],
)
def test_malformed_response(data):
    with pytest.raises(GLMError, match="^glm_invalid_reply$"):
        parse_reply(data)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stream": True},
        {"thinking": {"type": "enabled"}},
        {"protocol": "anthropic"},
        {"base_url": "https://untrusted.invalid"},
        {"timeout_s": float("inf")},
        {"model": ""},
    ],
)
def test_unsupported_configuration_rejected(kwargs):
    with pytest.raises(ValidationError):
        GLMConfig(**({"model": "fixture"} | kwargs))


def test_live_opt_in_and_required_environment(monkeypatch):
    monkeypatch.delenv("GLM_MODEL", raising=False)
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        GLMConfig.from_env()
    with pytest.raises(GLMError, match="glm_live_model_opt_in_required"):
        GLMClient(GLMConfig(model="fixture"))
    with pytest.raises(GLMError, match="glm_api_key_missing_or_invalid"):
        GLMClient(GLMConfig(model="fixture"), live_model=True)
    monkeypatch.setenv("GLM_MODEL", "explicit-account-model")
    monkeypatch.setenv("GLM_BASE_URL", CODING_BASE_URL + "/")
    assert GLMConfig.from_env().model == "explicit-account-model"


@pytest.mark.parametrize("status", [301, 401, 429, 503])
async def test_errors_no_body_leak_no_retry_or_paid_fallback(status):
    urls = []

    def respond(request):
        urls.append(str(request.url))
        return httpx.Response(
            status,
            text="secret-provider-diagnostic",
            headers={"location": "https://open.bigmodel.cn/api/paas/v4/chat/completions"},
        )

    client = GLMClient(GLMConfig(model="fixture"), transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(GLMError, match=f"^glm_http_{status}$"):
            await client.complete(REQUEST)
        assert urls == [CODING_BASE_URL + "/chat/completions"]
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

    client = GLMClient(
        GLMConfig(model="fixture", timeout_s=0.05),
        transport=httpx.MockTransport(never_returns),
    )
    try:
        with pytest.raises(GLMError, match="^glm_timeout$"):
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


async def test_network_failure_redacted():
    def fail(request):
        raise httpx.ConnectError("sensitive network diagnostic", request=request)

    client = GLMClient(GLMConfig(model="fixture"), transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(GLMError, match="^glm_transport_error$"):
            await client.complete(REQUEST)
    finally:
        await client.aclose()


async def test_oversized_reply_bounded_and_closed():
    client = GLMClient(
        GLMConfig(model="fixture"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"x" * (MAX_BYTES + 1))
        ),
    )
    try:
        with pytest.raises(GLMError, match="^glm_reply_too_large$"):
            await client.complete(REQUEST)
    finally:
        await client.aclose()
