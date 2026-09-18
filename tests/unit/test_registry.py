from types import SimpleNamespace

import pytest
import zenoh

from wrs_agent import step
from wrs_agent.bindings import DEFAULT, load_bindings
from wrs_agent.registry import NodeRegistry
from wrs_agent.schemas import CapabilitySnapshot, NodeInfo


class QueryFixture:
    """Unit-only Zenoh boundary; cross-process liveliness is tested separately."""

    def __init__(self, info):
        self.info = info
        self.session = SimpleNamespace(liveliness=lambda: self)
        self.handles = []
        self.live = {info.boot_id}
        self.callback = None
        self.before_reply = None
        self.queries = 0

    def key(self, suffix):
        return "unit/" + suffix

    def declare_subscriber(self, key, handler, *, history):
        assert history
        self.callback = handler.callback
        for boot in self.live.copy():
            self.emit(boot, True)
        return SimpleNamespace(undeclare=lambda: None)

    def emit(self, boot, present):
        self.live.add(boot) if present else self.live.discard(boot)
        self.callback(
            SimpleNamespace(
                key_expr=f"unit/presence/speaker/{boot}",
                kind=zenoh.SampleKind.PUT if present else zenoh.SampleKind.DELETE,
            )
        )

    async def request(self, key, payload, *, timeout):  # noqa: ASYNC109 - query fixture
        self.queries += 1
        if self.info is None:
            raise TimeoutError
        result = self.info.model_dump()
        if self.before_reply:
            self.before_reply()
        return result


def speaker(boot="first", **changes):
    return NodeInfo(
        node_id="speaker",
        node_type="tts",
        boot_id=boot,
        ready=True,
        health="ready",
        skills=["speak"],
        capabilities=["speak"],
    ).model_copy(update=changes)


def registry(bus):
    return NodeRegistry(
        {"speaker": bus}, {"speaker": {"type": "tts", "enabled": True}}, {"speak": "speaker"}
    )


async def test_registry_presence_offline_restart_and_explicit_binding():
    bus = QueryFixture(speaker())
    view = registry(bus)
    await view.refresh()
    assert view.node_for("speak") == "speaker"
    assert not hasattr(view, "checked")  # No local heartbeat/expiry authority remains.
    bus.emit("first", False)
    assert view.snapshot()["speaker"]["health"] == "offline"
    with pytest.raises(ValueError, match="not_ready"):
        view.node_for("speak")
    bus.info = None
    before = bus.queries
    assert (await view.refresh())["speaker"]["health"] == "offline"
    assert bus.queries == before  # Native absence needs no query timeout.
    bus.info = speaker("second", capabilities=[])
    bus.emit("second", True)
    await view.refresh()
    with pytest.raises(ValueError, match="unsupported_skill"):
        view.node_for("speak")
    assert view.entries["speaker"].boot_id == "second"
    bus.info = bus.info.model_copy(update={"node_id": "impostor"})
    assert not (await view.refresh())["speaker"]["ready"]


async def test_presence_is_not_readiness_and_capability_cache_tracks_instance():
    bus = QueryFixture(speaker())
    view = registry(bus)
    calls = 0

    async def capabilities():
        nonlocal calls
        calls += 1
        return CapabilitySnapshot(skills=["speak"], robot_controls=False)

    client = SimpleNamespace(capabilities=capabilities)
    await view.refresh()
    await view.capabilities("speaker", client)
    await view.refresh()
    await view.capabilities("speaker", client)
    assert calls == 1
    bus.info = speaker(ready=False, health="held")
    assert (await view.refresh())["speaker"]["health"] == "held"
    with pytest.raises(ValueError, match="not_ready"):
        view.node_for("speak")
    bus.emit("first", False)
    bus.emit("second", True)
    bus.info = speaker("second")
    await view.refresh()
    await view.capabilities("speaker", client)
    assert calls == 2
    with pytest.raises(ValueError, match="instance_changed"):
        view.check_instance("speaker", "first")
    bus.info = None  # Session/token alive, application no longer answers.
    offline_app = (await view.refresh())["speaker"]
    assert offline_app["boot_id"] == "second" and offline_app["health"] == "unknown"
    assert not offline_app["ready"]


async def test_duplicate_instances_and_descriptor_leave_race_fail_closed():
    bus = QueryFixture(speaker())
    view = registry(bus)
    # A descriptor reply must not revive a token removed during its query.
    bus.before_reply = lambda: bus.emit("first", False)
    assert (await view.refresh())["speaker"]["health"] == "offline"
    bus.before_reply = None
    bus.emit("first", True)
    bus.emit("second", True)
    assert (await view.refresh())["speaker"]["health"] == "unknown"
    with pytest.raises(ValueError, match="not_ready"):
        view.node_for("speak")
    bus.emit("second", False)
    assert (await view.refresh())["speaker"]["ready"]
    for n in range(17):
        bus.emit(f"extra-{n}", True)
    assert view.snapshot()["speaker"]["health"] == "unknown"


def test_bindings_allow_named_providers_but_not_duplicate_action_endpoints(tmp_path):
    text = DEFAULT.read_text(encoding="utf-8").replace("nodes.wrs", "nodes.wrs_lite6")
    text = text.replace('= "wrs"', '= "wrs_lite6"').replace('type = "wrs_lite6"', 'type = "wrs"')
    path = tmp_path / "bindings.toml"
    path.write_text(text, encoding="utf-8")
    nodes, bindings = load_bindings(path)
    assert bindings["pick"] == "wrs_lite6" and nodes["wrs_lite6"]["type"] == "wrs"
    path.write_text(text.replace('suffix = "-tts"', 'suffix = ""'), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_execution_endpoint"):
        load_bindings(path)


def test_small_step_api_preserves_explicit_dependencies():
    observed = step("observe")
    picked = step("pick", object="A", after=observed)
    said = step("speak", text="hello")
    assert picked.depends_on == [observed.step_id]
    assert said.depends_on == []
