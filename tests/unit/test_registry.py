import pytest

from wrs_agent import step
from wrs_agent.bindings import DEFAULT, load_nodes
from wrs_agent.registry import NodeRegistry
from wrs_agent.schemas import NodeInfo


class QueryFixture:
    def __init__(self, info):
        self.info = info

    async def request(self, key, payload, *, timeout):  # noqa: ASYNC109 - query fixture
        if self.info is None:
            raise TimeoutError
        return self.info.model_dump()


async def test_registry_expiry_offline_restart_and_explicit_binding():
    definitions = {"speaker": {"type": "tts", "enabled": True}}
    bus = QueryFixture(
        NodeInfo(
            node_id="speaker",
            node_type="tts",
            boot_id="first",
            ready=True,
            health="ready",
            skills=["speak"],
            capabilities=["speak"],
        )
    )
    registry = NodeRegistry({"speaker": bus}, definitions, {"speak": "speaker"})
    await registry.refresh()
    assert registry.provider("speak") == "speaker"
    registry.checked["speaker"] -= 3
    with pytest.raises(ValueError, match="not_ready"):
        registry.provider("speak")
    assert registry.snapshot()["speaker"]["health"] == "stale"
    bus.info = None
    assert (await registry.refresh())["speaker"]["health"] == "offline"
    bus.info = NodeInfo(
        node_id="speaker",
        node_type="tts",
        boot_id="second",
        ready=True,
        health="ready",
        skills=["speak"],
    )
    await registry.refresh()
    with pytest.raises(ValueError, match="unsupported_skill"):
        registry.provider("speak")
    assert registry.entries["speaker"].boot_id == "second"
    bus.info = bus.info.model_copy(update={"node_id": "impostor"})
    assert not (await registry.refresh())["speaker"]["ready"]


def test_bindings_allow_named_providers_but_not_duplicate_action_endpoints(tmp_path):
    text = DEFAULT.read_text(encoding="utf-8").replace("nodes.wrs", "nodes.wrs_lite6")
    text = text.replace('= "wrs"', '= "wrs_lite6"').replace('type = "wrs_lite6"', 'type = "wrs"')
    path = tmp_path / "bindings.toml"
    path.write_text(text, encoding="utf-8")
    nodes, bindings = load_nodes(path)
    assert bindings["pick"] == "wrs_lite6" and nodes["wrs_lite6"]["type"] == "wrs"
    path.write_text(text.replace('suffix = "-tts"', 'suffix = ""'), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_execution_endpoint"):
        load_nodes(path)


def test_small_step_api_preserves_explicit_dependencies():
    observed = step("observe")
    picked = step("pick", object="A", after=observed)
    said = step("speak", text="hello")
    assert picked.depends_on == [observed.step_id]
    assert said.depends_on == []
