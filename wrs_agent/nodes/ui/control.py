"""Dashboard state: poll discovered nodes, turn operator input into node requests."""

import asyncio
import threading
import time
from collections import deque

from wrs_agent.errors import AgentError, from_exception
from wrs_agent.nodes.asr.capture import CAPTURE_SECONDS
from wrs_agent.policy import text_intent
from wrs_agent.schemas import TERMINAL, ControlRequest, TextInput, new_id
from wrs_agent.skills import lookup_skills
from wrs_agent.system import step

ROLES = ("wrs", "agent", "voice", "tts", "asr")
REACHABLE = {"ready", "held"}
# These planning verdicts carry text in the overview's reason; DONE/IDLE reasons may be stale.
REPLIED = {"ANSWER", "CLARIFY", "FAILED", "STALE", "REQUIRES_CONFIRMATION"}
HINTS = {
    "planner_unavailable_or_busy": (
        "Agent 没有启用大模型（启动时加 --live-model 并配置 LLM_*），或上一条还在规划。"
    ),
    "task_busy": "上一条任务还在执行；先点“立即停止”。",
    "task_not_available": "上一次任务结果无法确认；先点“立即停止”，确认后再下新任务。",
    "node_unavailable": "需要的节点不在线。",
    "robot_allow_actions_unsupported": "该机器人节点不支持允许动作。",
    "robot_mode_unsupported": "该机器人节点不支持切换虚拟/真机。",
}
TEXT_REASONS = {
    "acknowledgement": "这是一句应答，不是指令。",
    "quoted_text": "指令里不要带引号。",
    "negated_text": "含否定词的指令不会被执行，请直接说要做什么。",
    "ambiguous_control": "像是控制指令但不明确；要停止请点“立即停止”。",
    "use_explicit_task_commands": "请分成单独的指令依次发送。",
    "cancel_then_start_required": "已停止；新目标请单独再发一次。",
}
VOICE_REASONS = {
    "no_complete_utterance": (
        f"没收到完整语音：请在 {CAPTURE_SECONDS:.0f} 秒内说完一句，太短或只录到环境声会整段丢弃。"
    ),
    "capture_failed": "麦克风没能录下这一段，请检查设备后重按。",
    "voice_unconfirmed": "停止已发出但未收到确认，请再按一次并确认机器人已停。",
}
# Empty form fields are omitted so the agent can keep its process environment. When that
# environment is also empty, name the page fields instead of the LLM_* variables.
_FORM_GAPS = {
    "llm_base_url_missing": (
        "还没有 Base URL。请把服务商预设改成「智谱 GLM」等厂商，或在输入框里填写地址。"
        "灰色斜体只是示例，不会提交。"
    ),
    "llm_model_missing": (
        "还没有模型 ID。请把服务商预设改成「智谱 GLM」等厂商，或在输入框里填写模型 ID。"
        "灰色斜体只是示例，不会提交。"
    ),
}


def describe(exc):
    if isinstance(exc, AgentError):
        return HINTS.get(exc.code, exc.error.message or exc.code)
    if isinstance(exc, ValueError) and str(exc) in HINTS:
        return HINTS[str(exc)]
    return from_exception(exc).message


class EventHub:
    """Bounded event log shared with HTTP threads; ids keep increasing across trims."""

    def __init__(self, limit=2000):
        self._cv = threading.Condition()
        self._events = deque(maxlen=limit)
        self.last = 0
        self._closed = False

    def emit(self, event_type, /, **payload):
        with self._cv:
            self.last += 1
            event = {"id": self.last, "at": time.time(), "type": event_type, **payload}
            self._events.append(event)
            self._cv.notify_all()
            return event

    def wait(self, after, timeout=20.0):
        with self._cv:
            if self.last <= after and not self._closed:
                self._cv.wait(timeout)
            return [event for event in self._events if event["id"] > after], self.last

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()


