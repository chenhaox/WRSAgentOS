/** Kitchen poses only: edits go to the active 05 session, never to the defaults. */
const panel = document.createElement('aside');
panel.className = 'kitchen-editor';
panel.setAttribute('aria-label', '厨具摆放编辑器');
panel.innerHTML = `
  <header class="ke-header">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 8 0 9 9 5 9-5V8M12 13v9"/></svg>
    <div class="ke-heading"><h2>厨具摆放</h2><p>本次运行有效 · 重启 05 恢复默认</p></div>
    <button class="ke-collapse" aria-label="收起编辑器" aria-expanded="true" aria-controls="ke-body"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m4 10 4-4 4 4"/></svg></button>
  </header>
  <div id="ke-body" class="ke-body">
    <label class="ke-field-label" for="ke-object">选择厨具 / 物体</label>
    <select id="ke-object" disabled><option>正在连接场景…</option></select>
    <p class="ke-id" id="ke-id">读取本次运行的厨具配置</p>
    <fieldset class="ke-section"><legend>位置 <span class="ke-unit">/ 米</span></legend>
      <div class="ke-vector">${['X（红）', 'Y（绿）', 'Z（蓝）'].map((axis, i) => `
        <label class="ke-axis-${'xyz'[i]}">${axis}<input type="number" step="0.01" data-position="${i}" aria-label="位置 ${axis}，单位米" disabled></label>`).join('')}
      </div>
    </fieldset>
    <fieldset class="ke-section"><legend>朝向 <span class="ke-unit">/ 度</span></legend>
      <div class="ke-rotation">
        <div><span class="ke-control-name">X / Y 旋转摇杆</span>
          <div class="ke-stick" id="ke-stick" role="group" tabindex="0" aria-label="X/Y旋转摇杆，左右调整X，上下调整Y；方向键每次1度，Shift加速" aria-describedby="ke-drag-hint" aria-disabled="true">
            <span class="ke-stick-x">X+</span><span class="ke-stick-y">Y+</span><span class="ke-stick-handle"></span>
          </div>
        </div>
        <div><span class="ke-control-name">Z（蓝）旋转旋钮</span>
          <div class="ke-dial" id="ke-dial" role="slider" tabindex="0" aria-label="绕Z轴旋转，拖动旋钮或使用左右方向键" aria-valuemin="-180" aria-valuemax="180" aria-valuenow="0" aria-disabled="true">
            <span class="ke-dial-arm"></span><output>0°</output>
          </div>
        </div>
      </div>
      <div class="ke-vector">${['X（红）', 'Y（绿）', 'Z（蓝）'].map((axis, i) => `
        <label class="ke-axis-${'xyz'[i]}">绕 ${axis}<input type="number" step="1" data-rotation="${i}" aria-label="绕${axis}旋转，单位度" disabled></label>`).join('')}
      </div>
      <p class="ke-hint" id="ke-drag-hint">拖动即调整，松手停在新角度。数字可精调。</p>
    </fieldset>
    <div class="ke-context"><p id="ke-parent">坐标系：—</p><p class="ke-world" id="ke-world">世界位置：—</p></div>
    <div class="ke-actions"><button id="ke-reset" disabled>恢复所选默认</button><button id="ke-refresh" disabled>重新读取配置</button></div>
    <details class="ke-help"><summary>坐标与组合关系说明</summary>
      <p>位置单位为米：0.01 米＝1 厘米。有父物体时，数值相对于父物体；没有父物体时，相对于场景原点。</p>
      <p>移动父物体会带动子物体。世界坐标中，+X 为机械臂前方，+Y 为左侧，+Z 为上方；父物体旋转后，其子物体的坐标轴也跟着转。</p>
      <p>旋转中心是 STL 模型原点；只有原点在模型中心时，旋转才会保持外形中心不动。恢复默认只恢复所选物体的配置，父物体的改动会继续影响它。</p>
    </details>
  </div>
  <footer class="ke-footer"><p class="ke-status" role="status" aria-live="polite"><span class="ke-dot" aria-hidden="true"></span><span id="ke-status">正在连接编辑器…</span></p></footer>`;
