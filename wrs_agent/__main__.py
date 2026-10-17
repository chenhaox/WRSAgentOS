"""Command-line entry; built-in nodes and local Node subclasses share serve_node()."""

import argparse
import asyncio
import json

from wrs_agent.nodes.serve import NODES, node_options, serve_node
from wrs_agent.processes import LocalStack


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=[*NODES, "launch"])
    parser.add_argument("--endpoint", default="tcp/127.0.0.1:7447")
    parser.add_argument("--site", default="local")
    parser.add_argument("--env-id", default="arm01")
    parser.add_argument("--journal")
    parser.add_argument("--node-id")
    parser.add_argument(
        "--bindings", help="Explicit deployment file; launch uses the default profile",
    )
    parser.add_argument("--suffix", help="This node's service suffix; defaults to -<node-id>")
    parser.add_argument("--actions", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--peers", help="JSON object mapping required roles to node IDs")
    parser.add_argument("--skill-bindings", help="JSON object selecting skill providers")
    parser.add_argument("--options", help="One node's JSON options; cannot mix with backend flags")
    parser.add_argument("--scene", help="Initial WRS scene TOML (world coordinates)")
    parser.add_argument(
        "--live-model", action="store_true", default=None,
        help="Enable LLMClient using LLM_* environment variables; otherwise planning is disabled",
    )
    parser.add_argument("--backend", choices=["mock", "wrs"])
    parser.add_argument("--tts-backend", choices=["mock", "qwen"])
    parser.add_argument("--tts-prepare", dest="tts_prepared_texts", action="append")
    parser.add_argument("--asr-backend", choices=["mock", "qwen"])
    parser.add_argument(
        "--asr-script", action="append",
        help="offline fixture transcript, returned once per press; no microphone is opened",
    )
    parser.add_argument("--asr-vocabulary", action="append")
    parser.add_argument("--duration", type=float)
    parser.add_argument("--fault")
    parser.add_argument("--ui-port", type=int, help="Dashboard port of the ui node (8010)")
    parser.add_argument(
        "--ui-viewer", action=argparse.BooleanOptionalAction, default=None,
        help="Let the ui node host the WRS 3D page itself (default on)",
    )
    parser.add_argument("--ui-viewer-port", type=int, help="WRS 3D page port (8000)")
    args = parser.parse_args()
    values = vars(args)
    for name in ("peers", "skill_bindings"):
        raw = values[name]
        if raw is None:
            continue
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in parsed.items()
            ):
                raise ValueError("expected a JSON object of strings")
            values[name] = parsed
        except ValueError as exc:
            parser.error(f"--{name.replace('_', '-')}: {exc}")
    if args.role == "launch" and any(
        values[name] is not None
        for name in ("suffix", "actions", "peers", "skill_bindings", "node_id")
    ):
        parser.error("single-node parameters do not apply to launch")
    legacy_fields = {name for node in NODES.values() for name in node.launch_options.values()}
    if args.options is not None:
        if args.role == "launch" or any(values[name] is not None for name in legacy_fields):
            parser.error("--options applies to one node and cannot mix with backend flags")
        try:
            options = NODES[args.role].options_type.model_validate_json(args.options)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    else:
        try:
            checked = {
                role: node.options_type.model_validate(node_options(role, values))
                for role, node in NODES.items()
            }
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        options = checked.get(args.role)

    if args.role == "launch":
        kwargs = {key: values[key] for key in legacy_fields if values[key] is not None}
        async with LocalStack(
            port=7447, site=args.site, env_id=args.env_id, bindings=args.bindings, **kwargs,
        ) as stack:
            print(
                f"ready {stack.backend} stack: {stack.endpoint} {stack.env_id}; Ctrl+C to stop",
                flush=True,
            )
            await asyncio.Event().wait()
    else:
        await serve_node(
            NODES[args.role], options=options, node_id=args.node_id, bindings=args.bindings,
            suffix=args.suffix, actions=args.actions, peers=args.peers,
            skill_bindings=args.skill_bindings,
            endpoint=args.endpoint, site=args.site, env_id=args.env_id, journal=args.journal,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
