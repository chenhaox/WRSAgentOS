"""Foreground supervision; Mock or WRS virtual capability nodes, hardware cannot be enabled."""

import argparse
import asyncio
import os

from wrs_agent.bindings import load_bindings
from wrs_agent.environments.api import ActionClient
from wrs_agent.environments.mock import make_mock_environment
from wrs_agent.nodes.agent import register_runtime
from wrs_agent.nodes.environment import register_actions
from wrs_agent.nodes.speech import register_speech
from wrs_agent.nodes.tts import make_mock_tts
from wrs_agent.planner.model import ModelPlanner
from wrs_agent.planner.providers.mock import MockClient
from wrs_agent.processes import InstanceLock, LocalStack
from wrs_agent.runtime import Runtime
from wrs_agent.schemas import Empty
from wrs_agent.transport import Transport


async def node(args):
    suffixes, bindings = load_bindings()
    target = args.env_id + (
        suffixes["tts"] if args.role == "tts" else "-voice" if args.role == "voice" else ""
    )
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
            tts = connect(args.env_id + suffixes["tts"])
            model = MockClient(
                '{"kind":"execute","plan":{"steps":['
                '{"step_id":"home","skill":"move_named_pose","args":{"pose":"home"}}]}}',
                deferred=args.deferred_planner,
            )
            owner = Runtime(
                {"wrs": ActionClient(transport), "tts": ActionClient(tts)},
                bindings,
                planner=ModelPlanner(model),
            )
            register_runtime(transport, owner)
            if args.deferred_planner:

                async def release(payload):
                    Empty.model_validate(payload)
                    model.gate.set()
                    return {"released": True, "provider": "mock"}

                transport.register_handler("request/test/planner/release", release, control=True)
        else:
            wrs_bus = connect(args.env_id)
            tts_bus = connect(args.env_id + suffixes["tts"])
            register_speech(transport, ActionClient(wrs_bus), ActionClient(tts_bus), wrs_bus)

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
    parser.add_argument("role", choices=["environment", "tts", "voice", "runtime", "launch"])
    parser.add_argument("--endpoint", default="tcp/127.0.0.1:7447")
    parser.add_argument("--site", default="local")
    parser.add_argument("--env-id", default="arm01")
    parser.add_argument("--journal")
    parser.add_argument("--backend", choices=["mock", "wrs_virtual"], default="mock")
    parser.add_argument("--duration", type=float, default=0.4)
    parser.add_argument(
        "--deferred-planner",
        action="store_true",
        help="offline test fixture; no GLM request is sent",
    )
    parser.add_argument(
        "--fault", choices=["grasp", "localization", "unknown", "inconclusive", "stop_unknown"]
    )
    args = parser.parse_args()
    if args.duration <= 0 or args.duration > 30:
        parser.error("duration must be in (0, 30]")
    if args.role == "launch":
        async with LocalStack(
            duration=args.duration, port=7447, voice=True, backend=args.backend
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
