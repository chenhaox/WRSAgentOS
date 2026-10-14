// Read-only camera panel added to the existing WRS page. No robot commands or credentials.
import { CameraScene } from "./scene.js";

const scene = new CameraScene();
const panel = document.createElement("section");
panel.id = "camera-panel";
panel.setAttribute("aria-label", "相机观测");
panel.innerHTML = `
  <style>
    #camera-panel {position:fixed;bottom:20px;right:20px;width:min(380px,calc(100vw - 40px));
      z-index:20;background:#fffffff5;border:1px solid #cbd5d1;border-radius:12px;
      box-shadow:0 6px 24px #102a231c;color:#183c33;font:13px system-ui,sans-serif;overflow:hidden}
    #camera-panel header {display:flex;justify-content:space-between;align-items:center;
      padding:12px 16px;border-bottom:1px solid #e1e8e4}
    #camera-panel strong {font-size:15px} #camera-panel small {color:#60766d}
    #camera-panel nav {display:flex;gap:6px;padding:10px 12px}
    #camera-panel button {background:#edf2ef;border:0;border-radius:6px;padding:6px 12px;
      cursor:pointer;color:#35574b} #camera-panel button[aria-pressed=true] {background:#176958;color:white}
    #camera-panel .viewport {aspect-ratio:4/3;background:#e9eeeb;display:grid;place-items:center;
      position:relative;margin:0 12px;border-radius:8px;overflow:hidden}
    #camera-panel img {width:100%;height:100%;object-fit:contain;position:absolute}
    #camera-panel .scene-controls {padding:0 12px 8px;display:flex;gap:6px;flex-wrap:wrap}
    #camera-panel .scene-note {padding:8px 14px;line-height:1.7;background:#edf2ef}
    #camera-panel [hidden] {display:none!important}
    #camera-panel .empty {padding:24px;text-align:center;color:#60766d;line-height:1.8}
    #camera-panel footer {padding:10px 14px;line-height:1.6;overflow-wrap:anywhere}
  </style>
  <header><strong>相机观测</strong><small class="status" role="status">连接中</small></header>
  <nav aria-label="观测类型">
    <button data-view="rgb" aria-pressed="true">RGB</button>
    <button data-view="depth" aria-pressed="false">深度</button>
    <button data-view="points" aria-pressed="false">点云</button>
  </nav>
  <div class="scene-controls" hidden>
    <button data-source="live" aria-pressed="true">实时点云</button>
    <button data-source="grasps" aria-pressed="false">抓取候选</button>
    <button class="focus">定位点云</button>
  </div>
  <div class="scene-note" hidden></div>
  <div class="viewport"><img hidden alt="相机画面">
    <div class="empty">等待相机节点连接</div></div>
  <footer><div class="detail">未收到观测</div><small class="hint">相机未连接时，机器人界面仍可使用。</small></footer>`;
document.body.append(panel);
const status = panel.querySelector(".status"), detail = panel.querySelector(".detail");
const hint = panel.querySelector(".hint"), empty = panel.querySelector(".empty");
const img = panel.querySelector("img"), viewport = panel.querySelector(".viewport");
const controls = panel.querySelector(".scene-controls"), note = panel.querySelector(".scene-note");
let current = null, selected = "rgb", graspMode = false;
let previousFrame = null, paintedFrame = null, received = 0, fps = 0, began = performance.now();
const states = {connecting:"连接中", offline:"节点未连接", disconnected:"相机未连接",
  stale:"观测已过期", unavailable:"观测不可用"};
