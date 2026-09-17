"""Foreground supervision; Mock or WRS virtual capability nodes, hardware cannot be enabled."""

import argparse
import asyncio
import os

from wrs_agent.bindings import load_nodes
from wrs_agent.environments.mock import make_mock_environment
from wrs_agent.nodes.actions import register_actions
from wrs_agent.nodes.agent import register_runtime
from wrs_agent.nodes.api import ActionClient, RobotClient
from wrs_agent.nodes.speech import register_speech
from wrs_agent.nodes.tts import make_mock_tts
from wrs_agent.planner.model import ModelPlanner
from wrs_agent.planner.providers.mock import MockClient
from wrs_agent.processes import InstanceLock, LocalStack
from wrs_agent.registry import NodeRegistry, register_node
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import Empty
from wrs_agent.transport import Transport


async def node(args):
    definitions, bindings = load_nodes(args.bindings)
    node_type = {"environment": "wrs", "runtime": "agent"}.get(args.role, args.role)
    node_id = args.node_id or node_type
    definition = definitions.get(node_id)
    if not definition or definition["type"] != node_type or not definition["enabled"]:
        raise ValueError("node_not_configured")
    target = args.env_id + definition["suffix"]
    scope = "action" if args.role in {"environment", "tts"} else args.role
    lock = InstanceLock(f"{args.site}-{target}-{scope}")
    journal = args.journal or f".local/state/{args.site}-{target}.sqlite3"
    buses = []
    owner = None
    model = None
    done = asyncio.Event()
    try:

        def connect(target_id):
            bus = Transport(
                args.endpoint,
                args.site,
                target_id,
                os.environ.get("WRS_AGENT_TOKEN", ""),
                args.role,
            )
            buses.append(bus)
            return bus

        transport = connect(target)
        if args.role in {"environment", "tts"}:
            if args.role == "environment" and args.backend == "wrs_virtual":
                from wrs_agent.environments.wrs import make_wrs_environment

                owner = await make_wrs_environment(journal, duration=args.duration)
            elif args.role == "environment":
                owner = make_mock_environment(journal, duration=args.duration, fault=args.fault)
            else:
                owner = make_mock_tts(journal, duration=args.duration)
            register_actions(transport, owner)
        elif args.role == "runtime":
            if args.model_provider == "glm":
                from wrs_agent.planner.providers.glm import GLMClient, GLMConfig

                model = GLMClient(GLMConfig.from_env(), live_model=args.live_model)
            else:
                model = MockClient(
                    '{"kind":"execute","plan":{"steps":['
                    '{"step_id":"home","skill":"move_named_pose","args":{"pose":"home"}}]}}',
                    deferred=args.deferred_planner,
                )
            registry = NodeRegistry(
                {
                    name: connect(args.env_id + d["suffix"])
                    for name, d in definitions.items()
                    if d["enabled"]
                },
                definitions,
                bindings,
            )
            clients = {
                name: (
                    RobotClient(registry.buses[name])
                    if d["type"] == "wrs"
                    else ActionClient(registry.buses[name])
                )
                for name, d in definitions.items()
                if d["actions"] and d["enabled"]
            }
            owner = Runtime(
                clients,
                bindings,
                planner=ModelPlanner(model),
                registry=registry,
            )
            register_runtime(transport, owner)
            if args.deferred_planner:

                async def release(payload):
                    Empty.model_validate(payload)
                    model.gate.set()
                    return {"released": True, "provider": "mock"}

                transport.register_handler("request/test/planner/release", release, control=True)
        else:

            def role_bus(role):
                matches = [d for d in definitions.values() if d["type"] == role and d["enabled"]]
                if len(matches) != 1:
                    raise ValueError("voice_requires_one_provider_per_role")
                return connect(args.env_id + matches[0]["suffix"])

            register_speech(
                transport,
                RobotClient(role_bus("wrs")),
                ActionClient(role_bus("tts")),
                role_bus("agent"),
            )

        info = register_node(
            transport,
            node_id,
            node_type,
            owner if args.role in {"environment", "tts"} else None,
        )

        if args.role == "runtime":
            owner.registry.local[node_id] = info

        async def shutdown(payload):
            Empty.model_validate(payload)
            done.set()
            return {"stopping": True}

        transport.register_handler(f"request/{args.role}/shutdown", shutdown, control=True)
        print(f"ready {args.role} {target}", flush=True)
        await done.wait()
    finally:
        try:
            if owner:
                await owner.close()
            if model:
                await model.aclose()
        finally:
            for bus in buses:
                await bus.close()
            lock.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "role", choices=["wrs", "agent", "environment", "tts", "voice", "runtime", "launch"]
    )
    parser.add_argument("--endpoint", default="tcp/127.0.0.1:7447")
    parser.add_argument("--site", default="local")
    parser.add_argument("--env-id", default="arm01")
    parser.add_argument("--journal")
    parser.add_argument("--node-id")
    parser.add_argument("--bindings")
    parser.add_argument("--model-provider", choices=["mock", "glm"], default="mock")
    parser.add_argument("--live-model", action="store_true")
    parser.add_argument("--backend", choices=["mock", "wrs_virtual"], default="mock")
    parser.add_argument("--duration", type=float, default=0.4)
    parser.add_argument(
        "--deferred-planner",
        action="store_true",
        help="offline test fixture; no GLM request is sent",
    )
    parser.add_argument(
        "--fault",
        choices=[
            "grasp",
            "grasp_once",
            "localization",
            "localization_once",
            "unknown",
            "inconclusive",
            "stop_unknown",
        ],
    )
    args = parser.parse_args()
    args.role = {"wrs": "environment", "agent": "runtime"}.get(args.role, args.role)
    if args.model_provider == "glm" and (not args.live_model or args.deferred_planner):
        parser.error("GLM requires --live-model and cannot use --deferred-planner")
    if args.duration <= 0 or args.duration > 30:
        parser.error("duration must be in (0, 30]")
    if args.role == "launch":
        async with LocalStack(
            duration=args.duration,
            port=7447,
            voice=True,
            backend=args.backend,
            model_provider=args.model_provider,
            live_model=args.live_model,
            bindings=args.bindings,
        ) as stack:
            print(
                f"ready {args.backend} stack: {stack.endpoint} {stack.env_id}; Ctrl+C to stop",
                flush=True,
            )
            await asyncio.Event().wait()
    else:
        await node(args)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
