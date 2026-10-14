"""HTTP model client for any vendor, selected by wire protocol rather than by vendor name.

Three protocols cover most services. A new vendor on one of them is configuration only
(LLM_BASE_URL, LLM_MODEL, LLM_EXTRA_BODY); a new protocol is one module exposing PATH,
headers, request_body and parse_reply, added to PROTOCOLS. Proposes plans; never
executes provider tools.
"""

import asyncio
import json
import os
import socket
import ssl
import time
from dataclasses import replace
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, ValidationError, field_validator

from wrs_agent.planner.providers import (
    ModelReply,
    ModelRequest,
    ModelTiming,
    anthropic_messages,
    openai_chat,
    openai_responses,
)
from wrs_agent.planner.providers.wire import LLMError
from wrs_agent.schemas import MAX_BYTES, Boundary, encode

__all__ = ["PROTOCOLS", "LLMClient", "LLMConfig", "LLMError", "transport_error_code"]

PROTOCOLS = {
    "openai_chat": openai_chat,
    "openai_responses": openai_responses,
    "anthropic_messages": anthropic_messages,
}
# The union of the level names OpenAI, GLM and Claude publish. Values pass through
# unchanged; which of them a given model accepts is the provider's decision.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Headers the adapter or HTTP layer owns; LLM_EXTRA_HEADERS may not replace them.
RESERVED_HEADERS = {
    "anthropic-version", "authorization", "connection", "content-length", "content-type",
    "host", "transfer-encoding", "user-agent", "x-api-key",
}
USER_AGENT = "WRS-Agent/0.1.0"
ENV_FIELDS = (
    "protocol", "base_url", "model", "reasoning_effort", "tool_choice", "output_mode", "max_tokens",
    "timeout_s", "extra_body", "extra_headers", "proxy",
)


def _check_url(value: str, *, proxy: bool = False) -> str:
    parts = urlsplit(value)
    if (
        not value.isascii()
        or not value.isprintable()
        or " " in value
        or parts.scheme not in {"https", "http"}
        or not parts.hostname
        or parts.query
        or parts.fragment
    ):
        raise ValueError("invalid_url")
    _ = parts.port  # Raises ValueError for a malformed port.
    # An endpoint receives the API key: it cannot embed other credentials, and only a
    # loopback server may be reached in plaintext. A proxy tunnels HTTPS via CONNECT.
    if not proxy and (parts.username or parts.password):
        raise ValueError("credentials_in_url")
    if not proxy and parts.scheme == "http" and parts.hostname not in LOOPBACK:
        raise ValueError("plaintext_requires_loopback")
    return value.rstrip("/")


class LLMConfig(Boundary):
    protocol: Literal["openai_chat", "openai_responses", "anthropic_messages"] = "openai_chat"
    base_url: str = Field(min_length=1, max_length=512)
    # Slashes, colons and @ appear in OpenRouter, Ollama and Hugging Face model IDs.
    model: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]*$"
    )
    # None omits the field, leaving the provider's default. Less reasoning trades planning
    # quality for latency; Runtime still validates every plan before execution.
    reasoning_effort: ReasoningEffort | None = None
    # Under auto some models write the plan as message text, which is only ever an answer;
    # required requests propose_plan. Neither value guarantees syntactically valid arguments.
    tool_choice: Literal["auto", "required"] = "auto"
    # Only an explicit JSON mode lets message content carry a proposed decision.
    output_mode: Literal["tools", "json_object", "json_schema"] = "tools"
    max_tokens: int = Field(default=2048, ge=128, le=65536)
    timeout_s: float = Field(default=20.0, gt=0, le=300)
    # Vendor-specific request fields, e.g. GLM's thinking switch or vLLM template flags.
    extra_body: dict = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # Explicit only: environment proxy variables never silently carry robot planning.
    proxy: str | None = Field(default=None, max_length=512)

    @field_validator("output_mode")
    @classmethod
    def _output_mode(cls, value, info):
        if value != "tools" and info.data.get("protocol") != "openai_chat":
            raise ValueError("json_mode_requires_openai_chat")
        return value

    @field_validator("base_url")
    @classmethod
    def _endpoint(cls, value):
        return _check_url(value)

    @field_validator("proxy")
    @classmethod
    def _proxy(cls, value):
        return None if value is None else _check_url(value, proxy=True)

    @field_validator("extra_body")
    @classmethod
    def _extra_body(cls, value):
        if len(encode(value)) > 4096:
            raise ValueError("extra_body_too_large")
        return value

    @field_validator("extra_headers")
    @classmethod
    def _extra_headers(cls, value):
        for name, text in value.items():
            if (
                not 0 < len(name) <= 64
                or not all(c.isascii() and (c.isalnum() or c == "-") for c in name)
                or name.lower() in RESERVED_HEADERS
                or len(text) > 1024
                or not text.isascii()
                or not text.isprintable()
            ):
                raise ValueError("invalid_header")
        return value

    @property
    def local(self) -> bool:
        return urlsplit(self.base_url).hostname in LOOPBACK

    @classmethod
    def from_env(cls):
        """Read LLM_* variables. Model and endpoint are both required: credentials alone
        do not identify an account's enabled model, and no vendor is assumed."""
        env = {name: os.environ.get("LLM_" + name.upper(), "").strip() for name in ENV_FIELDS}
        for name in ("model", "base_url"):
            if not env[name]:
                raise LLMError(f"llm_{name}_missing")
        values = {}
        for name, text in env.items():
            if not text:
                continue
            try:
                if name == "max_tokens":
                    values[name] = int(text)
                elif name == "timeout_s":
                    values[name] = float(text)
                elif name in {"extra_body", "extra_headers"}:
                    values[name] = json.loads(text)
                elif name in {"protocol", "reasoning_effort", "tool_choice", "output_mode"}:
                    values[name] = text.lower()
                else:
                    values[name] = text
            except ValueError:
                raise LLMError(f"llm_{name}_invalid") from None
        try:
            return cls(**values)
        except ValidationError as exc:
            field = exc.errors(include_input=False)[0]["loc"][0]
            raise LLMError(f"llm_{field}_invalid") from None