class Dashboard:
    """Runs on the node's event loop; HTTP threads only read `state` and submit coroutines."""

    def __init__(self, system, *, node_id, endpoint, env_id, viewer_url):
        self.system = system
        self.hub = EventHub()
        self.info = {
            "node_id": node_id, "endpoint": endpoint, "env_id": env_id, "viewer_url": viewer_url,
        }
        self.opened = False
        self.nodes = {}
        self.overview = None
        self.robot = None  # Latest NodeSnapshot of the robot node; the 3D view reads it.
        self.skills = []
        self.planner = None
        self.voice = {"listening": False, "recognizing": False, "heard": ""}
        self._press = None
        self._workers = set()
        self.viewer = {"state": "disabled", "detail": f"嵌入 {viewer_url}"}
        self.errors = {}
        self.state = {}
        self._skill_key = None
        self._seen_actions = None
        self._last = {"task": None, "state": None, "steps": {}, "planning": None}
        self._publish()

    async def close(self):
        self.hub.close()
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    def _background(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)
        return task

    def online(self, role):
        """A held robot still answers snapshots and allow_actions, so it counts as online."""
        name = self.system.roles.get(role)
        row = self.nodes.get(name)
        return name if row and row["health"] in REACHABLE else None

    # ------------------------------------------------------------- polling

    async def watch(self):
        tick = 0
        while True:
            if tick % 10 == 0:
                await self._poll("nodes", self._poll_nodes)
                await self._poll("planner", self._poll_planner)
            if tick % 3 == 0:
                await self._poll("agent", self._poll_agent)
            await self._poll("robot", self._poll_robot)
            self._publish()
            tick += 1
            await asyncio.sleep(0.1)

    async def _poll(self, name, call):
        try:
            await call()
            self.errors.pop(name, None)
        except Exception as exc:  # A failed observation is shown, never fatal to the page.
            self.errors[name] = describe(exc)

    async def _poll_nodes(self):
        rows = await self.system.nodes()
        for name, row in rows.items():
            previous = self.nodes.get(name)
            if previous is None or previous["health"] != row["health"]:
                self.hub.emit(
                    "node", node_id=name, node_type=row["node_type"], health=row["health"],
                )
        for name in set(self.nodes) - set(rows):
            self.hub.emit("node", node_id=name, node_type=self.nodes[name]["node_type"],
                          health="offline")
        self.nodes = rows
        names = sorted(
            name for name, row in rows.items()
            if row["actions"] and row["health"] in REACHABLE and name in self.system.clients
        )
        key = tuple((name, rows[name]["boot_id"], rows[name]["skill_revision"]) for name in names)
        if key != self._skill_key:
            registry = self.system.registry
            caps = await asyncio.gather(*(
                registry.features(name, self.system.clients[name], require_ready=False)
                for name in names
            ))
            routes, _ = registry.routes()
            self.skills = [
                spec.model_dump(mode="json")
                for spec in lookup_skills(dict(zip(names, caps, strict=True)), routes)
            ]
            self._skill_key = key

    async def _poll_planner(self):
        if not self.online("agent"):
            self.planner = None
            return
        try:
            self.planner = await self.system.agent.request("request/agent/planner", {})
        except (AgentError, TimeoutError):
            # An Agent without runtime configuration still plans per its --live-model flag.
            self.planner = {"enabled": None, "key_source": "unknown"}

    async def _poll_agent(self):
        if not self.online("agent"):
            self.overview = None
            return
        overview = await self.system.status()
        self._diff(overview)
        self.overview = overview

    async def _poll_robot(self):
        name = self.online("wrs")
        self.robot = await self.system.snapshot(node=name) if name else None

    def _diff(self, overview):
        last = self._last
        planning = (overview["planning"], overview["planning_request_id"])
        if planning != last["planning"] and last["planning"] is not None:
            verdict = overview["planning"]
            self.hub.emit(
                "planning", state=verdict, request_id=overview["planning_request_id"],
                reason=overview["reason"] if verdict in REPLIED else "",
                timing=overview.get("last_planning", {}) if verdict != "WAITING" else {},
            )
        last["planning"] = planning
        task_id, state = overview["task_id"], overview["state"]
        started = task_id != last["task"]
        if started:
            if task_id is not None and last["state"] is not None:
                self.hub.emit("task", task_id=task_id, state=state)
            last.update(task=task_id, steps={})
        history = overview.get("action_history", [])
        if self._seen_actions is None:
            self._seen_actions = {item["action_id"] for item in history}
        for item in history:
            if item["action_id"] not in self._seen_actions:
                self._seen_actions.add(item["action_id"])
                self.hub.emit("action", skill=item["skill"], action_id=item["action_id"],
                              task_id=item["task_id"])
        for name, result in overview["steps"].items():
            if last["steps"].get(name) != result and last["state"] is not None:
                self.hub.emit("step", step=name, state=result, task_id=task_id)
        last["steps"] = dict(overview["steps"])
        # A task that starts and finishes between two polls still reports its result.
        if task_id is not None and state in TERMINAL and (started or state != last["state"]):
            if last["state"] is not None:
                self.hub.emit("done", task_id=task_id, state=state, reason=overview["reason"],
                              error=overview["error"])
        last["state"] = state

    def _publish(self):
        overview, robot = self.overview, self.robot
        busy = bool(overview) and (
            overview["planning"] == "WAITING"
            or (overview["task_id"] is not None and overview["state"] not in TERMINAL)
        )
        self.state = {
            "info": self.info,
            "opened": self.opened,
            "roles": {role: self.online(role) for role in ROLES},
            "nodes": [
                {
                    "node_id": name, "node_type": row["node_type"], "health": row["health"],
                    "ready": row["ready"], "actions": row["actions"],
                    "robot_controls": row["robot_controls"], "skills": len(row["skills"]),
                    "error": (row["error"] or {}).get("code"),
                }
                for name, row in sorted(self.nodes.items())
            ],
            "task": None if overview is None else {
                key: overview.get(key) for key in (
                    "task_id", "state", "reason", "error", "planning", "active_actions",
                    "steps", "queued", "planner_calls", "cache_hits", "cache_misses",
                    "last_planning",
                )
            },
            "busy": busy,
            "robot": None if robot is None else self._robot_view(robot),
            "skills": self.skills,
            "planner": self.planner,
            "voice": dict(self.voice),
            "viewer": dict(self.viewer),
            "errors": dict(self.errors),
            "events": self.hub.last,
        }

    @staticmethod
    def _robot_view(snapshot):
        data = snapshot.data
        view = {
            "node_id": snapshot.node_id, "admission": snapshot.admission,
            "stop_confirmed": snapshot.stop_confirmed, "active_action": snapshot.active_action,
            "control_epoch": snapshot.control_epoch,
        }
        if data.kind == "scene":
            kinematics = data.robot.kinematics
            view.update(
                pose=data.robot.pose, held_object=data.robot.held_object,
                objects={name: obj.label or name for name, obj in data.objects.items()},
                facts=dict(data.facts),
                tcp_pos=kinematics.tcp_pos if kinematics else None,
                gripper_width=kinematics.gripper_width if kinematics else None,
                qs=kinematics.qs if kinematics else None,
            )
        return view

    # ------------------------------------------------------------- operator input

    def _reply(self, ok, message, **extra):
        self.hub.emit("reply" if ok else "error", text=message)
        return {"ok": ok, "message": message, **extra}

    async def open(self, mode=None):
        if mode:
            result = await self.set_mode(mode)
            if not result["ok"]:
                return result
        self.opened = True
        self._publish()
        return {"ok": True, "message": ""}

    async def _prepare_robot(self):
        """Refuse new motion on an unconfirmed robot; reopen admission after a confirmed stop."""
        name = self.online("wrs")
        if name is None:
            return None
        current = await self.system.snapshot(node=name)
        if current.admission == "UNKNOWN" or not current.stop_confirmed:
            return "机器人状态未确认，拒绝新任务；先点“立即停止”。"
        if current.admission == "HELD":
            receipt = await self.system.allow_actions(node=name)
            if not receipt.accepted:
                return f"未能允许新动作（{receipt.phase}）。"
            self.hub.emit("reply", text="机器人处于停止保持状态，已允许新动作。")
        return None

    async def send_text(self, text, *, kind="text"):
        text = text.strip()
        if not text:
            return {"ok": False, "message": "请先输入内容。"}
        self.hub.emit("user", text=text, kind=kind)
        intent, reason = text_intent(TextInput(text=text))
        if intent == "stop":
            result = await self.stop(echo=False)
            if reason:
                self.hub.emit("reply", text=TEXT_REASONS.get(reason, reason))
            return result
        if intent in {"ignore", "clarify"}:
            return self._reply(False, "未提交：" + TEXT_REASONS.get(reason, reason))
        try:
            if intent == "query":
                return self._reply(True, self._summary())
            if intent == "cancel_tts":
                if not self.online("voice"):
                    return self._reply(False, "没有在线的 voice/tts 节点，无需停止播报。")
                receipt = await self.system.send_text(text)
                return self._reply(receipt.accepted, f"停止播报：{receipt.phase or receipt.reason}")
            if not self.online("agent"):
                return self._reply(False, "需要在线的 agent 节点来规划目标。")
            if self.state.get("busy"):
                return self._reply(False, "上一条还在规划或执行；先点“立即停止”再下新目标。")
            refused = await self._prepare_robot()
            if refused:
                return self._reply(False, refused)
            handle = await self.system.goal(text)
            return self._reply(True, f"目标已交给 Agent 规划（{handle.request_id[:8]}）。",
                               request_id=handle.request_id)
        except Exception as exc:
            return self._reply(False, describe(exc))

    async def run_skill(self, skill, args):
        spec = next((item for item in self.skills if item["name"] == skill), None)
        if spec is None:
            return {"ok": False, "message": f"没有已发现的技能 {skill}。"}
        try:
            args = coerce_args(spec["parameters"], args)
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        shown = ", ".join(f"{key}={value}" for key, value in args.items())
        self.hub.emit("user", text=f"{skill}({shown})", kind="skill")
        if not self.online("agent"):
            return self._reply(False, "需要在线的 agent 节点来执行任务。")
        try:
            refused = await self._prepare_robot()
            if refused:
                return self._reply(False, refused)
            task = await self.system.start(step(skill, **args))
            return self._reply(True, f"任务已启动（{task.id[:8]}）。", task_id=task.id)
        except Exception as exc:
            return self._reply(False, describe(exc))

    async def stop(self, *, echo=True):
        """Cancel through Agent first; with no task bound, fence the robot itself."""
        if echo:
            self.hub.emit("user", text="立即停止", kind="control")
        try:
            if self.online("agent"):
                receipt = await self.system.agent.request(
                    "request/task/interrupt", {"request_id": new_id()},
                    control=True, timeout=0.5,
                )
                if receipt.get("task_id") is not None:
                    return self._reply(True, f"已请求停止任务（{receipt['phase']}）。")
            name = self.online("wrs")
            if name is None:
                return self._reply(True, "没有正在执行的任务。")
            _, client = await self.system._action_client(name)
            state = await client.snapshot(control=True)
            receipt = await client.control("hold", ControlRequest(
                interrupt_id=new_id(), boot_id=state.boot_id, control_epoch=state.control_epoch,
            ))
            return self._reply(
                receipt.phase in {"STOPPED", "STOPPING"},
                f"机器人停止保持：{receipt.phase}。新任务会先自动允许动作。",
            )
        except Exception as exc:
            return self._reply(False, "停止未确认：" + describe(exc))

    async def allow(self):
        name = self.online("wrs")
        if name is None:
            return {"ok": False, "message": "没有在线的机器人节点。"}
        try:
            receipt = await self.system.allow_actions(node=name)
            return self._reply(receipt.accepted, f"允许动作：{receipt.phase}")
        except Exception as exc:
            return self._reply(False, describe(exc))

    async def set_mode(self, mode):
        name = self.online("wrs")
        if name is None:
            return {"ok": False, "message": "没有在线的机器人节点。"}
        try:
            receipt = await self.system.set_robot_mode(mode, node=name)
            label = "真机" if receipt.mode == "real" else "虚拟"
            return self._reply(
                receipt.accepted,
                f"已切换到{label}模式；动作保持停止，需要时点“允许动作”。"
                if receipt.accepted else f"未切换模式：{receipt.reason}",
            )
        except Exception as exc:
            return self._reply(False, describe(exc))

    async def configure_planner(self, settings):
        if not self.online("agent"):
            return {"ok": False, "message": "需要在线的 agent 节点。"}
        allowed = {"enabled", "protocol", "base_url", "model", "api_key", "proxy"}
        payload = {
            key: (value.strip() if isinstance(value, str) else value)
            for key, value in settings.items() if key in allowed and value not in ("", None)
        }
        payload["enabled"] = bool(settings.get("enabled"))
        try:
            self.planner = await self.system.agent.request(
                "request/agent/planner/configure", payload, timeout=10,
            )
        except Exception as exc:
            gap = _FORM_GAPS.get(getattr(exc, "code", ""))
            if gap is not None:
                return self._reply(False, gap)
            return self._reply(False, "大模型设置失败：" + describe(exc))
        self._publish()
        if not self.planner["enabled"]:
            return self._reply(True, "已关闭大模型规划；仍可在“技能”页直接执行技能。")
        source = "页面输入" if self.planner["key_source"] == "ui" else "Agent 环境变量"
        return self._reply(
            True, f"大模型规划已启用：{self.planner['model']}（API Key 来自{source}）。",
        )

    async def listen_begin(self):
        if not self.online("asr"):
            return {"ok": False, "message": "没有在线的 asr 节点。"}
        if self._press is not None:
            return {"ok": False, "message": "上一句还在识别。"}
        press_id = new_id()
        robot = self.robot
        self._press = {
            "id": press_id,
            "authority": robot and (robot.boot_id, robot.control_epoch),
        }
        try:
            await self.system.listen_begin(press_id)
        except Exception as exc:
            self._press = None
            return self._reply(False, "无法开始录音：" + describe(exc))
        self.voice.update(listening=True, recognizing=False)
        return {"ok": True, "message": "正在录音，松开结束。", "press_id": press_id}

    async def listen_end(self):
        press = self._press
        if press is None:
            return {"ok": False, "message": "没有正在进行的录音。"}
        self.voice.update(listening=False, recognizing=True)
        try:
            await self.system.listen_end(press["id"])
        except Exception as exc:
            self._press = None
            self.voice["recognizing"] = False
            return self._reply(False, "录音结束失败：" + describe(exc))
        self._background(self._finish_press(press))
        return {"ok": True, "message": "识别中…"}

    async def _finish_press(self, press):
        try:
            async with asyncio.timeout(60):
                while True:
                    result = await self.system.listen_result(press["id"])
                    if not result.capturing:
                        break
                    await asyncio.sleep(0.1)
        except Exception as exc:
            self._reply(False, "语音识别失败：" + describe(exc))
            return
        finally:
            self._press = None
            self.voice["recognizing"] = False
        self.voice["heard"] = result.text
        if result.receipt is not None:
            # ASR already sent this stop straight to the voice node.
            self.hub.emit("user", text=result.text, kind="voice")
            receipt = result.receipt
            self._reply(receipt.accepted, f"语音停止：{receipt.phase or receipt.reason}")
        elif not result.text:
            fallback = f"没有识别到内容（{result.reason}）"
            self._reply(False, VOICE_REASONS.get(result.reason, fallback))
        elif result.reason:
            self.hub.emit("user", text=result.text, kind="voice")
            self._reply(False, VOICE_REASONS.get(result.reason, f"未送达：{result.reason}"))
        else:
            robot = self.robot
            if press["authority"] and robot and press["authority"] != (
                robot.boot_id, robot.control_epoch,
            ):
                self.hub.emit("user", text=result.text, kind="voice")
                self._reply(False, "录音期间机器人控制权限变化，请重说一次。")
                return
            await self.send_text(result.text, kind="voice")

    async def speak(self, text):
        text = text.strip()[:240]
        if not text:
            return {"ok": False, "message": "没有要播报的内容。"}
        if not self.online("tts"):
            return {"ok": False, "message": "没有在线的 tts 节点。"}
        try:
            await self.system.action("speak", text=text)
        except Exception as exc:
            return {"ok": False, "message": "播报失败：" + describe(exc)}
        return {"ok": True, "message": ""}

    def _summary(self):
        task = self.state.get("task")
        if not task:
            return "Agent 不在线，无法查询任务。"
        if task["task_id"] is None:
            return f"当前没有任务；规划状态 {task['planning']}。"
        done = "；".join(f"{name}={state}" for name, state in task["steps"].items()) or "无"
        return f"任务 {task['task_id'][:8]}：{task['state']}；已完成步骤 {done}。"


def coerce_args(schema, args):
    """Form fields arrive as text; convert them using the skill's own JSON schema."""
    properties = schema.get("properties", {})
    result = {}
    for name, raw in (args or {}).items():
        if name not in properties:
            raise ValueError(f"未知参数 {name}")
        if raw is None or raw == "":
            continue
        kind = properties[name].get("type")
        try:
            if kind == "number":
                value = float(raw)
            elif kind == "integer":
                value = int(raw)
            elif kind == "boolean":
                value = raw if isinstance(raw, bool) else str(raw).lower() in {"1", "true", "yes"}
            else:
                value = str(raw)
        except (TypeError, ValueError):
            raise ValueError(f"参数 {name} 应为 {kind}") from None
        result[name] = value
    missing = [name for name in schema.get("required", []) if name not in result]
    if missing:
        raise ValueError("缺少参数：" + "、".join(missing))
    return result
