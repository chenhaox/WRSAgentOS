"""HTTP responses for real LLMClient tests; no substitute model client."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from wrs_agent.planner.providers.llm import ENV_FIELDS, LLMClient, LLMConfig

REPLY = Path(__file__).parent / "fixtures/models/openai_chat_tool_call.json"


def chat_reply(proposal):
    body = json.loads(REPLY.read_text(encoding="utf-8"))
    body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = proposal
    return body


@pytest.fixture
async def make_llm():
    clients = []

    def make(reply=None, **config):
        if callable(reply):
            handler = reply
        else:
            body = (
                json.loads(REPLY.read_text(encoding="utf-8"))
                if reply is None else chat_reply(reply)
            )

            def handler(request):
                return httpx.Response(200, json=body)

        client = LLMClient(
            LLMConfig(model="fixture", base_url="https://model.invalid/v1", **config),
            transport=httpx.MockTransport(handler),
        )
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


@pytest.fixture
def llm_server(monkeypatch):
    """Loopback HTTP endpoint shared with Agent child processes and sync callers.

    Tests explicitly use live_model=True and control the response/gate here. Every
    LLM setting is isolated from the operator's account, including extra headers.
    """
    state = SimpleNamespace(
        response=json.loads(REPLY.read_text(encoding="utf-8")),
        gate=threading.Event(), entered=threading.Event(), requests=[],
    )
    state.gate.set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state.requests.append(json.loads(body))
            state.entered.set()
            if not state.gate.wait(15):
                self.send_error(504)
                return
            data = json.dumps(state.response).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # Cancelling an in-flight LLM request closes its socket.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False  # server_close joins released request handlers too.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    for field in (*ENV_FIELDS, "api_key"):
        monkeypatch.delenv("LLM_" + field.upper(), raising=False)
    monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("LLM_MODEL", "offline-http-fixture")
    thread.start()
    try:
        yield state
    finally:
        state.gate.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
