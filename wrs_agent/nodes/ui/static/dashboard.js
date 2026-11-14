const $ = (id) => document.getElementById(id);

const transcript = $("transcript");
const timeline = $("timeline");
let afterId = 0;
let evN = 0;
let stepN = 0;
let toolGroup = null;
let monitorOpen = false;
let stayOnLauncher = false;
let skillsKey = "";
let last = null;

const ROLE_LABEL = { wrs: "机器人", agent: "规划", voice: "语音路由", tts: "播报", asr: "识别" };
const HEALTH_LABEL = {
  ready: "就绪", held: "停止保持", unknown: "未知", offline: "离线", stale: "过期", unsupported: "不支持",
};

function esc(text) {
  return String(text ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function short(id) {
  return id ? String(id).slice(0, 8) : "";
}

function fmt(value) {
  if (value == null) return "—";
  if (Array.isArray(value)) return value.map((v) => (typeof v === "number" ? v.toFixed(3) : v)).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(3);
  return String(value);
}

async function post(path, body = {}) {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await res.json();
  } catch {
    return { ok: false, message: "UI 节点没有响应。" };
  }
}

function healthClass(health) {
  if (health === "ready") return "runtime-ready";
  if (health === "held") return "runtime-held";
  if (health === "offline" || health === "unknown" || health === "stale") return "runtime-failed";
  return "runtime-pending";
}

// ------------------------------------------------------------------ transcript

function ensureTranscript() {
  if (transcript.querySelector(".empty")) transcript.innerHTML = "";
}

function bumpEvents() {
  evN += 1;
  $("evCount").textContent = evN ? `${evN} 条事件` : "";
  if ($("autoscroll").checked) transcript.scrollTop = transcript.scrollHeight;
}

function addEv(cls, html, attrs) {
  toolGroup = null;
  ensureTranscript();
  const div = document.createElement("div");
  div.className = `ev ${cls}`;
  div.innerHTML = html;
  if (attrs) Object.assign(div.dataset, attrs);
  transcript.appendChild(div);
  bumpEvents();
  return div;
}

function appendTool(type, name, detail, isError) {
  ensureTranscript();
  if (!toolGroup) {
    const box = document.createElement("div");
    box.className = "toolgroup";
    box.innerHTML = `<div class="tg-head"><span class="tg-count">0</span> 个动作 / 步骤更新</div><div class="tg-body"></div>`;
    box.querySelector(".tg-head").addEventListener("click", () => box.classList.toggle("open"));
    transcript.appendChild(box);
    toolGroup = box;
  }
  const div = document.createElement("div");
  div.className = `ev ${type}`;
  div.innerHTML = `${type === "tool_call" ? "→" : "←"} <span class="tname${isError ? " err" : ""}">${esc(name)}</span> <span class="args">${esc(detail)}</span>`;
  toolGroup.querySelector(".tg-body").appendChild(div);
  toolGroup.querySelector(".tg-count").textContent = String(toolGroup.querySelectorAll(".tg-body .ev").length);
  bumpEvents();
}

function addStep(name, detail, cls = "") {
  if (timeline.querySelector(".empty")) timeline.innerHTML = "";
  stepN += 1;
  const row = document.createElement("div");
  row.className = "step";
  row.innerHTML = `<div class="idx">${stepN}</div><div class="body"><div class="act ${cls}">${esc(name)}</div><div class="det">${esc(detail)}</div></div>`;
  timeline.appendChild(row);
  timeline.scrollTop = timeline.scrollHeight;
  $("stepCount").textContent = `${stepN}`;
}

function resetView() {
  transcript.innerHTML = `<div class="empty">在下方输入目标或在右侧选择技能，这里会显示规划与执行过程。</div>`;
  timeline.innerHTML = `<div class="empty">还没有动作。</div>`;
  evN = 0;
  stepN = 0;
  toolGroup = null;
  $("evCount").textContent = "";
  $("stepCount").textContent = "";
}

function describeError(error) {
  if (!error) return "";
  const where = [error.node_id, error.stage].filter(Boolean).join("／");
  return `${error.code}${where ? `（${where}）` : ""}${error.message ? `：${error.message}` : ""}`;
}

const DONE_LABEL = { SUCCEEDED: "任务成功", FAILED: "任务失败", CANCELLED: "任务已取消", UNKNOWN: "任务结果无法确认" };

function handle(ev) {
  if (ev.type === "user") {
    const label = ev.kind === "skill" ? "技能" : ev.kind === "control" ? "控制" : "你";
    addEv("user", esc(ev.text), { roleLabel: label });
  } else if (ev.type === "reply") {
    addEv("text", esc(ev.text));
  } else if (ev.type === "error") {
    addEv("err", esc(ev.text));
  } else if (ev.type === "node") {
    addEv("node", `节点 <b>${esc(ev.node_id)}</b>（${esc(ev.node_type)}）${esc(HEALTH_LABEL[ev.health] || ev.health)}`);
  } else if (ev.type === "planning") {
    if (ev.state === "WAITING") {
      addEv("turn", `Agent 规划中…（${esc(short(ev.request_id))}）`);
    } else {
      const t = ev.timing || {};
      const timing = t.total_s != null ? ` · ${t.total_s.toFixed(1)} s${t.model_s == null ? "（缓存命中）" : ""}` : "";
      addEv("turn", `规划结果 ${esc(ev.state)}${esc(timing)}`);
      if (ev.reason) {
        addEv("text", esc(ev.reason));
        announce(ev, ev.reason);
      }
    }
  } else if (ev.type === "task") {
    addEv("turn", `任务 ${esc(short(ev.task_id))} 开始（${esc(ev.state)}）`);
    $("resultBadge").style.display = "none";
  } else if (ev.type === "action") {
    appendTool("tool_call", ev.skill, `动作 ${short(ev.action_id)}`);
    addStep(ev.skill, `动作 ${short(ev.action_id)} · 任务 ${short(ev.task_id)}`);
  } else if (ev.type === "step") {
    appendTool("tool_result", `步骤 ${short(ev.step)}`, ev.state, ev.state !== "SUCCEEDED");
  } else if (ev.type === "done") {
    const ok = ev.state === "SUCCEEDED";
    $("resultBadge").style.display = "";
    $("resultBadge").className = `badge ${ok ? "b-ok" : "b-fail"}`;
    $("resultBadge").textContent = DONE_LABEL[ev.state] || ev.state;
    const detail = [ev.reason, describeError(ev.error)].filter(Boolean).join("；");
    addEv(ok ? "text" : "err", esc(`${DONE_LABEL[ev.state] || ev.state}${detail ? `：${detail}` : "。"}`));
    addStep(DONE_LABEL[ev.state] || ev.state, `任务 ${short(ev.task_id)}`, ok ? "ok" : "bad");
    announce(ev, `${DONE_LABEL[ev.state] || ev.state}。`);
  }
}

// ------------------------------------------------------------------ speech

const pageOpened = Date.now() / 1000;
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

function announce(ev, text) {
  // Only results that happen while the page is open; a reload never replays old speech.
  const mode = $("f-speak").value;
  if (mode === "off" || ev.at < pageOpened || !text) return;
  if (mode === "auto" && last && last.roles.tts) {
    post("/api/speak", { text });
  } else if ("speechSynthesis" in window) {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    window.speechSynthesis.speak(utterance);
  }
}

function voiceSource() {
  const mode = $("f-voice").value;
  if (mode === "off") return null;
  if ((mode === "auto" || mode === "asr") && last && last.roles.asr) return "asr";
  if ((mode === "auto" || mode === "browser") && Recognition) return "browser";
  return null;
}

const talk = { held: false, source: null, recognition: null, text: "" };

async function talkBegin() {
  if (talk.held) return;
  const source = voiceSource();
  if (!source) {
    $("inboxStatus").textContent = "没有可用的语音输入：启动 asr 节点，或用 Edge / Chrome 打开本页。";
    $("inboxStatus").classList.add("is-error");
    return;
  }
  Object.assign(talk, { held: true, source, text: "" });
  $("talkBtn").classList.add("listening");
  $("talkBtn").textContent = "松开结束";
  if (source === "asr") {
    const data = await post("/api/listen/begin");
    if (!data.ok) {
      talk.held = false;
      resetTalk(data.message);
    }
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "zh-CN";
  recognition.interimResults = true;
  recognition.continuous = true;
  recognition.onresult = (e) => {
    talk.text = Array.from(e.results).map((r) => r[0].transcript).join("");
    $("inboxStatus").textContent = `听到：${talk.text}`;
  };
  recognition.onerror = (e) => resetTalk(`浏览器识别失败：${e.error}`);
  recognition.onend = async () => {
    const text = talk.text.trim();
    talk.recognition = null;
    resetTalk(text ? "" : "没有识别到内容。");
    if (text) await act(() => post("/api/text", { text, kind: "voice" }));
  };
  talk.recognition = recognition;
  recognition.start();
}

async function talkEnd() {
  if (!talk.held) return;
  talk.held = false;
  if (talk.source === "asr") {
    $("talkBtn").classList.remove("listening");
    $("talkBtn").classList.add("recognizing");
    $("talkBtn").textContent = "识别中…";
    const data = await post("/api/listen/end");
    if (!data.ok) resetTalk(data.message);
  } else if (talk.recognition) {
    talk.recognition.stop();
  }
}

function resetTalk(message) {
  $("talkBtn").classList.remove("listening", "recognizing");
  $("talkBtn").textContent = "按住说话";
  if (message != null) {
    $("inboxStatus").textContent = message;
    $("inboxStatus").classList.toggle("is-error", !!message);
  }
}

function renderVoice(data) {
  const asr = data.roles.asr;
  const tts = data.roles.tts;
  $("voiceHint").innerHTML = [
    `ASR 节点：${asr ? `<b>${esc(asr)}</b> 在线` : "未启动"} · TTS 节点：${tts ? `<b>${esc(tts)}</b> 在线` : "未启动"}`,
    `浏览器识别：${Recognition ? "可用（需允许麦克风；Edge 用微软在线服务，Chrome 需能访问 Google）" : "当前浏览器不支持"}`,
    `当前会使用：${({ asr: "ASR 节点", browser: "浏览器识别" })[voiceSource()] || "无可用语音输入"}`,
  ].join("<br />");
  const source = voiceSource();
  $("talkBtn").disabled = !source;
  $("talkBtn").title = source === "asr" ? `由 ${asr} 录音识别` : source === "browser" ? "浏览器识别" : "没有可用的语音输入";
  if (talk.source === "asr" && !talk.held && !data.voice.recognizing && $("talkBtn").classList.contains("recognizing")) {
    resetTalk(null);
  }
}

// ------------------------------------------------------------------ planner settings

const PRESETS = {
  glm: ["openai_chat", "https://open.bigmodel.cn/api/paas/v4", "glm-4.5"],
  deepseek: ["openai_chat", "https://api.deepseek.com", "deepseek-chat"],
  qwen: ["openai_chat", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"],
  kimi: ["openai_chat", "https://api.moonshot.cn/v1", "kimi-k2-turbo-preview"],
  openai: ["openai_responses", "https://api.openai.com/v1", "gpt-4.1-mini"],
  ollama: ["openai_chat", "http://127.0.0.1:11434/v1", "qwen2.5:7b"],
};
const SAVED = ["f-preset", "f-protocol", "f-base-url", "f-model", "f-proxy", "f-voice", "f-speak"];
let plannerDirty = false;
let plannerFilled = false;

function restoreSettings() {
  // Never the API key: it stays in the input only until the page closes.
  const saved = JSON.parse(localStorage.getItem("wrs-ui-settings") || "{}");
  for (const id of SAVED) {
    if (!(id in saved)) continue;
    if ($(id).type === "checkbox") $(id).checked = saved[id];
    else $(id).value = saved[id];
  }
  plannerFilled = "f-base-url" in saved;
}

function saveSettings() {
  const saved = {};
  for (const id of SAVED) saved[id] = $(id).type === "checkbox" ? $(id).checked : $(id).value;
  localStorage.setItem("wrs-ui-settings", JSON.stringify(saved));
}

function renderPlanner(data) {
  const p = data.planner;
  const hint = $("plannerHint");
  if (!data.roles.agent) {
    hint.textContent = "agent 节点不在线。";
  } else if (!p) {
    hint.textContent = "读取 Agent 状态…";
  } else if (p.enabled === null) {
    hint.textContent = "这个 Agent 节点不支持在页面里切换模型，请用 --live-model 重启。";
  } else if (p.enabled) {
    hint.innerHTML = `当前：<b>已启用</b> ${esc(p.model)} · ${esc(p.protocol)} · ${esc(p.base_url)} · API Key 来自${p.key_source === "ui" ? "页面输入" : "Agent 环境变量"}`;
  } else {
    const saved = p.model ? ` · Agent 环境变量里有 ${esc(p.model)}` : "";
    const sample = !$("f-base-url").value.trim() && !$("f-model").value.trim() && !p.base_url
      ? "<br />灰色斜体只是示例。要用智谱，把服务商预设改成「智谱 GLM」，地址和模型会写入输入框。"
      : "";
    hint.innerHTML = `当前：<b>未启用</b>（只能在“技能”页直接执行技能）${saved}${sample}`;
  }
  if (p && !plannerFilled && p.enabled !== null) {
    plannerFilled = true;
    if (p.protocol) $("f-protocol").value = p.protocol;
    if (p.base_url) $("f-base-url").value = p.base_url;
    if (p.model) $("f-model").value = p.model;
    if (p.proxy) $("f-proxy").value = p.proxy;
  }
  if (p && p.enabled !== null && !plannerDirty) $("f-llm").checked = !!p.enabled;
  $("f-llm").disabled = !p || p.enabled === null;
  $("brainStatus").innerHTML = p && p.enabled
    ? `大模型：<b>${esc(p.model)}</b> 已启用 · <a href="#" id="brainLink">修改</a>`
    : `大模型未启用：自然语言目标不可用，可直接用“技能”页 · <a href="#" id="brainLink">去启用</a>`;
  $("brainLink").onclick = (e) => { e.preventDefault(); showLauncher(); };
}

function plannerGap() {
  if (!$("f-llm").checked) return "";
  const known = (last && last.planner) || {};
  const gaps = [];
  if (!$("f-base-url").value.trim() && !known.base_url) gaps.push("Base URL");
  if (!$("f-model").value.trim() && !known.model) gaps.push("模型 ID");
  if (!gaps.length) return "";
  return `还没有${gaps.join("和")}。请把服务商预设改成「智谱 GLM」等厂商，或在输入框里填写。灰色斜体只是示例，不会提交。`;
}

function setLaunchStatus(text, isError) {
  const el = $("launchStatus");
  el.textContent = text || "";
  el.classList.toggle("is-error", !!isError);
}

async function applyPlanner() {
  const gap = plannerGap();
  if (gap) return { ok: false, message: gap };
  const data = await post("/api/planner", {
    enabled: $("f-llm").checked,
    protocol: $("f-protocol").value,
    base_url: $("f-base-url").value.trim(),
    model: $("f-model").value.trim(),
    api_key: $("f-key").value.trim(),
    proxy: $("f-proxy").value.trim(),
  });
  if (data.ok) {
    plannerDirty = false;
    saveSettings();
  }
  return data;
}

async function poll() {
  try {
    const data = await (await fetch(`/api/events?after=${afterId}`)).json();
    if (data.last < afterId) {
      afterId = 0;  // The UI node restarted; its event ids start over.
      resetView();
    }
    for (const ev of data.events || []) {
      afterId = Math.max(afterId, ev.id);
      handle(ev);
    }
    $("connMeta").textContent = "已连接";
  } catch {
    $("connMeta").textContent = "重连中…";
    await new Promise((r) => setTimeout(r, 1000));
  }
  setTimeout(poll, 50);
}

// ------------------------------------------------------------------ state panels

function renderLauncher(data) {
  const info = data.info;
  $("connHint").innerHTML = [
    `Router：<b>${esc(info.endpoint)}</b>`,
    `环境 env_id：<b>${esc(info.env_id)}</b> · 本 UI 节点：<b>${esc(info.node_id)}</b>`,
  ].join("<br />");
  $("nodeList").innerHTML = data.nodes.length
    ? data.nodes.map((n) => `<span class="runtime-item ${healthClass(n.health)}" title="${esc(n.error || "")}">${esc(n.node_id)} · ${esc(n.node_type)} · ${esc(HEALTH_LABEL[n.health] || n.health)}</span>`).join("")
    : `<span class="meta">还没有发现节点：确认其他节点使用同一个 Router、env_id 和 WRS_AGENT_TOKEN。</span>`;
  const v = data.viewer;
  $("viewerHint").textContent = { ready: "UI 节点已启动画面", external: "嵌入已有画面", starting: "加载中…", failed: "不可用", disabled: "嵌入外部画面" }[v.state] || v.state;
  $("viewerHint").title = v.detail || "";
  $("skillHint").textContent = data.skills.length
    ? data.skills.map((s) => s.name).join(" · ")
    : "等待动作节点…";
  const wrs = data.nodes.find((n) => n.node_id === data.roles.wrs);
  $("f-mode").disabled = !(wrs && wrs.robot_controls);
}

function renderHeader(data) {
  const task = data.task;
  const el = $("statusBadge");
  let cls = "b-stale";
  let label = "Agent 离线";
  if (task) {
    if (task.planning === "WAITING") [cls, label] = ["b-running", "规划中"];
    else if (["RUNNING", "CANCELLING", "QUEUED"].includes(task.state)) [cls, label] = ["b-running", task.state === "CANCELLING" ? "停止中" : "执行中"];
    else if (task.state === "SUCCEEDED") [cls, label] = ["b-succeeded", "成功"];
    else if (task.state === "FAILED" || task.state === "UNKNOWN") [cls, label] = ["b-failed", task.state === "UNKNOWN" ? "无法确认" : "失败"];
    else if (task.state === "CANCELLED") [cls, label] = ["b-warn", "已取消"];
    else [cls, label] = ["b-stale", "空闲"];
  }
  el.className = `badge ${cls}`;
  el.textContent = label;
  $("runtimeStatus").innerHTML = Object.entries(ROLE_LABEL).map(([role, text]) => {
    const name = data.roles[role];
    const node = data.nodes.find((n) => n.node_id === name);
    const health = node ? node.health : "offline";
    const noModel = role === "agent" && data.planner && data.planner.enabled === false;
    const held = role === "wrs" && data.robot && data.robot.admission === "HELD";
    const shown = held || (noModel && health === "ready") ? "held" : health;
    const title = noModel ? `${name}：未启用大模型` : name || "未发现";
    return `<span class="runtime-item ${node ? healthClass(shown) : "runtime-pending"}" title="${esc(title)}">${text}</span>`;
  }).join("");
  $("taskMeta").textContent = task && task.task_id
    ? `任务 ${short(task.task_id)} · 模型调用 ${task.planner_calls} 次 · 缓存 ${task.cache_hits}/${task.cache_hits + task.cache_misses}`
    : "";
  const errors = Object.values(data.errors || {});
  $("interactionStatus").textContent = errors.length
    ? `部分状态读取失败：${errors.join("；")}`
    : data.busy ? "执行中：可以随时点“立即停止”。" : "输入目标后点发送。";
  $("interactionStatus").classList.toggle("is-error", errors.length > 0);
  $("runBtn").disabled = !data.roles.agent;
  $("allowBtn").disabled = !data.roles.wrs;
}

function renderViewer(data) {
  const v = data.viewer;
  const url = data.info.viewer_url;
  $("openViewer").href = url;
  $("frameCap").textContent = {
    ready: "WRS 画面由 UI 节点按机器人快照绘制，只显示不控制",
    external: v.detail,
    starting: v.detail,
    failed: v.detail,
    disabled: `嵌入 ${url}`,
  }[v.state] || v.detail || "";
  const frame = $("sceneFrame");
  if (monitorOpen && v.state !== "starting" && v.state !== "failed" && frame.dataset.loaded !== url) {
    // A new page has no camera panel until it says so.
    $("cameraToggle").hidden = true;
    frame.src = url;
    frame.dataset.loaded = url;
  }
}

let cameraVisible = false;

function sendCamera() {
  const frame = $("sceneFrame");
  if (!frame.src) return;
  frame.contentWindow?.postMessage(
    { type: "wrs-camera-panel", visible: cameraVisible }, new URL(frame.src).origin,
  );
}

window.addEventListener("message", (e) => {
  if (e.source !== $("sceneFrame").contentWindow || e.data?.type !== "wrs-camera-panel") return;
  if (!e.data.ready) return;
  $("cameraToggle").hidden = false;
  sendCamera();
});

$("cameraToggle").onclick = () => {
  cameraVisible = !cameraVisible;
  $("cameraToggle").setAttribute("aria-pressed", String(cameraVisible));
  sendCamera();
};

function renderRobot(data) {
  const r = data.robot;
  const box = $("robot");
  if (!r) {
    box.innerHTML = `<div class="empty">没有在线的机器人节点。</div>`;
    return;
  }
  const wrs = data.nodes.find((n) => n.node_id === r.node_id);
  const rows = [
    ["节点", r.node_id],
    ["动作准入", { OPEN: "OPEN（可接受新动作）", HELD: "HELD（停止保持）", UNKNOWN: "UNKNOWN（无法确认）" }[r.admission] || r.admission],
    ["停止已确认", r.stop_confirmed ? "是" : "否"],
    ["当前动作", r.active_action || "无"],
    ["命名位姿", r.pose],
    ["抓持物体", r.held_object || "无"],
    ["TCP 位置 (m)", r.tcp_pos],
    ["夹爪开度 (m)", r.gripper_width],
    ["关节 (rad)", r.qs],
    ["场景物体", r.objects && Object.keys(r.objects).length ? Object.keys(r.objects).join("、") : "无"],
    ...Object.entries(r.facts || {}).map(([k, v]) => [k, v]),
  ];
  const modes = wrs && wrs.robot_controls
    ? `<div class="mode-row"><span class="meta">切换模式：</span><button class="ghost" data-mode="virtual">virtual</button><button class="ghost" data-mode="real">real</button><button class="ghost" data-allow="1">允许动作</button></div>`
    : "";
  box.innerHTML = `<table class="kv">${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(fmt(v))}</td></tr>`).join("")}</table>${modes}`;
  box.querySelectorAll("[data-mode]").forEach((b) => { b.onclick = () => act(() => post("/api/mode", { mode: b.dataset.mode })); });
  box.querySelectorAll("[data-allow]").forEach((b) => { b.onclick = () => act(() => post("/api/allow")); });
}

// Arguments that name an object in the loaded scene. Their choices follow that scene.
const SCENE_ARGS = {
  pick: ["object"],
  place: ["object"],
  verify: ["object"],
  reorient: ["object"],
  place_on: ["object", "surface"],
  seat_in: ["object", "container"],
  set_control: ["appliance"],
  pour: ["source", "target"],
  stir: ["tool", "vessel"],
  scoop: ["tool", "source", "target"],
  shake_off: ["object"],
};

// Kitchen dropdowns only offer roles that already succeed in the food sequence.
const KITCHEN_CHOICES = {
  pick: { object: ["bowl_support", "bowl_support_2", "skimmer"] },
  place_on: {
    object: ["bowl_support", "bowl_support_2", "skimmer"],
    surface: ["bowl_rack", "bowl_rack_2", "skimmer_holder"],
  },
  seat_in: { object: ["skimmer"], container: ["pot"] },
  pour: {
    source: ["bowl_support", "bowl_support_2", "skimmer"],
    target: ["skimmer", "bowl_support", "bowl_support_2"],
  },
  shake_off: { object: ["skimmer"] },
};

function sceneObjects(data) {
  const objects = (data.robot && data.robot.objects) || {};
  return Object.keys(objects).sort().map((name) => ({
    name,
    label: objects[name] && objects[name] !== name ? `${name}（${objects[name]}）` : name,
  }));
}

function choicesFor(skill, name, objects, data) {
  const present = (data.robot && data.robot.objects) || {};
  const allowed = present.skimmer && present.pot && KITCHEN_CHOICES[skill] && KITCHEN_CHOICES[skill][name];
  if (!allowed) return objects;
  const keep = new Set(allowed);
  return objects.filter((item) => keep.has(item.name));
}

function field(skill, name, prop, required, objects) {
  const shown = prop.title && prop.title !== name ? prop.title : name;
  const label = `${esc(shown)}${required ? " *" : ""}`;
  const title = esc(prop.description || "");
  if (prop.enum) {
    const opts = prop.enum.map((v) => `<option value="${esc(v)}"${v === prop.default ? " selected" : ""}>${esc(v)}</option>`).join("");
    return `<label title="${title}">${label}<select name="${esc(name)}">${opts}</select></label>`;
  }
  if ((SCENE_ARGS[skill] || []).includes(name)) {
    const opts = objects.length
      ? `<option value="">请选择</option>${objects.map((item) => `<option value="${esc(item.name)}">${esc(item.label)}</option>`).join("")}`
      : `<option value="">场景里没有物体</option>`;
    return `<label title="${title}">${label}<select name="${esc(name)}"${required ? " required" : ""}>${opts}</select></label>`;
  }
  if (prop.type === "boolean") {
    return `<label title="${title}">${label}<select name="${esc(name)}"><option>false</option><option${prop.default ? " selected" : ""}>true</option></select></label>`;
  }
  if (prop.type === "number" || prop.type === "integer") {
    const attrs = [
      prop.minimum != null ? `min="${prop.minimum}"` : "",
      prop.maximum != null ? `max="${prop.maximum}"` : "",
      `step="${prop.type === "integer" ? 1 : 0.01}"`,
      prop.default != null ? `value="${prop.default}"` : "",
    ].join(" ");
    return `<label title="${title}">${label}<input type="number" name="${esc(name)}" ${attrs} /></label>`;
  }
  return `<label title="${title}">${label}<input type="text" name="${esc(name)}" value="${esc(prop.default ?? "")}" /></label>`;
}

function renderSkills(data) {
  const objects = sceneObjects(data);
  const key = JSON.stringify([data.skills.map((s) => [s.name, s.version]), objects.map((item) => item.name)]);
  if (key === skillsKey) return;
  const saved = new Map();
  for (const card of $("skills").querySelectorAll("form[data-skill]")) {
    const values = {};
    for (const el of card.querySelectorAll("[name]")) values[el.name] = el.value;
    saved.set(card.dataset.skill, values);
  }
  skillsKey = key;
  const box = $("skills");
  if (!data.skills.length) {
    box.innerHTML = `<div class="empty">没有已发现的技能。</div>`;
    return;
  }
  box.innerHTML = "";
  for (const spec of data.skills) {
    const props = (spec.parameters && spec.parameters.properties) || {};
    const required = new Set((spec.parameters && spec.parameters.required) || []);
    const card = document.createElement("form");
    card.className = "skill-card";
    card.dataset.skill = spec.name;
    card.innerHTML = `<div class="name">${esc(spec.name)} <span class="meta">v${esc(spec.version)} · ${esc((spec.resources || []).join(", "))}</span></div>
      <div class="desc">${esc(spec.description)}</div>
      ${Object.keys(props).length ? `<div class="args">${Object.entries(props).map(([n, p]) => field(spec.name, n, p, required.has(n), choicesFor(spec.name, n, objects, data))).join("")}</div>` : ""}
      <button class="run" type="submit">执行</button>`;
    const previous = saved.get(spec.name) || {};
    for (const el of card.querySelectorAll("[name]")) {
      const value = previous[el.name];
      if (value == null) continue;
      if (el.tagName === "SELECT" && ![...el.options].some((option) => option.value === value)) continue;
      el.value = value;
    }
    card.onsubmit = (e) => {
      e.preventDefault();
      const args = {};
      for (const el of card.querySelectorAll("[name]")) args[el.name] = el.value;
      act(() => post("/api/skill", { skill: spec.name, args }));
    };
    box.appendChild(card);
  }
}

async function refreshState() {
  try {
    const data = await (await fetch("/api/state")).json();
    last = data;
    renderLauncher(data);
    renderHeader(data);
    renderViewer(data);
    renderRobot(data);
    renderSkills(data);
    renderPlanner(data);
    renderVoice(data);
    if (data.opened && !monitorOpen && !stayOnLauncher) showMonitor();
  } catch {
    $("connMeta").textContent = "重连中…";
  }
}

// ------------------------------------------------------------------ actions

async function act(call) {
  $("inboxStatus").textContent = "处理中…";
  $("inboxStatus").classList.remove("is-error");
  const data = await call();
  $("inboxStatus").textContent = data.ok ? "" : (data.message || "失败");
  $("inboxStatus").classList.toggle("is-error", !data.ok);
  refreshState();
  return data;
}

function showMonitor() {
  stayOnLauncher = false;
  monitorOpen = true;
  $("launcher").classList.add("hidden");
  $("app").classList.remove("hidden");
  document.title = "WRS-Agent · 实时监控";
  if (last) renderViewer(last);
}

function showLauncher() {
  stayOnLauncher = true;
  monitorOpen = false;
  $("app").classList.add("hidden");
  $("launcher").classList.remove("hidden");
  document.title = "WRS-Agent · 新建会话";
}

$("launchBtn").onclick = async () => {
  $("launchBtn").disabled = true;
  setLaunchStatus("正在进入…", false);
  saveSettings();
  let data = { ok: true };
  if (plannerDirty && last && last.roles.agent) data = await applyPlanner();
  if (data.ok) data = await post("/api/open", { mode: $("f-mode").disabled ? "" : $("f-mode").value });
  $("launchBtn").disabled = false;
  if (!data.ok) {
    setLaunchStatus(data.message || "进入失败", true);
    return;
  }
  setLaunchStatus("", false);
  showMonitor();
  refreshState();
};

$("applyBtn").onclick = async () => {
  setLaunchStatus("正在应用…", false);
  const data = await applyPlanner();
  setLaunchStatus(data.message || (data.ok ? "已应用" : "失败"), !data.ok);
  refreshState();
};

for (const id of ["f-llm", "f-protocol", "f-base-url", "f-model", "f-key", "f-proxy"]) {
  $(id).addEventListener("input", () => { plannerDirty = true; });
  $(id).addEventListener("change", () => { plannerDirty = true; });
}
$("f-preset").onchange = () => {
  const preset = PRESETS[$("f-preset").value];
  if (!preset) return;
  [$("f-protocol").value, $("f-base-url").value, $("f-model").value] = preset;
  plannerDirty = true;
};
for (const id of ["f-voice", "f-speak"]) $(id).onchange = () => { saveSettings(); if (last) renderVoice(last); };

$("talkBtn").addEventListener("pointerdown", (e) => { e.preventDefault(); talkBegin(); });
$("talkBtn").addEventListener("pointerup", talkEnd);
$("talkBtn").addEventListener("pointerleave", talkEnd);
const typing = (el) => el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.tagName === "SELECT");
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.repeat || !monitorOpen || typing(document.activeElement)) return;
  e.preventDefault();
  talkBegin();
});
document.addEventListener("keyup", (e) => {
  if (e.code !== "Space" || !monitorOpen || typing(document.activeElement)) return;
  e.preventDefault();
  talkEnd();
});
restoreSettings();

async function submitTask() {
  const text = $("chatInput").value.trim();
  if (!text) return;
  $("runBtn").disabled = true;
  const data = await act(() => post("/api/text", { text }));
  if (data.ok) $("chatInput").value = "";
  $("runBtn").disabled = false;
}

$("runBtn").onclick = submitTask;
$("stopBtn").onclick = () => act(() => post("/api/stop"));
$("allowBtn").onclick = () => act(() => post("/api/allow"));
$("newSessionBtn").onclick = showLauncher;
$("chatInput").addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter" || ev.shiftKey || ev.isComposing) return;
  ev.preventDefault();
  submitTask();
});
$("showtools").onchange = () => transcript.classList.toggle("alltools", $("showtools").checked);
$("shownodes").onchange = () => transcript.classList.toggle("nonodes", !$("shownodes").checked);

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    for (const id of ["timeline", "skills", "robot"]) $(id).classList.toggle("hidden", id !== tab.dataset.tab);
  };
});

function dragGutter(gutter, onMove) {
  gutter.addEventListener("pointerdown", (down) => {
    down.preventDefault();
    gutter.setPointerCapture(down.pointerId);
    document.body.classList.add("dragging");
    const move = (ev) => onMove(ev);
    const up = () => {
      gutter.removeEventListener("pointermove", move);
      document.body.classList.remove("dragging");
    };
    gutter.addEventListener("pointermove", move);
    gutter.addEventListener("pointerup", up, { once: true });
  });
}

const mainEl = document.querySelector("main");
dragGutter(document.querySelector(".gutter-v"), (ev) => {
  const rect = mainEl.getBoundingClientRect();
  const left = Math.min(Math.max(ev.clientX - rect.left, 280), rect.width - 280);
  mainEl.style.setProperty("--leftw", `${left}px`);
});
dragGutter(document.querySelector(".gutter-h"), (ev) => {
  const rect = document.querySelector(".col.right").getBoundingClientRect();
  const top = Math.min(Math.max(ev.clientY - rect.top, 160), rect.height - 120);
  document.querySelector(".col.right").style.setProperty("--frameh", `${top}px`);
});

refreshState();
setInterval(refreshState, 700);
poll();
