"""Bounded DAG scheduling across explicitly bound nodes, with one Planner."""

import asyncio
from collections import deque
from dataclasses import dataclass

from wrs_agent.cache import PlanCache
from wrs_agent.nodes.actions import ActionProvider, action_request
from wrs_agent.planner import PlanDecision, Planner, PlanRequest
from wrs_agent.schemas import (
    TERMINAL,
    ControlReceipt,
    ControlRequest,
    Plan,
    Step,
    TaskControl,
    TaskRequest,
    new_id,
)
from wrs_agent.skills import SKILLS, lookup_skills, validate_skill


def validate_plan(plan, capabilities=None, bindings=None):
    plan = Plan.model_validate_json(plan.model_dump_json())
    for step in plan.steps:
        validate_skill(step.skill, step.version, step.args)
        if capabilities is not None:
            node = (bindings or {}).get(step.skill)
            cap = capabilities.get(node)
            if cap is None or not set(SKILLS[step.skill].spec.required_capabilities).issubset(
                cap.skills
            ):
                raise ValueError("unsupported_skill_on_current_node")
    return plan


@dataclass(frozen=True)
class _Task:
    """One execution definition; JSON also freezes nested arguments and dependencies."""

    task_id: str
    plan_json: str
    supersedes: str | None = None

    @classmethod
    def create(cls, plan, supersedes=None):
        return cls(new_id(), validate_plan(plan).model_dump_json(), supersedes)

    def plan(self):
        return Plan.model_validate_json(self.plan_json)


