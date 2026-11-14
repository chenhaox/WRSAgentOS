"""The dashboard is an ordinary node: it discovers peers and drives them over Zenoh."""

import asyncio
import json
import socket
import urllib.error
import urllib.request

import pytest
from conftest import eventually

from wrs_agent.processes import LocalStack


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def http(port, path, body=None, *, headers=None, timeout=5, raw=False):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read()
            return response.status, payload if raw else json.loads(payload)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


@pytest.mark.zenoh
async def test_ui_node_discovers_peers_and_runs_skills(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config = tmp_path / "ui.toml"
    config.write_text(
        '[nodes.wrs]\ntype="wrs"\nsuffix=""\nactions=true\nenabled=true\n'
        '[nodes.agent]\ntype="agent"\nsuffix=""\nactions=false\nenabled=true\n'
        '[nodes.ui]\ntype="ui"\nsuffix="-ui"\nactions=false\nenabled=true\n',
        encoding="utf-8",
    )
    port = free_port()
    async with LocalStack(bindings=config, ui_port=port, ui_viewer=False) as stack:
        nodes = await stack.system.nodes()
        assert nodes["ui"]["node_type"] == "ui"
        assert nodes["ui"]["features"] == ["ui.dashboard"]

        async def state():
            return (await asyncio.to_thread(http, port, "/api/state"))[1]

        current = await eventually(
            state, lambda s: s["roles"]["wrs"] == "wrs" and s["roles"]["agent"] == "agent"
            and any(spec["name"] == "move_named_pose" for spec in s["skills"]),
        )
        assert current["info"]["env_id"] == stack.env_id

        code, page = await asyncio.to_thread(lambda: http(port, "/", raw=True))
        assert code == 200 and b"WRS-Agent" in page
        status, result = await asyncio.to_thread(
            http, port, "/api/skill", {"skill": "move_named_pose", "args": {"pose": "B"}},
        )
        assert status == 200 and result["ok"], result
        await eventually(
            state, lambda s: s["robot"]["pose"] == "B" and s["task"]["state"] == "SUCCEEDED",
        )
        _, log = await asyncio.to_thread(http, port, "/api/events?after=0")
        kinds = [event["type"] for event in log["events"]]
        assert "action" in kinds and "done" in kinds

        _, result = await asyncio.to_thread(http, port, "/api/skill", {"skill": "move_named_pose"})
        assert not result["ok"] and "pose" in result["message"]
        _, result = await asyncio.to_thread(http, port, "/api/stop", {})
        assert result["ok"], result
        _, result = await asyncio.to_thread(http, port, "/api/text", {"text": "状态"})
        assert result["ok"] and "SUCCEEDED" in result["message"]
        _, result = await asyncio.to_thread(http, port, "/api/planner", {"enabled": True})
        assert not result["ok"] and "Base URL" in result["message"]

        forged, _ = await asyncio.to_thread(
            http, port, "/api/state", headers={"Host": "attacker.example"},
        )
        assert forged == 403
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/stop", data=b"x=1")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        with pytest.raises(urllib.error.HTTPError) as rejected:
            await asyncio.to_thread(
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open, request,
            )
        assert rejected.value.code == 415


UI_BINDINGS = (
    '[nodes.ui]\ntype="ui"\nsuffix="-ui"\nactions=false\nenabled=true\n'
)


async def events(port, kind):
    _, log = await asyncio.to_thread(http, port, "/api/events?after=0")
    return [event for event in log["events"] if event["type"] == kind]


@pytest.mark.zenoh
async def test_ui_node_switches_the_agent_planner_at_runtime(tmp_path, llm_server):
    config = tmp_path / "ui.toml"
    config.write_text(
        '[nodes.wrs]\ntype="wrs"\nsuffix=""\nactions=true\nenabled=true\n'
        '[nodes.agent]\ntype="agent"\nsuffix=""\nactions=false\nenabled=true\n' + UI_BINDINGS,
        encoding="utf-8",
    )
    port = free_port()
    async with LocalStack(bindings=config, ui_port=port, ui_viewer=False, duration=0.02):
        async def state():
            return (await asyncio.to_thread(http, port, "/api/state"))[1]

        current = await eventually(state, lambda s: s["planner"] is not None)
        assert current["planner"]["enabled"] is False
        assert current["planner"]["model"] == "offline-http-fixture"
        _, refused = await asyncio.to_thread(http, port, "/api/text", {"text": "把 A 放到 B"})
        assert not refused["ok"] and llm_server.requests == []

        _, result = await asyncio.to_thread(http, port, "/api/planner", {"enabled": True})
        assert result["ok"], result
        assert (await state())["planner"]["enabled"] is True
        _, result = await asyncio.to_thread(http, port, "/api/text", {"text": "把 A 放到 B"})
        assert result["ok"], result
        await eventually(lambda: llm_server.requests, bool)
        await eventually(
            lambda: events(port, "planning"),
            lambda rows: any(row["state"] != "WAITING" for row in rows),
        )

        _, result = await asyncio.to_thread(
            http, port, "/api/planner", {"enabled": True, "base_url": "ftp://bad"},
        )
        assert not result["ok"] and (await state())["planner"]["enabled"] is True
        _, result = await asyncio.to_thread(http, port, "/api/planner", {"enabled": False})
        assert result["ok"] and (await state())["planner"]["enabled"] is False


@pytest.mark.zenoh
async def test_ui_node_push_to_talk_goes_through_the_asr_node():
    port = free_port()
    async with LocalStack(
        bindings="examples/ui/voice_bindings.toml", ui_port=port, ui_viewer=False,
        asr_script=["状态", "停止"],
    ):
        async def state():
            return (await asyncio.to_thread(http, port, "/api/state"))[1]

        await eventually(state, lambda s: all(s["roles"][r] for r in ("asr", "voice", "tts")))
        for heard in ("状态", "停止"):
            _, result = await asyncio.to_thread(http, port, "/api/listen/begin", {})
            assert result["ok"], result
            _, result = await asyncio.to_thread(http, port, "/api/listen/end", {})
            assert result["ok"], result
            current = await eventually(state, lambda s, heard=heard: s["voice"]["heard"] == heard)
            assert not current["voice"]["recognizing"]
        spoken = [row["text"] for row in await events(port, "user") if row.get("kind") == "voice"]
        assert spoken == ["状态", "停止"]
        replies = [row["text"] for row in await events(port, "reply")]
        assert any(text.startswith("语音停止") for text in replies), replies

        _, result = await asyncio.to_thread(http, port, "/api/speak", {"text": "你好"})
        assert result["ok"], result
