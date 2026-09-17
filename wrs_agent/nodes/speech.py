"""Trusted event replay entry. No microphone or ASR is claimed by this node."""

from wrs_agent.policy import decide_event
from wrs_agent.schemas import ControlRequest, Empty, Interaction


def register_speech(bus, wrs, tts, agent_bus):
    processed = {}

    async def handle(payload):
        event = Interaction.model_validate(payload)
        if event.event_id in processed:
            original, result = processed[event.event_id]
            if original != event:
                raise ValueError("event_id_conflict")
            return result
        if len(processed) >= 4096:
            raise ValueError("event_capacity")
        disposition = decide_event(event)
        result = {"disposition": disposition}
        processed[event.event_id] = (event, result)
        if disposition in {"hold", "cancel_tts"}:
            node = wrs if disposition == "hold" else tts
            world = await node.snapshot(control=True)
            if disposition == "cancel_tts" and world.active_action is None:
                result["effect"] = "no_active_tts"
            else:
                request = ControlRequest(
                    interrupt_id=event.event_id,
                    boot_id=world.boot_id,
                    control_epoch=world.control_epoch,
                    action_id=world.active_action if disposition == "cancel_tts" else None,
                )
                receipt = await (
                    node.hold(request) if disposition == "hold" else node.cancel(request)
                )
                result.update(receipt.model_dump())
        elif disposition == "answer":
            result["task"] = await agent_bus.request("request/task/status", {})
        elif disposition in {"update", "enqueue"}:
            # Structured replay carries an explicit plan, never inferred coordinates.
            if event.plan is None:
                result.update(disposition="clarify", reason="explicit_plan_required_in_replay")
            elif disposition == "enqueue":
                result["task"] = await agent_bus.request(
                    "request/task/enqueue",
                    {"request_id": event.event_id, "plan": event.plan.model_dump()},
                )
            else:
                await agent_bus.request(
                    "request/task/hold", {"request_id": event.event_id + "-hold"}, control=True
                )
                result["task"] = await agent_bus.request(
                    "request/task/replace",
                    {"request_id": event.event_id, "replacement": event.plan.model_dump()},
                    control=True,
                )
        bus.publish("events/interaction", {"event_id": event.event_id, **result})
        return result

    async def health(payload):
        Empty.model_validate(payload)
        return {"backend": "event_replay", "processed": len(processed)}

    async def control_event(payload):
        event = Interaction.model_validate(payload)
        if event.kind not in {"stop", "barge_in"}:
            raise ValueError("control_intent_required")
        return await handle(payload)

    async def ordinary_event(payload):
        event = Interaction.model_validate(payload)
        if event.kind in {"stop", "barge_in"}:
            raise ValueError("use_voice_control_endpoint")
        return await handle(payload)

    bus.register_handler("request/voice/control", control_event, control=True)
    bus.register_handler("request/voice/event", ordinary_event)
    bus.register_handler("request/health", health)
