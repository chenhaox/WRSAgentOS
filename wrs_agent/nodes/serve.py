"""Built-in nodes, launcher options, and the entry point for serving any Node subclass."""

from wrs_agent.bindings import load_bindings
from wrs_agent.nodes.agent import AgentNode
from wrs_agent.nodes.asr import AsrNode
from wrs_agent.nodes.node import Node
from wrs_agent.nodes.tts import TtsNode
from wrs_agent.nodes.ui import UiNode
from wrs_agent.nodes.voice import VoiceNode
from wrs_agent.nodes.wrs import WrsNode

NODES = {
    node.node_type: node for node in (WrsNode, TtsNode, AgentNode, VoiceNode, AsrNode, UiNode)
}


def node_options(role, arguments):
    """Translate the existing flat launcher arguments using the selected node's declaration."""
    return {
        field: arguments[argument]
        for field, argument in NODES[role].launch_options.items()
        if arguments.get(argument) is not None
    }


async def serve_node(
    node, *, node_id=None, options=None, bindings=None, suffix=None, actions=None,
    peers=None, skill_bindings=None,
    endpoint="tcp/127.0.0.1:7447", site="local", env_id="arm01", journal=None,
):
    """Construct and serve one node; only setup() may acquire runtime resources."""
    if isinstance(node, str):
        try:
            node = NODES[node]
        except KeyError:
            raise ValueError("unsupported_node_role") from None
    if not isinstance(node, type) or not issubclass(node, Node):
        raise TypeError("node_class_required")
    # A deployment file is an explicit convenience at this entry point, never Node state.
    if bindings is not None:
        definitions, routes = load_bindings(bindings)
        node_id = node_id or node.node_type
        definition = definitions.get(node_id)
        if not definition or definition["type"] != node.node_type or not definition["enabled"]:
            raise ValueError("node_not_configured")
        suffix = definition["suffix"] if suffix is None else suffix
        actions = definition["actions"] if actions is None else actions
        selected = dict(peers or {})
        for role in node.requires:
            if role in selected:
                continue
            matches = [
                name for name, entry in definitions.items()
                if entry["enabled"] and entry["type"] == role
            ]
            if len(matches) != 1:
                raise ValueError(f"{node.node_type}_requires_one_{role}_node")
            selected[role] = matches[0]
        peers = selected
        skill_bindings = routes if skill_bindings is None else skill_bindings
    instance = node(
        node_id=node_id, options=options, suffix=suffix, actions=actions,
        peers=peers, skill_bindings=skill_bindings,
        endpoint=endpoint, site=site, env_id=env_id, journal=journal,
    )
    await instance._serve()
