"""Bounded DAG scheduling across explicitly bound nodes, with one Planner."""

import asyncio
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal

from wrs_agent.cache import PlanCache
from wrs_agent.errors import AgentError, error_info, from_exception
from wrs_agent.nodes.action_rpc import ActionProvider
from wrs_agent.plan_validation import validate_plan
from wrs_agent.planner import PlanDecision, Planner, PlanRequest, token_counts
from wrs_agent.schemas import (
    TERMINAL,
    ActionState,
    ControlRequest,
    ErrorInfo,
    GoalState,
    Plan,
    SceneData,
    Step,
    TaskCancelRequest,
    TaskRequest,
    TaskState,
    new_id,
)
from wrs_agent.skills import lookup_skills, require_contract, resolve_routes, skill_specs


@dataclass(frozen=True)
class TaskExecution:
    """One execution definition; JSON also freezes nested arguments and dependencies."""

    task_id: str
    plan_json: str
    bindings: dict = field(default_factory=dict, compare=False)
    specs: dict = field(default_factory=dict, compare=False)
    boots: dict = field(default_factory=dict, compare=False)
    clients: dict = field(default_factory=dict, compare=False, repr=False)
    robot_controls: dict = field(default_factory=dict, compare=False)
    authorities: dict = field(default_factory=dict, compare=False)
    actions: dict = field(default_factory=dict, compare=False)
    outputs: dict = field(default_factory=dict, compare=False)
    release_epochs: dict = field(default_factory=dict, compare=False)
    submitting: set = field(default_factory=set, compare=False)
    submissions_idle: asyncio.Event = field(default_factory=asyncio.Event, compare=False)

    @classmethod
    def create(cls, plan, *, bindings=None, specs=None, boots=None, clients=None, controls=None):
        task = cls(
            new_id(), Plan.model_validate_json(plan.model_dump_json()).model_dump_json(),
            dict(bindings or {}),
            {n: s.model_copy(deep=True) for n, s in (specs or {}).items()},
            dict(boots or {}), dict(clients or {}), dict(controls or {}),
        )
        task.submissions_idle.set()
        return task

    def plan(self):
        return Plan.model_validate_json(self.plan_json)


@dataclass
class ExecutionState:
    state: TaskState | Literal["IDLE"] = "IDLE"
    reason: str = ""
    error: ErrorInfo | None = None
    active_actions: dict[str, str] = field(default_factory=dict)
    results: dict[str, str] = field(default_factory=dict)