const requestedView = () => selected === "points" && graspMode ? "grasps" : selected;
function render() {
  const ready = current?.state === "ready" && current.view === requestedView();
  viewport.hidden = selected === "points";
  controls.hidden = note.hidden = selected !== "points";
  img.hidden = !ready || selected === "points";
  empty.hidden = ready;
  if (!ready) {
    paintedFrame = null;
    img.removeAttribute("src"); scene.clear();
    status.textContent = states[current?.state] || "连接中";
    empty.textContent = status.textContent + "。连接恢复后自动更新。";
    detail.textContent = current?.state === "offline" ? "请启动相机节点。"
      : current?.state === "disconnected" ? "请检查相机连接及节点配置。" : "等待有效观测。";
    const target = current?.connection;
    if (selected === "points" && graspMode) {
      status.textContent = current?.state === "offline" ? "抓取节点未连接" : "暂无有效抓取候选";
      detail.textContent = "完成一次 infer_grasps 后可查看；查看不会触发推理。";
      hint.textContent = target
        ? `查询 ${target.endpoint} · ${target.env_id}/${current.grasp_node_id}。结果过期或取消后清除。`
        : "等待 GraspNet 结果。";
      note.textContent = status.textContent;
      return;
    }
    note.textContent = empty.textContent;
    hint.textContent = target
      ? `查询 ${target.endpoint} · ${target.site}/${target.env_id} · ${target.node_id}。请核对相机节点的地址、环境和会话口令。`
      : "相机未连接时，机器人界面仍可使用。";
    return;
  }
  const source = current.source === "synthetic" ? "合成测试画面" : "已连接";
  status.textContent = graspMode && selected === "points"
    ? `${source} · 抓取快照` : `${source} · 接收 ${fps.toFixed(1)} FPS`;
  detail.textContent = `${current.width} × ${current.height} · ${current.frame_id}`;
  hint.textContent = selected === "points" ? "相机坐标系（米），未标定；展示位置不代表与机器人对齐。"
    : selected === "depth" ? "深度颜色范围 0–0.6 m；黑色表示无有效深度。" : "与深度、点云来自同一观测。";
  const key = `${selected}/${current.observation_id}`;
  if (selected === "points") {
    img.removeAttribute("src");
    try {
      note.textContent = scene.update(current)
        ? "已显示在主场景相机展示区；拖动场景旋转，滚轮缩放。" : "等待主场景就绪…";
    } catch {scene.clear();note.textContent="主场景绘制失败，请刷新页面。";}
    if (graspMode) {
      detail.textContent = `${current.target} · 显示 ${current.grasps.length}/${current.grasp_count} 个候选`
        + ` · 推理帧 ${current.age_s.toFixed(1)} 秒前`;
      hint.textContent = "绿：最高模型分数；橙：其他候选。夹爪为示意，未做碰撞检查。"
        + "点云与抓姿属于同一观测；相机展示区未标定到机器人。";
    }
    return;
  }
  scene.clear();
  if (key !== paintedFrame) {
    img.src = current[selected];
    paintedFrame = key;
  }
}
function resetView() {
  current=null; previousFrame=null; received=0; fps=0; began=performance.now();
  scene.clear();
  render();
}
for(const button of panel.querySelectorAll("button[data-view]")) button.onclick=()=>{
  selected=button.dataset.view;
  for(const b of panel.querySelectorAll("button[data-view]")) {
    b.setAttribute("aria-pressed",String(b===button));
  }
  resetView();
};
for(const button of panel.querySelectorAll("button[data-source]")) button.onclick=()=>{
  graspMode=button.dataset.source==="grasps";
  for(const b of panel.querySelectorAll("button[data-source]")) {
    b.setAttribute("aria-pressed",String(b===button));
  }
  resetView();
};
panel.querySelector(".focus").onclick=()=>scene.focus();
window.addEventListener("pagehide",()=>scene.clear());
async function poll() {
  const started=performance.now(), view=requestedView();
  try {
    const response=await fetch(`/camera/preview?view=${view}`,
      {cache:"no-store",signal:AbortSignal.timeout(1500)});
    if(!response.ok) throw Error("preview unavailable");
    const value=await response.json();
    if(view===requestedView()) {
      current=value;
      if(current.state==="ready" && current.observation_id!==previousFrame) {
        received++; previousFrame=current.observation_id;
      }
    }
  } catch {
    if(view===requestedView()) current={state:"offline",reason:"页面相机服务无响应"};
  }
  if(view===requestedView()) {
    const now=performance.now();
    if(current?.state!=="ready") {fps=0;received=0;previousFrame=null;began=now;}
    else if(now-began>=1000) {fps=received*1000/(now-began);received=0;began=now;}
    render();
  }
  // One request in flight; subtract work time instead of adding a delay after every frame.
  const interval=current?.state==="ready" ? 1000/current.preview_fps : 250;
  setTimeout(poll,view!==requestedView() ? 0 : Math.max(1,interval-(performance.now()-started)));
}
poll();
