// Read-only camera panel added to the existing WRS page. No robot commands or credentials.
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
    #camera-panel img,#camera-panel canvas {width:100%;height:100%;object-fit:contain;position:absolute}
    #camera-panel canvas {touch-action:none;cursor:grab}
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
  <div class="viewport"><img hidden alt="相机画面"><canvas hidden width="720" height="540"></canvas>
    <div class="empty">等待相机节点连接</div></div>
  <footer><div class="detail">未收到观测</div><small class="hint">相机未连接时，机器人界面仍可使用。</small></footer>`;
document.body.append(panel);
const status = panel.querySelector(".status"), detail = panel.querySelector(".detail");
const hint = panel.querySelector(".hint"), empty = panel.querySelector(".empty");
const img = panel.querySelector("img"), canvas = panel.querySelector("canvas");
const ctx = canvas.getContext("2d");
let current = null, selected = "rgb", yaw = 0.2, pitch = -0.15, zoom = 650, drag = null;
const states = {connecting:"连接中", offline:"节点未连接", disconnected:"相机未连接",
  stale:"观测已过期", unavailable:"观测不可用"};
function drawPoints() {
  ctx.fillStyle = "#e9eeeb"; ctx.fillRect(0,0,canvas.width,canvas.height);
  if (!current?.points.length) return;
  const points = current.points, center = [0,0,0];
  for (const p of points) for(let j=0;j<3;j++) center[j] += p[j] / points.length;
  const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
  for(let i=0;i<points.length;i++) {
    const [x,y,z] = points[i].map((v,j)=>v-center[j]);
    const px=cy*x+sy*z, pz=-sy*x+cy*z, py=cp*y-sp*pz;
    ctx.fillStyle = `rgb(${current.colors[i].join(",")})`;
    ctx.fillRect(canvas.width/2+px*zoom,canvas.height/2+py*zoom,3,3);
  }
}
function render() {
  const ready = current?.state === "ready";
  img.hidden = !ready || selected === "points";
  canvas.hidden = !ready || selected !== "points";
  empty.hidden = ready;
  if (!ready) {
    img.removeAttribute("src"); ctx.clearRect(0,0,canvas.width,canvas.height);
    status.textContent = states[current?.state] || "连接中";
    empty.textContent = status.textContent + "。连接恢复后自动更新。";
    detail.textContent = current?.state === "offline" ? "请启动相机节点。"
      : current?.state === "disconnected" ? "请检查相机连接及节点配置。" : "等待有效观测。";
    const target = current?.connection;
    hint.textContent = target
      ? `查询 ${target.endpoint} · ${target.site}/${target.env_id} · ${target.node_id}。请核对相机节点的地址、环境和会话口令。`
      : "相机未连接时，机器人界面仍可使用。";
    return;
  }
  status.textContent = current.source === "synthetic" ? "合成测试画面" : "已连接";
  detail.textContent = `${current.width} × ${current.height} · ${current.frame_id}`;
  hint.textContent = selected === "points" ? "相机坐标系（米）· 拖动旋转，滚轮缩放；非机器人世界坐标。"
    : selected === "depth" ? "深度颜色范围 0–0.6 m；黑色表示无有效深度。" : "与深度、点云来自同一观测。";
  if (selected === "points") drawPoints(); else img.src = current[selected];
}
for(const button of panel.querySelectorAll("button")) button.onclick=()=>{
  selected=button.dataset.view;
  for(const b of panel.querySelectorAll("button")) b.setAttribute("aria-pressed",String(b===button));
  render();
};
canvas.onpointerdown=e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);};
canvas.onpointermove=e=>{
  if(!drag) return;
  yaw+=(e.clientX-drag[0])*0.01; pitch+=(e.clientY-drag[1])*0.01;
  drag=[e.clientX,e.clientY];drawPoints();
};
canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;};
canvas.addEventListener("wheel",e=>{
  e.preventDefault();zoom=Math.max(100,Math.min(6000,zoom*Math.exp(-e.deltaY*0.001)));drawPoints();
},{passive:false});
async function poll() {
  try {
    const response=await fetch("/camera/preview",{cache:"no-store",signal:AbortSignal.timeout(1500)});
    if(!response.ok) throw Error("preview unavailable");
    current=await response.json();
  } catch {current={state:"offline",reason:"页面相机服务无响应"};}
  render();
  setTimeout(poll,250); // At most one request in flight, including while disconnected.
}
poll();
