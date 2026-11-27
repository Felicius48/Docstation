// Coordinates are thousandths of the displayed image so the same marks survive resizing and zoom.
function drawMarks(context, marks) {
  const {width, height} = context.canvas;
  const stroke = Math.max(1.5, Math.min(width, height) / 140);
  context.strokeStyle = "#ed3434";
  context.lineWidth = stroke;
  context.lineJoin = context.lineCap = "round";
  for (const [tool, x1, y1, x2, y2] of marks) {
    const x = x1 * width / 1000, y = y1 * height / 1000;
    const dx = (x2 - x1) * width / 1000, dy = (y2 - y1) * height / 1000;
    if (tool === "rect") {
      context.strokeRect(x, y, dx, dy);
    } else {
      const angle = Math.atan2(dy, dx);
      const head = Math.min(stroke * 5, Math.hypot(dx, dy) / 2);
      context.beginPath();
      context.moveTo(x, y);
      context.lineTo(x + dx, y + dy);
      for (const side of [-0.55, 0.55]) {
        context.moveTo(x + dx, y + dy);
        context.lineTo(x + dx - head * Math.cos(angle + side), y + dy - head * Math.sin(angle + side));
      }
      context.stroke();
    }
  }
}

class MarkedImage extends HTMLElement {
  connectedCallback() {
    const photo = this.querySelector("img");
    if (!photo) return;
    const layer = document.createElement("canvas");
    layer.setAttribute("aria-hidden", "true");
    this.append(layer);
    this.observer = new ResizeObserver(() => {
      if (!photo.clientWidth || !photo.clientHeight) return;
      layer.width = photo.clientWidth * devicePixelRatio;
      layer.height = photo.clientHeight * devicePixelRatio;
      drawMarks(layer.getContext("2d"), JSON.parse(this.dataset.marks));
    });
    this.observer.observe(photo);
  }
  disconnectedCallback() {
    this.observer?.disconnect();
    this.querySelector("canvas")?.remove();
  }
}
customElements.define("marked-image", MarkedImage);

export async function editMarks(file, initial) {
  const dialog = document.querySelector(".mark-dialog");
  if (dialog.open) return null;
  const canvas = dialog.querySelector("canvas");
  const context = canvas.getContext("2d");
  const status = dialog.querySelector("[data-mark-status]");
  const undo = dialog.querySelector("[data-undo]");
  const save = dialog.querySelector("[data-save]");
  const tools = dialog.querySelectorAll("[data-tool]");
  const photo = new Image();
  const url = URL.createObjectURL(file);
  const marks = initial.slice();
  const listeners = new AbortController();
  const options = {signal: listeners.signal};
  let tool = "rect", start = null, cursor = [500, 500], activePointer = null;
  let keyboard = false, ready = false;
  const result = new Promise(resolve => dialog.addEventListener("close", () => {
    listeners.abort();
    URL.revokeObjectURL(url);
    resolve(dialog.returnValue === "save" && ready ? marks : null);
  }, {once: true}));

  function redraw() {
    if (!ready) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(photo, 0, 0, canvas.width, canvas.height);
    drawMarks(context, start ? [...marks, [tool, ...start, ...cursor]] : marks);
    if (keyboard) {
      context.strokeStyle = "#0344dc";
      context.beginPath();
      context.arc(cursor[0] * canvas.width / 1000, cursor[1] * canvas.height / 1000, 6, 0, 2 * Math.PI);
      context.stroke();
    }
    undo.disabled = !marks.length && !start;
  }
  function selectTool(next) {
    tool = next;
    start = null;
    for (const button of tools) button.setAttribute("aria-pressed", String(button.dataset.tool === tool));
    redraw();
  }
  function finish() {
    if (marks.length >= 50) status.textContent = "Можно добавить до 50 отметок.";
    else if (start && (start[0] !== cursor[0] || start[1] !== cursor[1])) {
      marks.push([tool, ...start, ...cursor]);
      status.textContent = "Отметок: " + marks.length;
    }
    start = null;
    redraw();
  }
  const clamp = n => Math.max(0, Math.min(1000, Math.round(n)));
  function point(event) {
    const bounds = canvas.getBoundingClientRect();
    return [clamp((event.clientX - bounds.left) * 1000 / bounds.width),
            clamp((event.clientY - bounds.top) * 1000 / bounds.height)];
  }
  canvas.addEventListener("pointerdown", event => {
    if (!ready || event.button !== 0 || activePointer !== null) return;
    keyboard = false;
    cursor = point(event);
    if (start) { finish(); return; }
    start = cursor;
    activePointer = event.pointerId;
    canvas.setPointerCapture(activePointer);
    redraw();
  }, options);
  canvas.addEventListener("pointermove", event => {
    if (!start || (activePointer !== null && activePointer !== event.pointerId)) return;
    cursor = point(event);
    redraw();
  }, options);
  canvas.addEventListener("pointerup", event => {
    if (activePointer !== event.pointerId) return;
    activePointer = null;
    cursor = point(event);
    if (start && Math.hypot(cursor[0] - start[0], cursor[1] - start[1]) > 4) finish();
  }, options);
  canvas.addEventListener("pointercancel", () => { activePointer = start = null; redraw(); }, options);
  canvas.addEventListener("keydown", event => {
    if (!ready || activePointer !== null) return;
    const moves = {ArrowUp:[0,-1], ArrowDown:[0,1], ArrowLeft:[-1,0], ArrowRight:[1,0]};
    const delta = moves[event.key];
    if (!delta && event.key !== "Enter") return;
    event.preventDefault();
    keyboard = true;
    if (delta) cursor = cursor.map((n, i) => clamp(n + delta[i] * (event.shiftKey ? 50 : 10)));
    else if (start) finish();
    else start = cursor;
    redraw();
  }, options);
  for (const button of tools) button.addEventListener("click", () => selectTool(button.dataset.tool), options);
  undo.addEventListener("click", () => {
    if (start) start = null;
    else marks.pop();
    status.textContent = "Отметок: " + marks.length;
    redraw();
  }, options);
  save.addEventListener("click", () => dialog.close("save"), options);
  dialog.querySelector("[data-discard]").addEventListener("click", () => dialog.close("cancel"), options);

  selectTool("rect");
  save.disabled = undo.disabled = true;
  status.textContent = "Загрузка скриншота…";
  canvas.width = canvas.height = 1;
  dialog.returnValue = "";
  dialog.showModal();
  photo.src = url;
  try {
    await photo.decode();
    if (!listeners.signal.aborted) {
      const scale = Math.min(1, 1600 / Math.max(photo.naturalWidth, photo.naturalHeight));
      canvas.width = Math.max(1, Math.round(photo.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(photo.naturalHeight * scale));
      ready = true;
      save.disabled = false;
      status.textContent = "Отметок: " + marks.length;
      redraw();
    }
  } catch {
    if (!listeners.signal.aborted) status.textContent = "Не удалось открыть изображение.";
  }
  return result;
}