def transport_error_code(exc):
    """Classify known causes without copying URLs, headers or exception messages."""
    current = exc
    seen = set()
    for _ in range(12):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return "llm_dns_error"
        if isinstance(current, ssl.SSLCertVerificationError):
            return "llm_tls_certificate_error"
        if isinstance(current, ssl.SSLError):
            return "llm_tls_error"
        current = current.__cause__ or current.__context__
    if isinstance(exc, httpx.ProxyError):
        return "llm_proxy_error"
    if isinstance(exc, httpx.ConnectError):
        return "llm_connect_error"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "llm_protocol_error"
    return "llm_transport_error"


def http_transport(config: LLMConfig) -> httpx.AsyncHTTPTransport:
    # An explicit transport disables HTTPX environment proxy discovery, and its trust_env
    # keeps SSL_CERT_FILE/SSL_CERT_DIR out of the TLS context. The retry covers connection
    # setup only: a request already sent is never repeated.
    return httpx.AsyncHTTPTransport(retries=1, trust_env=False, proxy=config.proxy)


class LLMClient:
    def __init__(
        self,
        config: LLMConfig,
        *,
        live_model: bool = False,
        transport: httpx.MockTransport | None = None,
    ):
        # Only an explicit offline HTTP fixture bypasses the live opt-in.
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise LLMError("llm_offline_transport_required")
        if transport is None and not live_model:
            raise LLMError("llm_live_model_opt_in_required")
        key = "offline-fixture" if transport is not None else os.environ.get("LLM_API_KEY", "")
        # A loopback server (vLLM, Ollama) may run without a key; a remote one may not.
        if (not key and not config.local) or (
            key and (len(key) > 512 or not key.isascii() or not key.isprintable() or " " in key)
        ):
            raise LLMError("llm_api_key_missing_or_invalid")
        self.config = config
        self.wire = PROTOCOLS[config.protocol]
        # A conflicting LLM_EXTRA_BODY fails at startup, not on the first spoken goal.
        self.wire.request_body(ModelRequest("", {}), config)
        self._http = httpx.AsyncClient(
            base_url=config.base_url + "/",
            headers={**config.extra_headers, **self.wire.headers(key), "User-Agent": USER_AGENT},
            timeout=config.timeout_s,
            follow_redirects=False,
            transport=transport or http_transport(config),
        )

    async def complete(self, request: ModelRequest) -> ModelReply:
        body = self.wire.request_body(request, self.config)
        started, first_byte = time.perf_counter(), None
        try:
            async with asyncio.timeout(self.config.timeout_s):
                async with self._http.stream("POST", self.wire.PATH, json=body) as response:
                    if response.status_code != 200:
                        raise LLMError(f"llm_http_{response.status_code}")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if first_byte is None:
                            first_byte = time.perf_counter()
                        if len(data) + len(chunk) > MAX_BYTES:
                            raise LLMError("llm_reply_too_large")
                        data.extend(chunk)
            received = time.perf_counter()
            # An empty body fails, so first_byte is set. Parsing uses the requested mode;
            # model content never chooses its own interpretation.
            reply = self.wire.parse_reply(bytes(data), self.config)
            done = time.perf_counter()
            timing = ModelTiming(
                total=done - started,
                wait=first_byte - started,
                receive=received - first_byte,
                parse=done - received,
            )
            return replace(reply, timing=timing)
        except (httpx.TimeoutException, TimeoutError):
            raise LLMError("llm_timeout") from None
        except httpx.HTTPError as exc:
            raise LLMError(transport_error_code(exc)) from None

    async def aclose(self) -> None:
        await self._http.aclose()