class Runtime:
    def __init__(
        self,
        nodes: dict[str, ActionProvider],
        bindings: dict[str, str],
        planner: Planner | None = None,
        *,
        registry=None,
    ):
        self.nodes, self.bindings, self.planner = nodes, bindings, planner
        self.registry = registry
        self.task = None
        self.hold_accepted = False
        self.state, self.reason = "IDLE", ""
        self.active_actions = {}
        self.results = {}
        self.workers = set()
        self.requests = {}
        self.closed = False
        self.node_locks = {node: asyncio.Lock() for node in nodes}
        self.planner_calls = 0
        self.planning = None
        self.planning_request_id = None
        self.planning_state = "IDLE"
        self.queued = []
        self.cache = PlanCache()
        self.recoveries = 0
        self.recovered_task = None
        self.action_history = deque(maxlen=128)

    @property
    def task_id(self):
        return self.task.task_id if self.task else None

    def _active(self, task):
        return self.task is task and not self.closed and self.state in {"RUNNING", "RESUMING"}

    def _planning_current(self, request_id):
        return self.planning_request_id == request_id and not self.closed

    def _activate(self, task, state="RUNNING"):
        self.planning_request_id = None
        self.planning_state = "IDLE"
        self.task = task
        self.state, self.reason, self.results = state, "", {}
        self.active_actions.clear()
        self.hold_accepted = False

    def snapshot(self):
        actions = dict(self.active_actions)
        return {
            "nodes": self.registry.snapshot() if self.registry else {},
            "task_id": self.task_id,
            "revision": 0,  # Deprecated wire field; task IDs identify executions.
            "supersedes": self.task.supersedes if self.task else None,
            "state": self.state,
            "action_id": next(iter(actions.values()), None),
            "active_actions": actions,
            "steps": dict(self.results),
            "reason": self.reason,
            "planning": self.planning_state,
            "planning_request_id": self.planning_request_id,
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
        task = _Task.create(request.plan)
        self._activate(task)
        result = self.snapshot()
        self.requests[request.request_id] = (request.model_dump(), result)
        self._spawn(self._execute(task))
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
        task = _Task.create(plan)
        self.queued.append(task)
        result = {"accepted": True, "task_id": task.task_id, "queued": len(self.queued)}
        self.requests[request.request_id] = (data, result)
        return result

    async def goal(self, request):
        data = {"kind": "goal", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        if self.planner is None or (self.planning and not self.planning.done()):
            raise ValueError("planner_unavailable_or_busy")
        if self.closed or self.state not in {"IDLE", "SUCCEEDED", "FAILED", "CANCELLED", "RUNNING"}:
            raise ValueError("task_held")
        # Planning requests have an ID; a Task exists only after an executable plan is valid.
        was_running = self.state == "RUNNING"
        if not was_running:
            self.reason = ""
        self.planning_request_id = request.request_id
        self.planning_state = "WAITING"
        result = {"accepted": True, "request_id": request.request_id, "revision": 0}
        self.requests[request.request_id] = (data, result)
        self.planning = self._spawn(self._plan(request.goal, request.request_id, was_running))
        return result

    async def _state(self, node, *, control=False):
        return await node.snapshot(control=control)

    async def _capabilities(self, names=None):
        names = self.nodes if names is None else names
        if self.registry:
            await self.registry.refresh(names)
        result = {}
        for name in names:
            if self.registry:
                if not self.registry.snapshot()[name]["ready"]:
                    continue
                result[name] = await self.registry.capabilities(name, self.nodes[name])
            else:
                result[name] = await self.nodes[name].capabilities()
        return result

    async def _plan_valid(self, request_id, worlds):
        for name, old in worlds.items():
            if not self._planning_current(request_id):
                return False
            current = await self._state(self.nodes[name], control=True)
            if not self._planning_current(request_id):
                return False
            if (
                current.boot_id != old.boot_id
                or current.control_epoch != old.control_epoch
                or current.admission != "OPEN"
                or current.world_version != old.world_version
            ):
                self.planning_state = "STALE"
                return False
        return self._planning_current(request_id)

    async def _plan(self, goal, request_id, was_running):
        worlds = {}
        try:
            capabilities = await self._capabilities()
            worlds = {
                name: await self._state(self.nodes[name], control=True) for name in capabilities
            }
            if not self._planning_current(request_id):
                return
            request = PlanRequest(
                user_goal=goal,
                world={n: w.model_dump(exclude={"lease_id"}) for n, w in worlds.items()},
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
            if not await self._plan_valid(request_id, worlds):
                return
            if decision.kind != "execute":
                self.reason, self.planning_state = decision.text, decision.kind.upper()
                return
            if was_running:
                self.planning_state = "REQUIRES_CONFIRMATION"
                return
            validated = validate_plan(decision.plan, capabilities, self.bindings)
            task = _Task.create(validated)
            self._activate(task)
            self.planning_state = "DONE"
            await self._execute(task)
            if self.task is task and not self.closed:
                if self.state == "SUCCEEDED":
                    self.cache.remember(goal, validated, worlds, capabilities, self.bindings)
                else:
                    self.cache.invalidate(goal, self.reason or self.state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._planning_current(request_id):
                return
            # A late provider error after a physical stop is obsolete too.
            try:
                valid = await self._plan_valid(request_id, worlds)
            except Exception:
                valid = False
            if not self._planning_current(request_id):
                return
            if not valid:
                self.planning_state = "STALE"
                return
            self.planning_state = "FAILED"
            self.reason = str(exc)[:240]
            self.cache.invalidate(goal, self.reason)
            if not was_running:
                self.state = "FAILED"

    async def _execute(self, task):
        if not self._active(task):
            return
        plan = task.plan()
        try:
            capabilities = await self._capabilities({self.bindings[s.skill] for s in plan.steps})
            validate_plan(plan, capabilities, self.bindings)
            if self.registry:
                for step in plan.steps:
                    self.registry.node_for(step.skill)
        except Exception:
            if self._active(task):
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
                    if not self._active(task):
                        outcomes[step.step_id] = "STALE"
                        return
                    outcomes[step.step_id] = await self._run_step(step, node_name, task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                outcomes[step.step_id] = (
                    "UNKNOWN"
                    if (isinstance(exc, TimeoutError) or "UNKNOWN" in str(exc))
                    else "FAILED"
                )
                if self._active(task):
                    self.reason = str(exc)[:240]
                if self._active(task) and outcomes[step.step_id] == "UNKNOWN":
                    try:
                        await self._fence(self.bindings[step.skill], task)
                    except Exception:
                        if self._active(task):
                            self.reason = "UNKNOWN: control_unconfirmed"
            finally:
                if self._active(task):
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
        if not self._active(task):
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
            self._activate(queued)
            self._spawn(self._execute(queued))

    async def _run_step(self, step, node_name, task):
        outcome, status, original = await self._attempt(step, node_name, task)
        if (
            outcome != "FAILED"
            or status.verification != "FAIL"
            or status.reason not in {"localization_failed", "grasp_failed"}
            or "observe_once" not in SKILLS[step.skill].spec.recovery
            or self.recovered_task == task.task_id
            or not self._active(task)
            or self.closed
        ):
            return outcome
        node = self.nodes[node_name]
        authority = (original.boot_id, original.control_epoch)
        current = await node.snapshot()
        if not self._recoverable_state(step, current, authority, task):
            return outcome
        capabilities = await node.capabilities()
        if "observe" not in capabilities.skills or step.skill not in capabilities.skills:
            return outcome
        if not self._active(task):
            return "STALE"
        self.recovered_task = task.task_id
        self.recoveries += 1
        observe = Step(step_id="recovery-" + new_id(), skill="observe")
        observed, _, _ = await self._attempt(observe, node_name, task, authority)
        if observed != "SUCCEEDED":
            return observed
        current = await node.snapshot()
        if not self._recoverable_state(step, current, authority, task):
            return "BLOCKED"
        # Exactly one new action, with a fresh ID and receiver-issued authorization.
        return (await self._attempt(step, node_name, task, authority))[0]

    def _recoverable_state(self, step, world, authority, task):
        return (
            self._active(task)
            and not self.closed
            and (world.boot_id, world.control_epoch) == authority
            and world.admission == "OPEN"
            and world.stop_confirmed
            and world.active_action is None
            and world.data.held_object is None
            and world.data.objects.get(step.args.get("object")) not in {None, "gripper"}
            and (world.data.kinematics is None or world.data.kinematics.valid)
        )

    async def _attempt(self, step, node_name, task, authority=None):
        node = self.nodes[node_name]
        if self.registry:
            await self.registry.refresh([node_name])
            if self.registry.node_for(step.skill) != node_name:
                raise ValueError("node_binding_changed")
        world = await node.context()
        if self.registry:
            self.registry.check_instance(node_name, world.boot_id)
        if not self._active(task):
            return "STALE", None, world
        if authority is not None and (world.boot_id, world.control_epoch) != authority:
            return "STALE", None, world
        if world.admission != "OPEN":
            return "BLOCKED", None, world
        action = action_request(
            world,
            step.skill,
            step.args,
            task_id=task.task_id,
            version=step.version,
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
            if not self._active(task):
                return "STALE", None, world
            self.action_history.append(
                {
                    "action_id": action.action_id,
                    "skill": action.skill,
                    "task_id": task.task_id,
                    "revision": 0,
                }
            )
            async with asyncio.timeout(SKILLS[step.skill].spec.timeout):
                while status is not None and status.state not in TERMINAL:
                    if not self._active(task):
                        return "STALE", None, world
                    await asyncio.sleep(0.01)
                    status = await node.status(action.action_id)
            if not self._active(task):
                return "STALE", None, world
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

    async def _robot_controls(self, node_name):
        if self.registry:
            return self.registry.definitions[node_name]["type"] == "wrs"
        return (await self.nodes[node_name].capabilities()).robot_controls

    async def _fence(self, node_name, task=None):
        node = self.nodes[node_name]
        robot = await self._robot_controls(node_name)
        for _ in range(3):
            world = await self._state(node, control=True)
            if task is not None and self.task is not task:
                raise ValueError("stale_task")
            if not robot and world.active_action is None:
                return ControlReceipt(
                    accepted=world.stop_confirmed,
                    control_epoch=world.control_epoch,
                    phase="STOPPED" if world.stop_confirmed else "UNKNOWN",
                )
            request = ControlRequest(
                interrupt_id=new_id(),
                boot_id=world.boot_id,
                control_epoch=world.control_epoch,
                action_id=world.active_action if not robot else None,
            )
            result = await (node.control("hold", request) if robot else node.cancel(request))
            if result.accepted:
                return result
            if result.reason != "stale_control":
                raise ValueError(result.reason)
        raise ValueError("concurrent_control_change")

    def _control_target(self, request):
        if self.closed or request.task_id != self.task_id or self.task is None:
            raise ValueError("stale_task")
        return self.task

    async def hold(self, request: TaskControl):
        data = {"kind": "hold", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        task = self._control_target(request)
        if request.replacement is not None:
            raise ValueError("hold_does_not_accept_replacement")
        if self.state not in {"RUNNING", "RESUMING", "HELD", "UNKNOWN"}:
            raise ValueError("task_not_active")
        self.planning_request_id = None
        if self.planning_state == "WAITING":
            self.planning_state = "STALE"
        self.state = "HELD"
        self.hold_accepted = False
        self.queued.clear()
        result = {"task_id": task.task_id, "revision": 0, "state": "HELD", "phase": "PENDING"}
        self.requests[request.request_id] = (data, result)
        receipts = await asyncio.gather(
            *(self._fence(name, task) for name in self.nodes), return_exceptions=True
        )
        accepted = all(not isinstance(r, Exception) and r.accepted for r in receipts)
        phase = (
            "UNKNOWN"
            if not accepted
            or any(not isinstance(r, Exception) and r.phase == "UNKNOWN" for r in receipts)
            else "STOPPING"
            if any(r.phase == "STOPPING" for r in receipts)
            else "STOPPED"
        )
        if self.task is task:
            self.hold_accepted = accepted and phase != "UNKNOWN"
        result.update(accepted=accepted, phase=phase)
        return result

    async def replace(self, request: TaskControl):
        data = {"kind": "replace", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        old = self._control_target(request)
        if request.replacement is None:
            raise ValueError("replacement_required")
        task = _Task.create(request.replacement, supersedes=old.task_id)
        if self.state != "HELD":
            raise ValueError("explicit_hold_required_before_replacement")
        if not self.hold_accepted:
            raise ValueError("hold_pending_or_unconfirmed")
        self._activate(task, "RESUMING")
        result = self.snapshot()
        self.requests[request.request_id] = (data, result)
        self._spawn(self._resume_and_execute(task))
        return result

    async def _resume_and_execute(self, task):
        plan = task.plan()
        try:
            stopped = {}
            # A replacement cannot sidestep an unconfirmed stop on another old resource.
            for node_name, node in self.nodes.items():
                async with asyncio.timeout(3):
                    while True:
                        world = await self._state(node, control=True)
                        if not self._active(task):
                            return
                        if world.admission == "UNKNOWN":
                            raise ValueError("UNKNOWN: stop_unconfirmed")
                        if world.stop_confirmed and world.active_action is None:
                            break
                        await asyncio.sleep(0.01)
                stopped[node_name] = world
            for node_name in {self.bindings[s.skill] for s in plan.steps}:
                node, world = self.nodes[node_name], stopped[node_name]
                robot = await self._robot_controls(node_name)
                if not self._active(task):
                    return
                if not robot:
                    if world.admission != "OPEN":
                        raise ValueError("node_not_ready")
                    continue
                result = await node.control(
                    "resume",
                    ControlRequest(
                        interrupt_id=new_id(),
                        boot_id=world.boot_id,
                        control_epoch=world.control_epoch,
                        world_version=world.world_version,
                    ),
                )
                if not result.accepted:
                    raise ValueError(result.reason)
            if not self._active(task):
                return
            self.state, self.results = "RUNNING", {}
            await self._execute(task)
        except Exception as exc:
            if self._active(task):
                self.state, self.reason = "UNKNOWN", str(exc)[:240]

    async def close(self):
        self.closed = True
        self.planning_request_id = None
        self.state = "HELD"
        self.queued.clear()
        await asyncio.gather(*(self._fence(name) for name in self.nodes), return_exceptions=True)
        if self.planning and not self.planning.done():
            self.planning.cancel()
        await asyncio.gather(*list(self.workers), return_exceptions=True)