document.body.appendChild(panel);

const $ = selector => panel.querySelector(selector);
const select = $('#ke-object');
const positionInputs = [...panel.querySelectorAll('[data-position]')];
const rotationInputs = [...panel.querySelectorAll('[data-rotation]')];
const stick = $('#ke-stick');
const dial = $('#ke-dial');
const reset = $('#ke-reset');
const refresh = $('#ke-refresh');
const status = $('#ke-status');
let socket, state, selected, draft, pending, inFlight, sendTimer, dragging;
let online = false;

const rounded = (value, digits = 6) => Number(value.toFixed(digits));
const angle = value => rounded(((value + 180) % 360 + 360) % 360 - 180, 3);
const object = () => state?.objects.find(item => item.id === selected);

function feedback(message, error = false) {
  status.textContent = message;
  panel.dataset.error = String(error);
}

function enabled() {
  const ready = online && Boolean(object());
  // Keep the selected object stable while a drag or its final write is in flight.
  select.disabled = !ready || Boolean(pending || inFlight || dragging);
  for (const input of [...positionInputs, ...rotationInputs]) input.disabled = !ready;
  stick.setAttribute('aria-disabled', String(!ready));
  dial.setAttribute('aria-disabled', String(!ready));
  reset.disabled = refresh.disabled = !ready || Boolean(pending || inFlight || dragging);
  panel.dataset.online = String(online);
}

function paint() {
  if (!draft) return;
  for (const [i, input] of positionInputs.entries()) {
    if (document.activeElement !== input) input.value = rounded(draft.pos[i]);
  }
  for (const [i, input] of rotationInputs.entries()) {
    if (document.activeElement !== input) input.value = rounded(draft.rpy_deg[i], 3);
  }
  const z = angle(draft.rpy_deg[2]);
  // Positive Z is counterclockwise viewed from above; CSS rotation is clockwise.
  dial.style.setProperty('--angle', `${-z}deg`);
  dial.setAttribute('aria-valuenow', String(z));
  dial.setAttribute('aria-valuetext', `${z} 度`);
  dial.querySelector('output').textContent = `${rounded(z, 1)}°`;
}

function adopt(next, preserveDraft = false) {
  const first = !state || state.session !== next.session;
  state = next;
  if (first) {
    select.replaceChildren(...next.objects.map(item => {
      const option = document.createElement('option');
      option.value = item.id;
      option.textContent = item.label || item.id;
      return option;
    }));
    selected = next.objects.some(item => item.id === selected) ? selected
      : (next.objects.find(item => item.id === 'pot') || next.objects[0])?.id;
  }
  const item = object();
  if (!item) { enabled(); return; }
  select.value = selected;
  $('#ke-id').textContent = `${item.id}${item.parent ? ' · 组合中的子物体' : ' · 世界坐标'} `;
  const parent = next.objects.find(entry => entry.id === item.parent);
  $('#ke-parent').textContent = item.parent
    ? `相对于：${parent?.label || item.parent}。整套移动请选父物体。`
    : '坐标系：世界坐标（整个场景原点）';
  $('#ke-world').textContent = `世界位置：[${item.world_pos.map(v => rounded(v, 3)).join(', ')}] 米`;
  if (!preserveDraft) draft = { pos: [...item.pos], rpy_deg: [...item.rpy_deg] };
  paint();
  enabled();
}

function send(command) {
  if (!online || inFlight || socket.readyState !== WebSocket.OPEN) return false;
  inFlight = command;
  socket.send(JSON.stringify(command));
  enabled();
  return true;
}