@dataclass
class PlanningState:
    request_id: str | None = None
    state: GoalState | Literal["IDLE"] = "IDLE"
    worker: asyncio.Task | None = None
    # Display-only measurements; never a timeout or an authorization input.
    timing: dict = field(default_factory=dict)


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
        self._offered = {}
        self._starting = False
        self._control_serial = 0
        self.task = None
        self.execution = ExecutionState()
        self.workers = set()
        self.requests = {}
        self.tasks = {}
        self.goals = {}
        self.closed = False
        self.node_locks = {node: asyncio.Lock() for node in nodes}
        self.planner_calls = 0
        self.planning = PlanningState()
        self.queued = []
        self.cache = PlanCache()
        self.recoveries = 0
        self.recovered_task = None
        self.action_history = deque(maxlen=128)

    @property
    def task_id(self):
        return self.task.task_id if self.task else None

    def _active(self, task):
        return self.task is task and not self.closed and self.execution.state == TaskState.RUNNING

    def _planning_current(self, request_id):
        return self.planning.request_id == request_id and not self.closed

    def _activate(self, task, state=TaskState.RUNNING):
        self._save_task()
        self._stale_planning()
        self.planning.request_id = None
        self.planning.state = "IDLE"
        self.task = task
        self.execution = ExecutionState(state=TaskState(state))

    def _record_error(self, error):
        # Preserve the uncertain action when an independent branch subsequently fails.
        if self.execution.error is None or self.execution.error.code != "execution_unknown":
            self.execution.error, self.execution.reason = error, error.message

    def _capacity(self):
        if len(self.tasks) + len(self.goals) >= 4096:
            raise AgentError("result_capacity")

    def _remember_task(self, task):
        self._capacity()
        self.tasks[task.task_id] = {
            "task_id": task.task_id,
            "state": TaskState.QUEUED,
            "reason": "",
            "error": None,
            "steps": {},
            "active_actions": {},
        }

    def _save_task(self):
        if self.task is not None and self.tasks[self.task_id]["state"] not in {
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }:
            self.tasks[self.task_id].update(
                state=self.execution.state,
                reason=self.execution.reason,
                error=self.execution.error.model_dump() if self.execution.error else None,
                steps=dict(self.execution.results),
                active_actions=dict(self.execution.active_actions),
            )

    def task_status(self, task_id):
        if task_id not in self.tasks:
            raise AgentError("task_not_found")
        self._save_task()
        return deepcopy(self.tasks[task_id])

    def goal_status(self, request_id):
        if request_id not in self.goals:
            raise AgentError("goal_not_found")
        return deepcopy(self.goals[request_id])

    def _stale_planning(self):
        record = self.goals.get(self.planning.request_id)
        if record and record["state"] == GoalState.WAITING:
            record.update(state=GoalState.STALE, reason="execution_or_control_changed")

    def _clear_queue(self, reason="queue_cleared_by_control"):
        for task in self.queued:
            self.tasks[task.task_id].update(state=TaskState.CANCELLED, reason=reason)
        self.queued.clear()

    def snapshot(self):
        actions = dict(self.execution.active_actions)
        planning = self.goals.get(self.planning.request_id, {})
        return {
            "nodes": self.registry.snapshot() if self.registry else {},
            "task_id": self.task_id,
            "revision": 0,  # Deprecated wire field; task IDs identify executions.
            "state": self.execution.state,
            "action_id": next(iter(actions.values()), None),
            "active_actions": actions,
            "steps": dict(self.execution.results),
            "reason": self.execution.reason,
            "error": self.execution.error.model_dump() if self.execution.error else None,
            "planning": self.planning.state,
            "planning_request_id": self.planning.request_id,
            "planning_error": deepcopy(planning.get("error")),
            "planner_calls": self.planner_calls,
            "last_planning": dict(self.planning.timing),
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
                raise AgentError("request_capacity")
            return None
        original, result = self.requests[key]
        if data != original:
            raise AgentError("request_id_conflict")
        return result

    async def start(self, request: TaskRequest):
        request = TaskRequest.model_validate_json(request.model_dump_json())
        duplicate = self._duplicate(request.request_id, request.model_dump())
        if duplicate is not None:
            return duplicate
        if self.closed or self._starting or self.execution.state not in {
            "IDLE", TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED,
        }:
            raise AgentError("task_busy")
        self._starting = True
        serial = self._control_serial
        try:
            features, routes, ambiguous = await self._discover(include_held=True)
            task = self._task_from(request.plan, features, routes, ambiguous)
            if self.closed or serial != self._control_serial:
                raise AgentError("stale_task")
            self._remember_task(task)
            self._activate(task)
            result = self.snapshot()
            self.requests[request.request_id] = (request.model_dump(), result)
            self._spawn(self._execute(task))
            return result
        finally:
            self._starting = False

    async def enqueue(self, request: TaskRequest):
        request = TaskRequest.model_validate_json(request.model_dump_json())
        data = {"kind": "enqueue", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        previous, serial = self.task, self._control_serial
        features, routes, ambiguous = await self._discover(include_held=True)
        task = self._task_from(request.plan, features, routes, ambiguous)
        if previous is not self.task or serial != self._control_serial or self.closed:
            raise AgentError("stale_task")
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        if len(self.queued) >= 16:
            raise AgentError("task_queue_full")
        if self.execution.state != TaskState.RUNNING:
            raise AgentError("enqueue_requires_active_task")
        self._remember_task(task)
        self.queued.append(task)
        result = {"accepted": True, "task_id": task.task_id, "queued": len(self.queued)}
        self.requests[request.request_id] = (data, result)
        return result

    async def goal(self, request):
        data = {"kind": "goal", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return duplicate
        if self._starting or self.planner is None or (
            self.planning.worker and not self.planning.worker.done()
        ):
            raise AgentError("planner_unavailable_or_busy")
        if self.closed or self.execution.state not in {
            "IDLE",
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.RUNNING,
        }:
            raise AgentError("task_not_available")
        self._capacity()
        self.goals[request.request_id] = {
            "request_id": request.request_id,
            "state": GoalState.WAITING,
            "reason": "",
            "task_id": None,
            "error": None,
        }
        self._save_task()
        # Planning requests have an ID; a Task exists only after an executable plan is valid.
        was_running = self.execution.state == TaskState.RUNNING
        if not was_running:
            self.execution.reason = ""
        self.planning.request_id = request.request_id
        self.planning.state = GoalState.WAITING
        result = {"accepted": True, "request_id": request.request_id, "revision": 0}
        self.requests[request.request_id] = (data, result)
        self.planning.worker = self._spawn(
            self._plan(request.goal, request.request_id, was_running)
        )
        return result

    async def _state(self, node, *, control=False):
        return await node.snapshot(control=control)

    async def _features(self, names=None, *, include_held=False, clients=None):
        required = names is not None
        if self.registry:
            await self.registry.refresh(names)
        clients = self.nodes if clients is None else clients
        names = list(clients) if names is None else list(names)
        result = {}
        for name in names:
            try:
                if self.registry:
                    if (
                        not required and not include_held
                        and not self.registry.snapshot()[name]["ready"]
                    ):
                        continue
                    result[name] = await self.registry.features(
                        name, clients[name], require_ready=not include_held,
                    )
                else:
                    result[name] = await clients[name].features()
            except Exception as exc:
                if not required:
                    continue
                raise AgentError(from_exception(exc, node_id=name, stage="preflight")) from None
        return result

    async def _discover(self, *, include_held=False):
        features = await self._features(include_held=include_held)
        if self.registry:
            routes, ambiguous = self.registry.routes()
        else:
            for name, cap in features.items():
                self._offered.setdefault(name, set()).update(cap.skills)
            routes, ambiguous = resolve_routes(self._offered, self.bindings)
        return features, routes, ambiguous

    def _task_from(self, plan, features, routes, ambiguous=(), *, clients=None):
        if any(step.skill in ambiguous for step in plan.steps):
            raise AgentError("skill_provider_ambiguous", stage="preflight")
        specs = skill_specs(features, routes)
        steps = []
        for step in plan.steps:
            if step.version is None:
                if step.skill not in specs:
                    raise AgentError("provider_not_found", stage="preflight")
                step = step.model_copy(update={"version": specs[step.skill].version})
            steps.append(step)
        clients = self.nodes if clients is None else clients
        participants = {routes.get(step.skill) for step in steps}
        for name in participants:
            if name in clients:
                self.node_locks.setdefault(name, asyncio.Lock())
        return TaskExecution.create(
            plan.model_copy(update={"steps": steps}), bindings=routes, specs=specs,
            boots={name: cap.boot_id for name, cap in features.items()},
            clients={name: clients[name] for name in participants if name in clients},
            controls={name: cap.robot_controls for name, cap in features.items()},
        )

    async def _plan_valid(self, request_id, worlds, clients):
        for name, old in worlds.items():
            if not self._planning_current(request_id):
                return False
            current = await self._state(clients[name], control=True)
            if self.registry:
                self.registry.check_instance(name, old.boot_id)
            if not self._planning_current(request_id):
                return False
            if (
                current.boot_id != old.boot_id
                or current.control_epoch != old.control_epoch
                or current.admission != "OPEN"
                or current.state_version != old.state_version
            ):
                self.planning.state = GoalState.STALE
                return False
        return self._planning_current(request_id)

    async def _plan(self, goal, request_id, was_running):
        worlds, clients = {}, {}
        started = time.perf_counter()
        self.planning.timing = {}
        try:
            features, routes, ambiguous = await self._discover()
            clients = {name: self.nodes[name] for name in features}
            worlds = {
                name: await self._state(clients[name], control=True) for name in features
            }
            if not self._planning_current(request_id):
                return
            request = PlanRequest(
                user_goal=goal,
                world={n: w.model_dump(exclude={"lease_id"}) for n, w in worlds.items()},
                skills=[spec.model_dump() for spec in lookup_skills(features, routes)],
            )
            cached = self.cache.lookup(goal, worlds, features, routes)
            called = time.perf_counter()
            if cached is None:
                self.planner_calls += 1
                decision = await self.planner.plan(request)
                usage = token_counts(getattr(self.planner, "last_usage", None) or {})
            else:
                decision = PlanDecision(kind="execute", plan=cached)
                usage = {}
            done = time.perf_counter()
            if self._planning_current(request_id):  # A replaced goal's late reply is not shown.
                self.planning.timing = {
                    "total_s": round(done - started, 3),  # Preflight snapshots plus the model.
                    "model_s": round(done - called, 3) if cached is None else None,
                    **usage,
                }
            if not await self._plan_valid(request_id, worlds, clients):
                return
            if decision.kind != "execute":
                self.planning.state = GoalState(decision.kind.upper())
                self.goals[request_id]["reason"] = decision.text
                if not was_running:
                    self.execution.reason = decision.text
                return
            if was_running:
                self.planning.state = GoalState.REQUIRES_CONFIRMATION
                return
            validated = validate_plan(decision.plan, features, routes)
            task = self._task_from(validated, features, routes, ambiguous, clients=clients)
            validated = task.plan()
            self._remember_task(task)
            self.goals[request_id].update(state=GoalState.DONE, task_id=task.task_id)
            self._activate(task)
            self.planning.state = GoalState.DONE
            await self._execute(task)
            if self.task is task and not self.closed:
                if self.execution.state == TaskState.SUCCEEDED:
                    self.cache.remember(goal, validated, worlds, features, routes)
                else:
                    self.cache.invalidate(goal, self.execution.reason or self.execution.state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._planning_current(request_id):
                return
            # A late provider error after a physical stop is obsolete too.
            try:
                valid = await self._plan_valid(request_id, worlds, clients)
            except Exception:
                valid = False
            if not self._planning_current(request_id):
                return
            if not valid:
                self.planning.state = GoalState.STALE
                return
            self.planning.state = GoalState.FAILED
            failure = from_exception(exc, stage="planning")
            self.goals[request_id].update(reason=failure.message, error=failure.model_dump())
            self.cache.invalidate(goal, failure.code)
            if not was_running:
                self.execution.state, self.execution.reason = TaskState.FAILED, failure.message

        finally:
            record = self.goals[request_id]
            if record["state"] == GoalState.WAITING:
                record.update(
                    state=self.planning.state
                    if self._planning_current(request_id)
                    else GoalState.STALE,
                    reason=record["reason"]
                    or (
                        self.execution.reason
                        if self._planning_current(request_id)
                        else "planning_obsolete"
                    ),
                )

    async def _execute(self, task):
        if not self._active(task):
            return
        plan = task.plan()
        try:
            features = await self._features(self._participants(task), clients=task.clients)
            validate_plan(plan, features, task.bindings)
            for step in plan.steps:
                cap = features[task.bindings[step.skill]]
                if cap.specs.get(step.skill) != task.specs[step.skill]:
                    raise AgentError("skill_catalog_changed", stage="preflight")
            # All Python validators run at their owners before ANY branch dispatches.
            # This is effect-free: no leases, resource claims or physical actions.
            await asyncio.gather(*(
                task.clients[name].validate(
                    [step for step in plan.steps if task.bindings[step.skill] == name],
                    boot_id=task.boots[name],
                )
                for name in self._participants(task)
            ))
            # Capture every participant before ANY branch can dispatch.
            for name in self._participants(task):
                if not self._active(task):
                    return
                try:
                    world = await self._state(task.clients[name], control=True)
                except Exception as exc:
                    raise AgentError(from_exception(exc, node_id=name, stage="preflight")) from None
                if not self._active(task):
                    return
                if self.registry:
                    self.registry.check_instance(name, world.boot_id)
                if world.boot_id != task.boots[name]:
                    raise AgentError("node_instance_changed", node_id=name, stage="preflight")
                if world.admission != "OPEN":
                    raise AgentError("node_not_ready", node_id=name, stage="preflight")
                task.authorities[name] = (world.boot_id, world.control_epoch)
        except Exception as exc:
            if self._active(task):
                self.execution.state = TaskState.FAILED
                self._record_error(from_exception(exc, task_id=task.task_id, stage="preflight"))
                self._clear_queue("preceding_task_did_not_succeed")
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
                node_name = task.bindings[step.skill]
                async with self.node_locks[node_name]:
                    if not self._active(task):
                        outcomes[step.step_id] = "STALE"
                        return
                    resolved = step
                    if step.inputs:
                        args = dict(step.args)
                        for parameter, ref in step.inputs.items():
                            source = task.outputs.get(ref.step_id, {})
                            if ref.field not in source:
                                raise AgentError("step_output_missing")
                            args[parameter] = source[ref.field]
                        resolved = step.model_copy(update={"args": args, "inputs": {}})
                        await task.clients[node_name].validate(
                            [resolved], boot_id=task.boots[node_name],
                        )
                        if not self._active(task):
                            outcomes[step.step_id] = "STALE"
                            return
                    outcomes[step.step_id] = await self._run_step(resolved, node_name, task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = from_exception(
                    exc,
                    node_id=task.bindings.get(step.skill),
                    task_id=task.task_id,
                    stage="preflight",
                )
                outcomes[step.step_id] = (
                    "UNKNOWN" if failure.code == "execution_unknown" else "FAILED"
                )
                if self._active(task):
                    self._record_error(failure)
                if self._active(task) and outcomes[step.step_id] == "UNKNOWN":
                    try:
                        await self._fence(task.bindings[step.skill], task)
                    except Exception:
                        pass  # The original action remains UNKNOWN; don't lose its ID/cause.
            finally:
                if self._active(task):
                    self.execution.results.update(outcomes)
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
        self.execution.state = (
            TaskState.UNKNOWN
            if ActionState.UNKNOWN in values
            else TaskState.FAILED
            if ActionState.FAILED in values
            else TaskState.CANCELLED
            if values != {ActionState.SUCCEEDED}
            else TaskState.SUCCEEDED
        )
        if self.execution.state == TaskState.SUCCEEDED:
            self.execution.reason, self.execution.error = "", None
        if self.execution.state != TaskState.SUCCEEDED:
            self._clear_queue("preceding_task_did_not_succeed")
        if self.execution.state == TaskState.SUCCEEDED and self.queued and not self.closed:
            queued = self.queued.pop(0)
            self._activate(queued)
            self._spawn(self._execute(queued))

    async def _run_step(self, step, node_name, task):
        outcome, status, original = await self._attempt(
            step, node_name, task, task.authorities[node_name]
        )
        if (
            outcome != "FAILED"
            or status.verification != "FAIL"
            or status.reason not in {"localization_failed", "grasp_failed"}
            or "observe_once" not in task.specs[step.skill].recovery
            or self.recovered_task == task.task_id
            or not self._active(task)
            or self.closed
        ):
            return outcome
        node = task.clients[node_name]
        authority = (original.boot_id, original.control_epoch)
        current = await node.snapshot()
        if not self._recoverable_state(step, current, authority, task):
            return outcome
        features = await node.features()
        if (
            "observe" not in task.specs
            or task.bindings.get("observe") != node_name
            or features.specs.get("observe") != task.specs["observe"]
            or features.skills.get(step.skill) != step.version
        ):
            return outcome
        if not self._active(task):
            return "STALE"
        self.recovered_task = task.task_id
        self.recoveries += 1
        observe = Step(
            step_id="recovery-" + new_id(), skill="observe", version=task.specs["observe"].version
        )
        observed, _, _ = await self._attempt(observe, node_name, task, authority)
        if observed != "SUCCEEDED":
            return observed
        current = await node.snapshot()
        if not self._recoverable_state(step, current, authority, task):
            return "BLOCKED"
        # Exactly one new action, with a fresh ID and receiver-issued authorization.
        return (await self._attempt(step, node_name, task, authority))[0]

    def _recoverable_state(self, step, world, authority, task):
        obj = (
            world.data.objects.get(step.args.get("object"))
            if isinstance(world.data, SceneData)
            else None
        )
        return (
            self._active(task)
            and not self.closed
            and (world.boot_id, world.control_epoch) == authority
            and world.admission == "OPEN"
            and world.stop_confirmed
            and world.active_action is None
            and obj is not None
            and obj.valid
            and obj.location not in {None, "gripper"}
            and world.data.robot.held_object is None
            and (world.data.robot.kinematics is None or world.data.robot.kinematics.valid)
        )

    async def _attempt(self, step, node_name, task, authority=None):
        node = task.clients[node_name]
        if self.registry:
            await self.registry.refresh([node_name])
            info = self.registry._ready(node_name)
            require_contract(
                step.skill, step.version, info["skills"], node_id=node_name, stage="preflight"
            )
        world = await node.context()
        if self.registry:
            self.registry.check_instance(node_name, world.boot_id)
        if not self._active(task):
            return "STALE", None, world
        if authority is not None and (world.boot_id, world.control_epoch) != authority:
            return "STALE", None, world
        if world.admission != "OPEN":
            return "BLOCKED", None, world
        task.submitting.add(step.step_id)
        task.submissions_idle.clear()
        try:
            handle = await node.submit(
                step.skill,
                step.args,
                context=world,
                task_id=task.task_id,
                version=step.version,
            )
        except AgentError as exc:
            if exc.code == "execution_unknown":
                task.actions[step.step_id] = (node_name, exc.error.action_id)
            raise AgentError(
                from_exception(exc, node_id=node_name, task_id=task.task_id, stage="submit")
            ) from None
        except Exception:
            task.actions[step.step_id] = (node_name, None)
            raise AgentError(
                "execution_unknown", node_id=node_name, task_id=task.task_id, stage="submit"
            ) from None
        finally:
            task.submitting.discard(step.step_id)
            if not task.submitting:
                task.submissions_idle.set()
        action, status = handle.request, handle.receipt.status
        task.actions[step.step_id] = (node_name, action.action_id)
        if not self._active(task):
            return "STALE", None, world
        self.execution.active_actions[step.step_id] = action.action_id
        try:
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
            async with asyncio.timeout(task.specs[step.skill].timeout):
                while status is not None and status.state not in TERMINAL:
                    if not self._active(task):
                        return "STALE", None, world
                    await asyncio.sleep(0.01)
                    status = await node.status(action.action_id)
            if not self._active(task):
                return "STALE", None, world
            if status is None or status.state == ActionState.UNKNOWN:
                raise AgentError(
                    "execution_unknown",
                    node_id=node_name,
                    task_id=task.task_id,
                    action_id=action.action_id,
                    stage="observe",
                )
            if status.state == ActionState.SUCCEEDED and status.verification != "PASS":
                raise AgentError(
                    "execution_unknown",
                    node_id=node_name,
                    task_id=task.task_id,
                    action_id=action.action_id,
                    stage="observe",
                )
            if status.state == ActionState.FAILED:
                self._record_error(
                    status.error
                    or error_info(
                        "action_failed",
                        node_id=node_name,
                        task_id=task.task_id,
                        action_id=action.action_id,
                        stage="observe",
                    )
                )
            if status.state == ActionState.SUCCEEDED:
                task.outputs[step.step_id] = dict(status.output)
            return status.state, status, world
        except Exception:
            if not self._active(task):
                return "STALE", None, world
            raise AgentError(
                "execution_unknown",
                node_id=node_name,
                task_id=task.task_id,
                action_id=action.action_id,
                stage="observe",
            ) from None
        finally:
            if self.execution.active_actions.get(step.step_id) == action.action_id:
                self.execution.active_actions.pop(step.step_id)

    def _participants(self, task):
        names = set()
        for step in task.plan().steps:
            name = task.bindings.get(step.skill)
            if name not in task.clients:
                raise AgentError(
                    "provider_not_found", node_id=name, task_id=task.task_id, stage="preflight"
                )
            names.add(name)
        return names

    async def _fence(self, node_name, task):
        node = task.clients[node_name]
        robot = task.robot_controls[node_name]
        for _ in range(3):
            world = await self._state(node, control=True)
            if task is not None:
                if self.task is not task:
                    raise AgentError("stale_task")
                bound = task.authorities.get(node_name)
                if world.boot_id != task.boots.get(node_name, world.boot_id):
                    raise AgentError("node_instance_changed", node_id=node_name, stage="control")
                if bound and world.boot_id != bound[0]:
                    raise AgentError("node_instance_changed", node_id=node_name, stage="control")
                task.authorities.setdefault(node_name, (world.boot_id, world.control_epoch))
            request = ControlRequest(
                interrupt_id=new_id(),
                boot_id=world.boot_id,
                control_epoch=world.control_epoch,
            )
            result = await (node.control("hold", request) if robot else node.cancel(request))
            if result.accepted:
                if (
                    task is not None
                    and robot
                    and world.admission == "OPEN"
                    and world.control_epoch == task.authorities[node_name][1]
                ):
                    task.release_epochs[node_name] = result.control_epoch
                return result
            if result.reason != "stale_control":
                raise AgentError(
                    result.error or error_info(result.reason, node_id=node_name, stage="control")
                )
        raise AgentError("concurrent_control_change")

    async def cancel(self, request: TaskCancelRequest):
        data = {"kind": "cancel", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return deepcopy(duplicate)
        if self.closed:
            raise AgentError("runtime_closed", stage="control")
        queued = next((task for task in self.queued if task.task_id == request.task_id), None)
        if queued is not None:
            self.queued.remove(queued)
            self.tasks[queued.task_id].update(
                state=TaskState.CANCELLED, reason="cancelled_before_start"
            )
            result = self._cancel_receipt(queued.task_id, TaskState.CANCELLED, "STOPPED")
        elif self.task is None or request.task_id != self.task_id:
            raise AgentError("stale_task", task_id=request.task_id, stage="control")
        elif self.execution.state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}:
            result = self._cancel_receipt(request.task_id, self.execution.state, "STOPPED")
        else:
            result = self._begin_cancel(self.task)
        self.requests[request.request_id] = (data, result)
        return deepcopy(result)

    @staticmethod
    def _cancel_receipt(task_id, state, phase):
        return {"task_id": task_id, "state": TaskState(state), "accepted": True, "phase": phase}

    def _begin_cancel(self, task):
        if self.execution.state != TaskState.CANCELLING:
            self._stale_planning()
            self.planning.request_id = None
            if self.planning.state == GoalState.WAITING:
                self.planning.state = GoalState.STALE
            self.execution.state = TaskState.CANCELLING
            self._clear_queue()
            self._spawn(self._finish_cancel(task))
        return self._cancel_receipt(task.task_id, TaskState.CANCELLING, "STOPPING")

    async def interrupt(self, request):
        """Bind the current task/planning once; retries never target later work."""
        data = {"kind": "interrupt", **request.model_dump()}
        duplicate = self._duplicate(request.request_id, data)
        if duplicate is not None:
            return deepcopy(duplicate)
        if self.closed:
            raise AgentError("runtime_closed", stage="control")
        self._control_serial += 1
        self._stale_planning()
        self.planning.request_id = None
        if self.planning.state == GoalState.WAITING:
            self.planning.state = GoalState.STALE
            # WAITING means this call has not started a task, so only the model request is
            # dropped; its reply would be discarded anyway and would keep goal() busy until then.
            if self.planning.worker and not self.planning.worker.done():
                self.planning.worker.cancel()
        self._clear_queue()
        if self.task and self.execution.state in {
            TaskState.RUNNING,
            TaskState.CANCELLING,
            TaskState.UNKNOWN,
        }:
            result = self._begin_cancel(self.task)
        else:
            result = {"accepted": True, "phase": "STOPPED", "task_id": None}
        self.requests[request.request_id] = (data, result)
        return deepcopy(result)

    async def _finish_cancel(self, task):
        try:
            names = sorted(self._participants(task))
            receipts = await asyncio.gather(
                *(self._fence(name, task) for name in names), return_exceptions=True
            )
            for name, receipt in zip(names, receipts, strict=True):
                if isinstance(receipt, Exception):
                    raise AgentError(from_exception(receipt, node_id=name, stage="control"))
                if not receipt.accepted or receipt.phase == "UNKNOWN":
                    raise AgentError("control_unconfirmed", node_id=name, stage="control")
            # Fence first, then reconcile any submit that was already on the wire.
            async with asyncio.timeout(3):
                await task.submissions_idle.wait()
            stopped = await asyncio.gather(*(self._wait_stopped(name, task) for name in names))
            results = dict(self.execution.results)
            async with asyncio.timeout(3):
                for step_id, (name, action_id) in task.actions.items():
                    status = await task.clients[name].status(action_id) if action_id else None
                    while status is not None and status.state not in TERMINAL:
                        await asyncio.sleep(0.01)
                        status = await task.clients[name].status(action_id)
                    if (
                        status is None
                        or status.state == ActionState.UNKNOWN
                        or (status.state == ActionState.SUCCEEDED and status.verification != "PASS")
                    ):
                        raise AgentError(
                            "execution_unknown", node_id=name, action_id=action_id, stage="control"
                        )
                    results[step_id] = status.state
            for step in task.plan().steps:
                results.setdefault(step.step_id, "CANCELLED")
            # Task cancellation ends this execution. Only reopen the fence we just created;
            # another operator hold, a restart or UNKNOWN must remain closed.
            for name, world, receipt in zip(names, stopped, receipts, strict=True):
                if (
                    not self.closed
                    and task.robot_controls[name]
                    and world.admission == "HELD"
                    and world.control_epoch == receipt.control_epoch
                    and task.release_epochs.get(name) == receipt.control_epoch
                ):
                    await task.clients[name].control(
                        "allow_actions",
                        ControlRequest(
                            interrupt_id=new_id(),
                            boot_id=world.boot_id,
                            control_epoch=world.control_epoch,
                            state_version=world.state_version,
                        ),
                    )
            if self.task is task:
                self.execution.state, self.execution.results = TaskState.CANCELLED, results
                self.execution.reason, self.execution.error = "cancelled_by_request", None
                self.execution.active_actions.clear()
                self._save_task()
        except Exception as exc:
            if self.task is task:
                self.execution.state = TaskState.UNKNOWN
                self._record_error(from_exception(exc, task_id=task.task_id, stage="control"))
                self._save_task()

    async def _wait_stopped(self, node_name, previous):
        async with asyncio.timeout(3):
            while True:
                world = await self._state(previous.clients[node_name], control=True)
                bound = previous.authorities.get(node_name) if previous else None
                if bound and world.boot_id != bound[0]:
                    raise AgentError("node_instance_changed", node_id=node_name, stage="control")
                if world.admission == "UNKNOWN":
                    raise AgentError("control_unconfirmed", node_id=node_name, stage="control")
                if world.stop_confirmed and world.active_action is None:
                    return world
                await asyncio.sleep(0.01)

    async def close(self):
        if self.closed:
            return
        if self.task and self.execution.state in {TaskState.RUNNING, TaskState.UNKNOWN}:
            self._begin_cancel(self.task)
        self.closed = True
        self._control_serial += 1
        self._stale_planning()
        self.planning.request_id = None
        self._clear_queue()
        if self.planning.worker and not self.planning.worker.done():
            self.planning.worker.cancel()
        await asyncio.gather(*list(self.workers), return_exceptions=True)
