"""Bounded DAG scheduling across explicitly bound nodes, with one Planner."""

import asyncio
from collections import deque

from wrs_agent.cache import PlanCache
from wrs_agent.environments.api import Environment
from wrs_agent.planner.api import PlanDecision, Planner, PlanRequest
from wrs_agent.schemas import (
    TERMINAL,
    ActionRequest,
    ControlRequest,
    Plan,
    Step,
    TaskControl,
    TaskRequest,
    new_id,
)
from wrs_agent.skills import SPECS, lookup_skills, validate_skill


def validate_plan(plan, capabilities=None, bindings=None):
    plan = Plan.model_validate_json(plan.model_dump_json())
    for step in plan.steps:
        validate_skill(step.skill, step.version, step.args)
        if capabilities is not None:
            node = (bindings or {}).get(step.skill, SPECS[step.skill].node)
            cap = capabilities.get(node)
            if cap is None or not set(SPECS[step.skill].required_capabilities).issubset(cap.skills):
                raise ValueError("unsupported_skill_on_current_node")
    return plan


class Runtime:
    def __init__(
        self,
        nodes: dict[str, Environment],
        bindings: dict[str, str],
        planner: Planner | None = None,
    ):
        self.nodes, self.bindings, self.planner = nodes, bindings, planner
        self.task_id = new_id()
        self.revision = 0
        self.state, self.reason = "IDLE", ""
        self.active_actions = {}
        self.results = {}
        self.workers = set()
        self.requests = {}
        self.closed = False
        self.node_locks = {node: asyncio.Lock() for node in nodes}
        self.planner_calls = 0
        self.planning = None
        self.planning_state = "IDLE"
        self.queued = []
        self.cache = PlanCache()
        self.recoveries = 0
        self.recovery_revision = None
        self.action_history = deque(maxlen=128)

    def snapshot(self):
        actions = dict(self.active_actions)
        return {
            "task_id": self.task_id,
            "revision": self.revision,
            "state": self.state,
            "action_id": next(iter(actions.values()), None),
            "active_actions": actions,
            "steps": dict(self.results),
            "reason": self.reason,
            "planning": self.planning_state,
            "planner_calls": self.planner_calls,
            "queued": len(self.queued),
            "cache_hit": self.cache.last_hit,
            "cache_hits": self.cache.hits,
            "cache_misses": self.cache.misses,
            "cache_reject_reason": self.cache.reject_reason,
            "semantic_shadow": self.cache.shadow_candidate,
            "recoveries": self.recoveries,
            "action_history": list(self.action_history),
        }

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.workers.add(task)
        task.add_done_callback(self.workers.discard)
        return task

    def _duplicate(self, key, data):
        if key not in self.requests:
            if len(self.requests) >= 4096:
                raise ValueError("request_capacity")
            return None
        original, result = self.requests[key]
        if data != original:
            raise ValueError("request_id_conflict")
        return result

    async def start(self, request: TaskRequest):
        duplicate = self._duplicate(request.request_id, request.model_dump())
        if duplicate is not None:
            return duplicate
        if self.closed or self.state not in {"IDLE", "SUCCEEDED", "FAILED", "CANCELLED"}:
            raise ValueError("task_busy_or_held")
        if self.planning and not self.planning.done():
            raise ValueError("planner_busy")
        plan = validate_plan(request.plan)
        self.revision += 1
        self.task_id = new_id()
        self.state, self.reason, self.results = "RUNNING", "", {}
        result = self.snapshot()
        self.requests[request.request_id] = (request.model_dump(), result)
        self._spawn(self._execute(plan, self.revision))
        return result

    async def enqueue(self, request: TaskRequest):
        data = {"kind": "enqueue", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        plan = validate_plan(request.plan)
        if len(self.queued) >= 16:
            raise ValueError("task_queue_full")
        if self.state != "RUNNING":
            raise ValueError("enqueue_requires_active_task")
        self.queued.append(plan)
        result = {"accepted": True, "queued": len(self.queued)}
        self.requests[request.request_id] = (data, result)
        return result

    async def goal(self, request):
        data = {"kind": "goal", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        if self.planner is None or (self.planning and not self.planning.done()):
            raise ValueError("planner_unavailable_or_busy")
        if self.state not in {"IDLE", "SUCCEEDED", "FAILED", "CANCELLED", "RUNNING"}:
            raise ValueError("task_held")
        # A new decision during execution is pending; it never implicitly resumes a held node.
        if self.state != "RUNNING":
            self.revision += 1
            self.task_id = new_id()
            self.reason, self.results = "", {}
        self.planning_state = "WAITING"
        result = {"accepted": True, "revision": self.revision}
        self.requests[request.request_id] = (data, result)
        self.planning = self._spawn(self._plan(request.goal, self.revision))
        return result

    async def _plan(self, goal, revision):
        was_running = self.state == "RUNNING"
        try:
            worlds = {name: await node.snapshot(control=True) for name, node in self.nodes.items()}
            capabilities = {name: await node.capabilities() for name, node in self.nodes.items()}
            request = PlanRequest(
                user_goal=goal,
                world={n: w.model_dump() for n, w in worlds.items()},
                skills=[
                    spec.model_dump() for spec in lookup_skills(goal, capabilities, self.bindings)
                ],
            )
            cached = self.cache.lookup(goal, worlds, capabilities, self.bindings)
            if cached is None:
                self.planner_calls += 1
                decision = await self.planner.plan(request)
            else:
                decision = PlanDecision(kind="execute", plan=cached)
            if revision != self.revision or self.closed:
                self.planning_state = "STALE"
                return
            for name, node in self.nodes.items():
                current = await node.snapshot(control=True)
                old = worlds[name]
                if (
                    current.boot_id != old.boot_id
                    or current.control_epoch != old.control_epoch
                    or current.admission != "OPEN"
                    or current.world_version != old.world_version
                ):
                    self.planning_state = "STALE"
                    return
            if revision != self.revision or self.closed:
                self.planning_state = "STALE"
                return
            if decision.kind != "execute":
                self.reason, self.planning_state = decision.text, decision.kind.upper()
                return
            if self.state == "RUNNING":
                # Do not silently substitute an active goal. Explicit hold/replace is required.
                self.planning_state = "REQUIRES_CONFIRMATION"
                return
            self.planning_state = "DONE"
            self.state = "RUNNING"
            validated = validate_plan(decision.plan, capabilities, self.bindings)
            await self._execute(validated, revision)
            if revision == self.revision:
                if self.state == "SUCCEEDED":
                    self.cache.remember(goal, validated, worlds, capabilities, self.bindings)
                else:
                    self.cache.invalidate(goal, self.reason or self.state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if revision != self.revision or self.closed:
                self.planning_state = "STALE"
                return
            self.planning_state = "FAILED"
            self.reason = str(exc)[:240]
            self.cache.invalidate(goal, self.reason)
            if not was_running:
                self.state = "FAILED"

    async def _execute(self, plan, revision):
        try:
            capabilities = {name: await node.capabilities() for name, node in self.nodes.items()}
            validate_plan(plan, capabilities, self.bindings)
        except Exception:
            if revision == self.revision:
                self.state, self.reason = "FAILED", "capability_preflight_failed"
            return
        done = {step.step_id: asyncio.Event() for step in plan.steps}
        outcomes = {}

        async def step_job(step):
            try:
                for dep in step.depends_on:
                    await done[dep].wait()
                    if outcomes.get(dep) != "SUCCEEDED":
                        outcomes[step.step_id] = "BLOCKED"
                        return
                node_name = self.bindings[step.skill]
                async with self.node_locks[node_name]:
                    if revision != self.revision or self.closed:
                        outcomes[step.step_id] = "STALE"
                        return
                    outcomes[step.step_id] = await self._run_step(step, node_name, revision)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                outcomes[step.step_id] = (
                    "UNKNOWN"
                    if (isinstance(exc, TimeoutError) or "UNKNOWN" in str(exc))
                    else "FAILED"
                )
                if revision == self.revision:
                    self.reason = str(exc)[:240]
                if outcomes[step.step_id] == "UNKNOWN":
                    try:
                        await self._fence(self.bindings[step.skill])
                    except Exception:
                        self.reason = "UNKNOWN: control_unconfirmed"
            finally:
                if revision == self.revision:
                    self.results.update(outcomes)
                done[step.step_id].set()

        # At most 12 jobs from the validated contract; no task per progress event.
        jobs = [
            asyncio.create_task(step_job(step))
            for step in sorted(plan.steps, key=lambda s: s.category != "interactive")
        ]
        try:
            await asyncio.gather(*jobs)
        finally:
            for job in jobs:
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
        if revision != self.revision or self.closed:
            return
        values = set(outcomes.values())
        self.state = (
            "UNKNOWN"
            if "UNKNOWN" in values
            else "FAILED"
            if "FAILED" in values
            else "CANCELLED"
            if values != {"SUCCEEDED"}
            else "SUCCEEDED"
        )
        if self.state == "SUCCEEDED":
            self.reason = ""
        if self.state == "SUCCEEDED" and self.queued and not self.closed:
            queued = self.queued.pop(0)
            self.revision += 1
            self.task_id = new_id()
            self.state, self.results = "RUNNING", {}
            self._spawn(self._execute(queued, self.revision))

    async def _run_step(self, step, node_name, revision):
        outcome, status, original = await self._attempt(step, node_name, revision)
        if (
            outcome != "FAILED"
            or status.verification != "FAIL"
            or status.reason not in {"localization_failed", "grasp_failed"}
            or "observe_once" not in SPECS[step.skill].recovery
            or self.recovery_revision == revision
            or revision != self.revision
            or self.closed
        ):
            return outcome
        node = self.nodes[node_name]
        authority = (original.boot_id, original.control_epoch)
        current = await node.snapshot()
        if not self._recoverable_state(step, current, authority, revision):
            return outcome
        capabilities = await node.capabilities()
        if "observe" not in capabilities.skills or step.skill not in capabilities.skills:
            return outcome
        self.recovery_revision = revision
        self.recoveries += 1
        observe = Step(step_id="recovery-" + new_id(), skill="observe")
        observed, _, _ = await self._attempt(observe, node_name, revision, authority)
        if observed != "SUCCEEDED":
            return observed
        current = await node.snapshot()
        if not self._recoverable_state(step, current, authority, revision):
            return "BLOCKED"
        # Exactly one new action, with a fresh ID and receiver-issued authorization.
        return (await self._attempt(step, node_name, revision, authority))[0]

    def _recoverable_state(self, step, world, authority, revision):
        return (
            revision == self.revision
            and not self.closed
            and (world.boot_id, world.control_epoch) == authority
            and world.admission == "OPEN"
            and world.stop_confirmed
            and world.active_action is None
            and world.held_object is None
            and world.objects.get(step.args.get("object")) not in {None, "gripper"}
            and (world.kinematics is None or world.kinematics.valid)
        )

    async def _attempt(self, step, node_name, revision, authority=None):
        node = self.nodes[node_name]
        world = await node.snapshot()
        if revision != self.revision or self.closed:
            return "STALE", None, world
        if authority is not None and (world.boot_id, world.control_epoch) != authority:
            return "STALE", None, world
        if world.admission != "OPEN":
            return "BLOCKED", None, world
        action = ActionRequest(
            action_id=new_id(),
            task_id=self.task_id,
            task_revision=revision,
            boot_id=world.boot_id,
            control_epoch=world.control_epoch,
            lease_id=world.lease_id,
            world_version=world.world_version,
            skill=step.skill,
            version=step.version,
            args=step.args,
        )
        self.active_actions[step.step_id] = action.action_id
        try:
            try:
                receipt = await node.submit(action)
            except TimeoutError:
                status = await node.status(action.action_id)
                if status is None:
                    raise ValueError("UNKNOWN: submit_outcome_unconfirmed") from None
            else:
                if not receipt.accepted:
                    raise ValueError(receipt.reason)
                status = receipt.status
            self.action_history.append(
                {
                    "action_id": action.action_id,
                    "skill": action.skill,
                    "revision": revision,
                }
            )
            async with asyncio.timeout(SPECS[step.skill].timeout):
                while status is not None and status.state not in TERMINAL:
                    if revision != self.revision or self.closed:
                        return "STALE", None, world
                    await asyncio.sleep(0.01)
                    status = await node.status(action.action_id)
            if status is None or status.state == "UNKNOWN":
                raise ValueError("UNKNOWN: missing_or_inconclusive_status")
            if status.state == "SUCCEEDED" and status.verification != "PASS":
                raise ValueError("UNKNOWN: missing_verification")
            if status.state == "FAILED":
                self.reason = status.reason
            return status.state, status, world
        finally:
            if self.active_actions.get(step.step_id) == action.action_id:
                self.active_actions.pop(step.step_id)

    async def _fence(self, node_name):
        node = self.nodes[node_name]
        for _ in range(3):
            world = await node.snapshot(control=True)
            result = await node.hold(
                ControlRequest(
                    interrupt_id=new_id(), boot_id=world.boot_id, control_epoch=world.control_epoch
                )
            )
            if result.accepted:
                return result
            if result.reason != "stale_control":
                raise ValueError(result.reason)
        raise ValueError("concurrent_control_change")

    async def hold(self, request: TaskControl):
        data = {"kind": "hold", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        if request.replacement is not None:
            raise ValueError("hold_does_not_accept_replacement")
        self.revision += 1
        self.state = "HELD"
        self.queued.clear()
        result = {"revision": self.revision, "state": "HELD", "phase": "PENDING"}
        self.requests[request.request_id] = (data, result)
        receipts = await asyncio.gather(
            *(self._fence(name) for name in self.nodes), return_exceptions=True
        )
        wrs = receipts[list(self.nodes).index("wrs")]
        if isinstance(wrs, Exception):
            result.update({"accepted": False, "phase": "UNKNOWN"})
        else:
            result.update(wrs.model_dump())
            result["accepted"] = all(not isinstance(r, Exception) for r in receipts)
        return result

    async def replace(self, request: TaskControl):
        data = {"kind": "replace", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        if request.replacement is None:
            raise ValueError("replacement_required")
        plan = validate_plan(request.replacement)
        if self.state != "HELD":
            raise ValueError("explicit_hold_required_before_replacement")
        self.revision += 1
        revision = self.revision
        self.state = "RESUMING"
        result = self.snapshot()
        self.requests[request.request_id] = (data, result)
        self._spawn(self._resume_and_execute(plan, revision))
        return result

    async def _resume_and_execute(self, plan, revision):
        try:
            for node_name in {self.bindings[s.skill] for s in plan.steps}:
                node = self.nodes[node_name]
                async with asyncio.timeout(3):
                    while True:
                        world = await node.snapshot(control=True)
                        if revision != self.revision or self.closed:
                            return
                        if world.admission == "UNKNOWN":
                            raise ValueError("UNKNOWN: stop_unconfirmed")
                        if world.stop_confirmed and world.active_action is None:
                            break
                        await asyncio.sleep(0.01)
                result = await node.resume(
                    ControlRequest(
                        interrupt_id=new_id(),
                        boot_id=world.boot_id,
                        control_epoch=world.control_epoch,
                        world_version=world.world_version,
                    )
                )
                if not result.accepted:
                    raise ValueError(result.reason)
            if revision != self.revision or self.closed:
                return
            self.state, self.results = "RUNNING", {}
            await self._execute(plan, revision)
        except Exception as exc:
            if revision == self.revision:
                self.state, self.reason = "UNKNOWN", str(exc)[:240]

    async def close(self):
        self.closed = True
        await self.hold(TaskControl(request_id=new_id()))
        if self.planning and not self.planning.done():
            self.planning.cancel()
        await asyncio.gather(*list(self.workers), return_exceptions=True)