function flush() {
  clearTimeout(sendTimer);
  if (!pending || !online || inFlight) return;
  const edit = pending;
  pending = null;
  send({ type: 'set', ...edit, revision: state.revision, session: state.session });
}

function changed() {
  pending = { id: selected, pos: [...draft.pos], rpy_deg: [...draft.rpy_deg] };
  paint();
  feedback('正在提交摆放调整…');
  enabled();
  clearTimeout(sendTimer);
  sendTimer = setTimeout(flush, 160);
}

function connect() {
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/kitchen-layout`);
  socket.onmessage = event => {
    let response;
    try { response = JSON.parse(event.data); }
    catch { feedback('编辑器返回了无效数据，请重新启动 07。', true); return; }
    const request = inFlight;
    const command = request?.type;
    inFlight = null;
    if (response.type === 'error') {
      pending = null;
      clearTimeout(sendTimer);
      endDrag();
      if (response.state) adopt(response.state);
      feedback(response.message || '修改未完成，请重新读取配置。', true);
      enabled();
      return;
    }
    if (response.type !== 'state') return;
    // A draft created while a read is in flight belongs to the old revision.
    // Only our own confirmed write may advance its revision without discarding it.
    if (state && response.revision !== state.revision && (pending || dragging)) {
      const ownWriteOnly = command === 'set' && response.objects.length === state.objects.length
        && response.objects.every(item => {
          const previous = state.objects.find(entry => entry.id === item.id);
          const expected = item.id === request.id ? request : previous;
          return expected && JSON.stringify(item.pos) === JSON.stringify(expected.pos)
            && JSON.stringify(item.rpy_deg) === JSON.stringify(expected.rpy_deg)
            && item.parent === previous?.parent;
        });
      if (!ownWriteOnly) {
        pending = null;
        clearTimeout(sendTimer);
        endDrag();
        adopt(response);
        feedback('其他窗口已修改配置；已读取最新值，请重新调整。', true);
        return;
      }
    }
    online = true;
    adopt(response, Boolean(pending || dragging));
    if (command === 'set' || command === 'reset') {
      feedback('已提交 · 节点空闲后约 1 秒内更新场景');
    } else if (!command || command === 'get' && panel.dataset.error !== 'true') {
      feedback('已连接 · 拖动或输入数字调整摆放');
    }
    if (pending) sendTimer = setTimeout(flush, 80);
  };
  socket.onclose = () => {
    const interrupted = Boolean(inFlight || pending);
    online = false;
    pending = inFlight = null;
    clearTimeout(sendTimer);
    endDrag();
    enabled();
    feedback(interrupted ? '连接中断，修改是否完成请重新读取确认。' : '编辑器离线 · 请保持 05 和 07 运行', true);
    setTimeout(connect, 1500);
  };
  socket.onerror = () => socket.close();
}

select.addEventListener('change', () => { selected = select.value; adopt(state); });
for (const [kind, inputs] of [['pos', positionInputs], ['rpy_deg', rotationInputs]]) {
  inputs.forEach((input, i) => {
    input.addEventListener('change', () => {
      const value = input.valueAsNumber;
      if (!Number.isFinite(value)) {
        input.setAttribute('aria-invalid', 'true');
        feedback('请输入有限数字，位置用米，角度用度。', true);
        return;
      }
      input.removeAttribute('aria-invalid');
      draft[kind][i] = kind === 'rpy_deg' ? angle(value) : rounded(value);
      input.value = draft[kind][i];
      changed();
    });
  });
}

function endDrag() {
  if (!dragging) return;
  const { target, pointerId } = dragging;
  dragging = null;
  target.dataset.dragging = 'false';
  if (target.hasPointerCapture(pointerId)) target.releasePointerCapture(pointerId);
  stick.querySelector('.ke-stick-handle').style.transform = '';
  enabled();
}

function startDrag(event, kind) {
  if (!online || !draft || event.button !== 0 || dragging) return;
  event.preventDefault();
  const target = event.currentTarget;
  const rect = target.getBoundingClientRect();
  const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
  const distance = Math.hypot(event.clientX - cx, event.clientY - cy);
  dragging = { kind, target, pointerId: event.pointerId,
    x: event.clientX, y: event.clientY, start: [...draft.rpy_deg],
    last: distance < 10 ? null : Math.atan2(event.clientY - cy, event.clientX - cx), total: 0 };
  target.setPointerCapture(event.pointerId);
  target.dataset.dragging = 'true';
  target.focus();
  enabled();
}

function moveDrag(event) {
  if (!dragging || event.pointerId !== dragging.pointerId || !online) return;
  const d = dragging;
  if (d.kind === 'stick') {
    const dx = event.clientX - d.x, dy = event.clientY - d.y;
    const radius = Math.max(1, Math.hypot(dx, dy) / 34);
    stick.querySelector('.ke-stick-handle').style.transform = `translate(${dx / radius}px, ${dy / radius}px)`;
    draft.rpy_deg[0] = angle(d.start[0] + dx * 0.5);
    draft.rpy_deg[1] = angle(d.start[1] - dy * 0.5);
  } else {
    const rect = dial.getBoundingClientRect();
    const dx = event.clientX - rect.left - rect.width / 2;
    const dy = event.clientY - rect.top - rect.height / 2;
    if (Math.hypot(dx, dy) < 10) return;
    const now = Math.atan2(dy, dx);
    if (d.last !== null) d.total += Math.atan2(Math.sin(now - d.last), Math.cos(now - d.last));
    d.last = now;
    draft.rpy_deg[2] = angle(d.start[2] - d.total * 180 / Math.PI);
  }
  changed();
}

for (const [target, kind] of [[stick, 'stick'], [dial, 'dial']]) {
  target.addEventListener('pointerdown', event => startDrag(event, kind));
  target.addEventListener('pointermove', moveDrag);
  target.addEventListener('pointerup', () => { endDrag(); flush(); });
  target.addEventListener('pointercancel', () => { endDrag(); flush(); });
  target.addEventListener('lostpointercapture', () => { endDrag(); flush(); });
  target.addEventListener('keydown', event => {
    if (!online || !draft || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const axis = kind === 'dial' ? 2 : (['ArrowLeft', 'ArrowRight'].includes(event.key) ? 0 : 1);
    const sign = ['ArrowRight', 'ArrowUp'].includes(event.key) ? 1 : -1;
    draft.rpy_deg[axis] = angle(draft.rpy_deg[axis] + sign * (event.shiftKey ? 5 : 1));
    changed();
  });
}
window.addEventListener('blur', () => { endDrag(); flush(); });
// The editor's arrows and wheel belong to its controls, not to the 3D camera.
for (const event of ['pointerdown', 'wheel', 'keydown', 'keyup']) {
  panel.addEventListener(event, e => e.stopPropagation());
}
reset.addEventListener('click', () => {
  if (send({ type: 'reset', id: selected, revision: state.revision, session: state.session })) feedback('正在恢复所选物体的默认配置…');
});
refresh.addEventListener('click', () => {
  for (const input of [...positionInputs, ...rotationInputs]) input.removeAttribute('aria-invalid');
  if (send({ type: 'get' })) { panel.dataset.error = 'false'; feedback('正在读取配置…'); }
});
$('.ke-collapse').addEventListener('click', event => {
  const button = event.currentTarget;
  const expanded = button.getAttribute('aria-expanded') !== 'true';
  button.setAttribute('aria-expanded', String(expanded));
  button.setAttribute('aria-label', expanded ? '收起编辑器' : '展开编辑器');
  $('#ke-body').hidden = !expanded;
});
setInterval(() => {
  if (online && !inFlight && !pending && !dragging && !panel.contains(document.activeElement)) send({ type: 'get' });
}, 2000);
enabled();
connect();
