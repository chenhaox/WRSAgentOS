"""Shared Action lifecycle, idempotence and per-node control fencing."""

import asyncio
import contextlib
import time
from collections import OrderedDict

from wrs_agent.schemas import (
    ActionContext,
    ActionReceipt,
    ActionRequest,
    ActionStatus,
    CapabilitySnapshot,
    ControlReceipt,
    ControlRequest,
    WorldSnapshot,
    new_id,
)
from wrs_agent.skills import SPECS, validate_skill
from wrs_agent.store import Journal


class SkillFailure(ValueError):
    """Known effect-free failure with an explicit machine-readable reason."""


class ExecutionUnknown(RuntimeError):
    """Backend effect/stop cannot be confirmed; admission must remain closed."""


class ActionExecutor:
    def __init__(
        self,
        journal_path,
        *,
        state,
        perform,
        skills,
        backend,
        duration=0.4,
        fault=None,
        advance=None,
        close_backend=None,
        capabilities_extra=None,
    ):
        self.boot_id = new_id()
        self.epoch = 0
        self.journal = Journal(journal_path)
        self.records = self.journal.records.copy()
        self.world = state
        self.perform = perform
        self.advance, self.close_backend = advance, close_backend
        self.capabilities_extra = capabilities_extra or {}
        self.skills = frozenset(skills)
        self.backend = backend
        self.admission = "HELD" if self.records else "OPEN"
        self.stop_confirmed = True
        if any(s.state == "UNKNOWN" for _, s in self.records.values()):
            self.admission = "UNKNOWN"
            self.stop_confirmed = False
        self.duration, self.fault = duration, fault
        self.active = None
        self.runner = None
        self.stop_signal = asyncio.Event()
        self.leases = OrderedDict()
        self.controls = {}
        self.revisions = {}
        self.executions = 0
        self.on_event = lambda suffix, event: None

    def capabilities(self):
        return CapabilitySnapshot(
            skills=sorted(self.skills),
            backend=self.backend,
            **self.capabilities_extra,
            resources=sorted({r for name in self.skills for r in SPECS[name].resources}),
        )

    def snapshot(self):
        lease = new_id()
        self.leases[lease] = (self.epoch, self.world.version, time.monotonic() + 2.0)
        while len(self.leases) > 64:
            self.leases.popitem(last=False)
        return WorldSnapshot(
            boot_id=self.boot_id,
            control_epoch=self.epoch,
            world_version=self.world.version,
            lease_id=lease,
            admission=self.admission,
            active_action=self.active,
            **self.world.snapshot(),
            stop_confirmed=self.stop_confirmed,
        )

    def context(self):
        state = self.snapshot().model_dump()
        return ActionContext.model_validate({k: state[k] for k in ActionContext.model_fields})

    def _finish_cancel(self):
        # TTS cancellation ends one utterance. New work still needs a fresh epoch/lease.
        if (
            not self.capabilities().robot_controls
            and self.admission == "HELD"
            and self.stop_confirmed
        ):
            self.admission = "OPEN"

    def status(self, action_id):
        record = self.records.get(action_id)
        return record[1] if record else None

    def _status(self, action_id, state, reason="", verification="PENDING", progress=None):
        request, old = self.records[action_id]
        status = ActionStatus(
            action_id=action_id,
            state=state,
            sequence=old.sequence + 1,
            progress=old.progress if progress is None else progress,
            reason=reason,
            verification=verification,
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
            self._status(action_id, "UNKNOWN", "journal_failed", "INCONCLUSIVE")
            self.stop_signal.set()
            raise

    def _fence_reason(self, request):
        if request.boot_id != self.boot_id:
            return "stale_boot"
        if request.control_epoch != self.epoch:
            return "stale_epoch"
        if self.admission != "OPEN":
            return "admission_closed"
        if request.world_version != self.world.version:
            return "stale_world"
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
                return ActionReceipt(accepted=False, reason="action_id_conflict")
            return ActionReceipt(accepted=True, status=status)
        reason = self._fence_reason(request)
        if not reason:
            try:
                if request.skill not in self.skills:
                    raise ValueError("skill_not_on_node")
                validate_skill(request.skill, request.version, request.args)
            except ValueError:
                reason = "invalid_skill_or_arguments"
        if not reason and self.active is not None:
            reason = "resource_busy"
        if not reason and len(self.records) >= 4096:
            reason = "journal_capacity"
        if reason:
            return ActionReceipt(accepted=False, reason=reason)
        self.revisions[request.task_id] = request.task_revision
        self.active = request.action_id
        self.stop_confirmed = False
        self.stop_signal = asyncio.Event()
        status = ActionStatus(action_id=request.action_id, state="ACCEPTED")
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
            self._status(request.action_id, "CANCELLED", reason or "held_before_start")
            self.active = None
            self.stop_confirmed = self.admission != "UNKNOWN"
            self._finish_cancel()
            await self._save(request.action_id)
        else:
            await self._execute(request)

    async def _execute(self, request):
        aid = request.action_id
        try:
            self._status(aid, "RUNNING")
            self.executions += 1
            await self._save(aid)
            args = validate_skill(request.skill, request.version, request.args)
            verified = False
            if self.advance is not None:

                def progress(value):
                    if not self.stop_signal.is_set():
                        self._status(aid, "RUNNING", progress=value)
                        self.on_event("state/world", self.snapshot().model_dump())

                if not self.stop_signal.is_set():
                    verified = await self.advance(request.skill, args, self.stop_signal, progress)
            else:
                try:
                    await asyncio.wait_for(self.stop_signal.wait(), timeout=self.duration)
                except TimeoutError:
                    pass
            if (
                self.stop_signal.is_set()
                or request.control_epoch != self.epoch
                or self.admission != "OPEN"
            ):
                if self.fault == "stop_unknown":
                    self.admission = "UNKNOWN"
                    self.stop_confirmed = False
                    self._status(aid, "UNKNOWN", "stop_unconfirmed", "INCONCLUSIVE")
                else:
                    self.stop_confirmed = True
                    self._status(aid, "CANCELLED", "controlled_stop")
                return
            if self.fault in {"unknown", "inconclusive"}:
                self.admission = "UNKNOWN"
                self.stop_confirmed = False
                self._status(aid, "UNKNOWN", "observation_inconclusive", "INCONCLUSIVE")
                return
            if self.advance is None:
                verified = self.perform(request.skill, self.world, args, self.fault)
            self.world.version += 1
            self._status(aid, "VERIFYING")
            # No await between virtual effect and verification: one state owner.
            self._status(
                aid,
                "SUCCEEDED" if verified else "FAILED",
                "" if verified else "postcondition_failed",
                "PASS" if verified else "FAIL",
                progress=1.0,
            )
        except asyncio.CancelledError:
            self.admission = "UNKNOWN"
            self.stop_confirmed = False
            self._status(aid, "UNKNOWN", "worker_cancelled", "INCONCLUSIVE")
            raise
        except SkillFailure as exc:
            self._status(aid, "FAILED", str(exc), "FAIL")
        except ExecutionUnknown:
            self.admission = "UNKNOWN"
            self.stop_confirmed = False
            self._status(aid, "UNKNOWN", "backend_state_unknown", "INCONCLUSIVE")
        except Exception:
            if self.status(aid).state != "UNKNOWN":
                self._status(aid, "FAILED", "precondition_or_execution_failed", "FAIL")
        finally:
            if self.status(aid).state in {"SUCCEEDED", "FAILED"}:
                self.stop_confirmed = True
            self.active = None
            if self.status(aid).state == "CANCELLED":
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
                accepted=accepted, phase=phase, reason=reason, control_epoch=self.epoch
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
        if kind == "resume":
            if (
                self.admission != "HELD"
                or not self.stop_confirmed
                or self.active is not None
                or request.world_version != self.world.version
            ):
                return receipt(False, "REJECTED", "resume_not_ready")
            self.epoch += 1
            self.leases.clear()
            self.admission = "OPEN"
            result = receipt(True, "RESUMED")
        elif kind in {"hold", "cancel"}:
            if kind == "cancel" and (request.action_id is None or request.action_id != self.active):
                return receipt(False, "REJECTED", "action_not_active")
            # Atomic fence, independent of motion and persistence waits.
            self.epoch += 1
            self.leases.clear()
            self.admission = "HELD" if self.admission != "UNKNOWN" else "UNKNOWN"
            self.stop_confirmed = self.active is None and self.admission != "UNKNOWN"
            self.stop_signal.set()
            if self.active:
                self._status(self.active, "CANCELLING")
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

    async def resume(self, request):
        return await self.control("resume", request)

    async def close(self):
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
