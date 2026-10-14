"""Shared Action lifecycle, idempotence and per-node control fencing."""

import asyncio
import contextlib
import inspect
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping

from pydantic import ValidationError

from wrs_agent.errors import AgentError, error_info
from wrs_agent.schemas import (
    MAX_BYTES,
    ActionContext,
    ActionReceipt,
    ActionRequest,
    ActionState,
    ActionStatus,
    ControlReceipt,
    ControlRequest,
    FeatureSnapshot,
    NodeSnapshot,
    SkillCheck,
    SkillCheckResult,
    new_id,
)
from wrs_agent.skills import Skill, validate_skill
from wrs_agent.store import Journal


class SkillFailure(ValueError):
    """Known effect-free failure with an explicit machine-readable reason."""


class ExecutionUnknown(RuntimeError):
    """Backend effect/stop cannot be confirmed; admission must remain closed.

    The diagnostic message stays local. reason is a fixed public code chosen by the backend.
    """

    def __init__(self, message, *, reason="backend_state_unknown"):
        super().__init__(message)
        self.reason = reason


class ActionExecutor:
    def __init__(
        self,
        journal_path,
        *,
        state,
        skills=(),
        backend,
        close_backend=None,
        features_extra=None,
    ):
        self.skills = {}
        self.skill_revision = 0
        self._skill_thread = threading.get_ident()
        self._closed = False
        if isinstance(skills, Mapping):
            raise TypeError("skills_sequence_required")
        self.add_skills(*skills)
        self.boot_id = new_id()
        self.epoch = 0
        self.journal = Journal(journal_path)
        self.records = self.journal.records.copy()
        self.world = state
        self.close_backend = close_backend
        self.features_extra = features_extra or {}
        self.backend = backend
        self.node_id = "wrs" if self.features().robot_controls else "tts"
        self.admission = "HELD" if self.records and self.features().robot_controls else "OPEN"
        self.stop_confirmed = True
        if any(s.state == ActionState.UNKNOWN for _, s in self.records.values()):
            self.admission = "UNKNOWN"
            self.stop_confirmed = False
        self.active = None
        self.runner = None
        self.stop_signal = asyncio.Event()
        self.leases = OrderedDict()
        self.controls = {}
        self.revisions = {}  # Legacy wire compatibility; new executions use fresh task IDs.
        self.executions = 0
        self.on_event = lambda suffix, event: None

    def add_skills(self, *skills):
        """Atomically append reviewed local bindings on the owning thread.

        Startup uses this same check. Existing names cannot be replaced, so
        in-flight actions and plans keep their original implementation.
        """
        if self._closed or threading.get_ident() != self._skill_thread:
            raise RuntimeError("skill_registration_outside_owner")
        pending = {}
        for entry in skills:
            if not isinstance(entry, Skill) or not callable(entry.handler):
                raise ValueError("invalid_skill_registration")
            name = entry.name
            if name in self.skills or name in pending:
                raise ValueError("duplicate_skill")
            pending[name] = entry
        combined = {**self.skills, **pending}
        if len(combined) > 64:
            raise ValueError("invalid_skill_count")
        # Leave room for feature metadata and the transport envelope.
        payload_size = sum(len(s.spec.model_dump_json().encode()) for s in combined.values())
        if payload_size > MAX_BYTES - 8192:
            raise ValueError("skill_catalog_too_large")
        if pending:
            self.skills = combined
            self.skill_revision += 1

    def validate(self, request: SkillCheck):
        request = SkillCheck.model_validate_json(request.model_dump_json())
        if request.boot_id != self.boot_id:
            raise AgentError("node_instance_changed")
        for step in request.steps:
            try:
                entry = self.skills.get(step.skill)
                if step.inputs:
                    if entry is None or step.version != entry.version:
                        raise AgentError("skill_version_mismatch")
                    if not step.inputs.keys() <= set(entry.result_parameters):
                        raise AgentError("result_input_not_allowed")
                    try:
                        entry.arguments.model_validate(step.args)
                    except ValidationError as exc:
                        # Only the explicitly bound missing fields may remain unresolved.
                        if any(e["type"] != "missing" or tuple(e["loc"]) not in
                               {(name,) for name in step.inputs} for e in exc.errors()):
                            raise AgentError("invalid_arguments") from None
                else:
                    validate_skill(step.skill, step.version, step.args, registry=self.skills)
            except AgentError as exc:
                raise AgentError(exc.error.model_copy(
                    update={"node_id": self.node_id, "stage": "preflight"},
                )) from None
        return SkillCheckResult(boot_id=self.boot_id, count=len(request.steps))

    def features(self):
        return FeatureSnapshot(
            skills={name: entry.version for name, entry in sorted(self.skills.items())},
            backend=self.backend,
            boot_id=self.boot_id,
            skill_revision=self.skill_revision,
            specs={name: entry.spec for name, entry in self.skills.items()},
            **self.features_extra,
            resources=sorted({r for entry in self.skills.values() for r in entry.resources}),
        )

    def snapshot(self):
        return NodeSnapshot(
            node_id=self.node_id,
            captured_at_ns=time.time_ns(),
            boot_id=self.boot_id,
            control_epoch=self.epoch,
            state_version=self.world.version,
            admission=self.admission,
            active_action=self.active,
            data=self.world.snapshot(),
            stop_confirmed=self.stop_confirmed,
        )

    def context(self):
        lease = new_id()
        self.leases[lease] = (self.epoch, self.world.version, time.monotonic() + 2.0)
        while len(self.leases) > 64:
            self.leases.popitem(last=False)
        return ActionContext(**self.snapshot().model_dump(), lease_id=lease)

    def _finish_cancel(self):
        # TTS cancellation ends one utterance. New work still needs a fresh epoch/lease.
        if (
            not self.features().robot_controls
            and self.admission == "HELD"
            and self.stop_confirmed
        ):
            self.admission = "OPEN"

    def status(self, action_id):
        record = self.records.get(action_id)
        return record[1] if record else None

    def active_request(self):
        """Task identity accepted by this executor, for task-scoped backend artifacts."""
        if self.active is None:
            raise AgentError("no_active_action")
        request, status = self.records[self.active]
        if status.state != ActionState.RUNNING:
            raise AgentError("action_not_running")
        return ActionRequest.model_validate(request)

    def _status(
        self, action_id, state: ActionState, reason="", verification="PENDING", progress=None,
        output=None,
    ):
        request, old = self.records[action_id]
        status = ActionStatus(
            action_id=action_id,
            state=state,
            sequence=old.sequence + 1,
            progress=old.progress if progress is None else progress,
            error=error_info(
                "execution_unknown" if state == ActionState.UNKNOWN else "action_failed",
                node_id=self.node_id,
                task_id=request["task_id"],
                action_id=action_id,
                stage="observe",
            )
            if state in {ActionState.FAILED, ActionState.UNKNOWN}
            else None,
            reason=reason,
            verification=verification,
            output=output or {},
        )
        self.records[action_id] = (request, status)
        self.on_event("events/action", status.model_dump())
        return status

    async def _save(self, action_id):
        request, status = self.records[action_id]
        try:
            await self.journal.save(request, status)
        except Exception:
            self.admission = "UNKNOWN"
            self.stop_confirmed = False
            self._status(action_id, ActionState.UNKNOWN, "journal_failed", "INCONCLUSIVE")
            self.stop_signal.set()
            raise

    def _fence_reason(self, request):
        if request.boot_id != self.boot_id:
            return "stale_boot"
        if request.control_epoch != self.epoch:
            return "stale_epoch"
        if self.admission != "OPEN":
            return "admission_closed"
        if request.state_version != self.world.version:
            return "stale_state"
        grant = self.leases.get(request.lease_id)
        if not grant or grant[:2] != (self.epoch, self.world.version):
            return "invalid_lease"
        if time.monotonic() >= grant[2]:
            return "expired_lease"
        if request.task_revision < self.revisions.get(request.task_id, 0):
            return "stale_revision"
        return ""

    async def submit(self, request: ActionRequest):
        # Copy even local calls: caller-owned mutable dictionaries cannot change authority.
        request = ActionRequest.model_validate_json(request.model_dump_json())
        data = request.model_dump()
        if request.action_id in self.records:
            previous, status = self.records[request.action_id]
            if data != previous:
                return ActionReceipt(
                    accepted=False,
                    reason="action_id_conflict",
                    error=error_info(
                        "action_id_conflict",
                        node_id=self.node_id,
                        action_id=request.action_id,
                        task_id=request.task_id,
                        stage="submit",
                    ),
                )
            return ActionReceipt(accepted=True, status=status)
        reason = self._fence_reason(request)
        if not reason:
            try:
                if request.skill not in self.skills:
                    raise AgentError("skill_not_on_node")
                validate_skill(request.skill, request.version, request.args, registry=self.skills)
            except AgentError as exc:
                reason = exc.code
        if not reason and self.active is not None:
            reason = "resource_busy"
        if not reason and len(self.records) >= 4096:
            reason = "journal_capacity"
        if reason:
            return ActionReceipt(
                accepted=False,
                reason=reason,
                error=error_info(
                    reason,
                    node_id=self.node_id,
                    task_id=request.task_id,
                    action_id=request.action_id,
                    stage="submit",
                ),
            )
        self.revisions[request.task_id] = request.task_revision
        self.active = request.action_id
        self.stop_confirmed = False
        self.stop_signal = asyncio.Event()
        status = ActionStatus(action_id=request.action_id, state=ActionState.ACCEPTED)
        self.records[request.action_id] = (data, status)
        self.runner = asyncio.create_task(self._admit_and_execute(request))
        return ActionReceipt(accepted=True, status=status)

    async def _admit_and_execute(self, request):
        # Receipt is quick; intent must reach disk before any backend effect.
        try:
            await self._save(request.action_id)
        except Exception:
            self.active = None
            return
        reason = self._fence_reason(request)
        if reason or self.stop_signal.is_set():
            self._status(request.action_id, ActionState.CANCELLED, reason or "held_before_start")
            self.active = None
            self.stop_confirmed = self.admission != "UNKNOWN"
            self._finish_cancel()
            await self._save(request.action_id)
        else:
            await self._execute(request)

    async def _execute(self, request):
        aid = request.action_id
        try:
            self._status(aid, ActionState.RUNNING)
            self.executions += 1
            await self._save(aid)
            args = validate_skill(
                request.skill, request.version, request.args, registry=self.skills
            )
            verified = False
            output = {}

            def progress(value):
                if not self.stop_signal.is_set():
                    self._status(aid, ActionState.RUNNING, progress=value)
                    self.on_event("state/world", self.snapshot().model_dump())

            if (
                not self.stop_signal.is_set()
                and request.control_epoch == self.epoch
                and self.admission == "OPEN"
            ):
                result = self.skills[request.skill].handler(
                    self.world,
                    args,
                    self.stop_signal,
                    progress,
                )
                result = await result if inspect.isawaitable(result) else result
                entry = self.skills[request.skill]
                if entry.output is not None and result is not False:
                    output = entry.output.model_validate(result).model_dump()
                    verified = True
                else:
                    verified = result is True
            if (
                self.stop_signal.is_set()
                or request.control_epoch != self.epoch
                or self.admission != "OPEN"
            ):
                self.stop_confirmed = True
                self._status(aid, ActionState.CANCELLED, "controlled_stop")
                return
            self.world.version += 1
            self._status(aid, ActionState.VERIFYING)
            # The backend returns only after checking its postcondition.
            self._status(
                aid,
                ActionState.SUCCEEDED if verified else ActionState.FAILED,
                "" if verified else "postcondition_failed",
                "PASS" if verified else "FAIL",
                progress=1.0,
                output=output if verified else {},
            )
        except asyncio.CancelledError:
            self.admission = "UNKNOWN"
            self.stop_confirmed = False
            self._status(aid, ActionState.UNKNOWN, "worker_cancelled", "INCONCLUSIVE")
            raise
        except SkillFailure as exc:
            self._status(aid, ActionState.FAILED, str(exc), "FAIL")
        except ExecutionUnknown as exc:
            self.admission = "UNKNOWN"
            self.stop_confirmed = False
            self._status(aid, ActionState.UNKNOWN, exc.reason, "INCONCLUSIVE")
        except Exception:
            if self.status(aid).state != ActionState.UNKNOWN:
                self._status(aid, ActionState.FAILED, "precondition_or_execution_failed", "FAIL")
        finally:
            if self.status(aid).state in {ActionState.SUCCEEDED, ActionState.FAILED}:
                self.stop_confirmed = True
            self.active = None
            if self.status(aid).state == ActionState.CANCELLED:
                self._finish_cancel()
            with contextlib.suppress(Exception):
                await self._save(aid)
            self.on_event(
                "events/control",
                {
                    "boot_id": self.boot_id,
                    "control_epoch": self.epoch,
                    "stop_confirmed": self.stop_confirmed,
                    "admission": self.admission,
                },
            )

    async def control(self, kind, request: ControlRequest, *, authorized=True):
        def receipt(accepted, phase, reason=""):
            return ControlReceipt(
                accepted=accepted,
                phase=phase,
                reason=reason,
                control_epoch=self.epoch,
                error=error_info(
                    reason or "control_unconfirmed",
                    node_id=self.node_id,
                    action_id=request.action_id,
                    stage="control",
                )
                if not accepted or phase == "UNKNOWN"
                else None,
            )

        if not authorized:
            return receipt(False, "REJECTED", "unauthorized")
        if request.interrupt_id in self.controls:
            old_kind, old_request, result = self.controls[request.interrupt_id]
            if kind != old_kind or request != old_request:
                return receipt(False, "REJECTED", "interrupt_id_conflict")
            return result
        if request.boot_id != self.boot_id or request.control_epoch != self.epoch:
            return receipt(False, "REJECTED", "stale_control")
        if len(self.controls) >= 4096:
            return receipt(False, "REJECTED", "control_capacity")
        if kind == "allow_actions":
            if (
                self.admission != "HELD"
                or not self.stop_confirmed
                or self.active is not None
                or request.state_version != self.world.version
            ):
                return receipt(False, "REJECTED", "allow_actions_not_ready")
            self.epoch += 1
            self.leases.clear()
            self.admission = "OPEN"
            result = receipt(True, "ACTIONS_ALLOWED")
        elif kind in {"hold", "cancel"}:
            if (
                kind == "cancel"
                and request.action_id is not None
                and request.action_id != self.active
            ):
                return receipt(False, "REJECTED", "action_not_active")
            # No action_id means cancel this resource, including delayed old-epoch submits.
            # Atomic fence, independent of motion and persistence waits.
            self.epoch += 1
            self.leases.clear()
            self.admission = "HELD" if self.admission != "UNKNOWN" else "UNKNOWN"
            self.stop_confirmed = self.active is None and self.admission != "UNKNOWN"
            self.stop_signal.set()
            if self.active:
                self._status(self.active, ActionState.CANCELLING)
            else:
                self._finish_cancel()
            result = receipt(
                True,
                "STOPPING" if self.active else ("STOPPED" if self.stop_confirmed else "UNKNOWN"),
            )
        else:
            return receipt(False, "REJECTED", "unknown_control")
        self.controls[request.interrupt_id] = (kind, request, result)
        self.on_event("events/control", result.model_dump())
        return result

    async def hold(self, request):
        return await self.control("hold", request)

    async def cancel(self, request):
        return await self.control("cancel", request)

    async def allow_actions(self, request):
        return await self.control("allow_actions", request)

    async def close(self):
        self._closed = True
        if self.active:
            await self.hold(
                ControlRequest(
                    interrupt_id=new_id(), boot_id=self.boot_id, control_epoch=self.epoch
                )
            )
        try:
            if self.runner:
                await self.runner
        finally:
            await asyncio.to_thread(self.journal.close)
            if self.close_backend is not None:
                await self.close_backend()
