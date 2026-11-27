import { loadPdf } from "./pdf-document.js";

const dialog = document.querySelector("#pdf-editor");
const get = id => dialog.querySelector("#editor-" + id);
const source = get("source");
const status = get("status");
const preview = get("preview");
const list = get("pages");
const limit = 2000;

export function openPdfEditor(files, existing) {
  const layout = existing.map(row => [...row]);
  const loaders = new Map();
  const listeners = new AbortController();
  const options = { signal: listeners.signal };
  let pageNumber = 1;
  let previewAngle = 0;
  let total = 0;
  let generation = 0;
  let result;
  let populateFirstFile = !layout.length;

  function renderList(focusIndex, focusAction) {
    list.replaceChildren(...layout.map(([file, page, angle], position) => {
      const row = document.createElement("li");
      const label = document.createElement("button");
      label.type = "button";
      label.className = "btn btn-outline-secondary editor-page-label";
      label.textContent = file.name + " · стр. " + page + (angle ? " · " + angle + "°" : "");
      function showChosenPage() {
        source.selectedIndex = files.indexOf(file);
        pageNumber = page;
        previewAngle = layout[position][2];
        renderPage();
      }
      label.addEventListener("click", showChosenPage);
      row.append(label);
      const actions = [
        ["↑", "Переместить вверх", position === 0, () => move(-1)],
        ["↓", "Переместить вниз", position === layout.length - 1, () => move(1)],
        ["↻", "Повернуть на 90°", false, () => { layout[position][2] = (angle + 90) % 360; showChosenPage(); }],
        ["×", "Убрать страницу", false, () => layout.splice(position, 1)],
      ];
      let next = position;
      function move(direction) {
        next += direction;
        [layout[position], layout[next]] = [layout[next], layout[position]];
      }
      actions.forEach(([symbol, title, disabled, action], actionIndex) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn btn-outline-secondary";
        button.textContent = symbol;
        button.setAttribute("aria-label", title + ": " + label.textContent);
        button.disabled = disabled;
        button.addEventListener("click", () => {
          action();
          renderList(Math.min(next, layout.length - 1), actionIndex);
        });
        row.append(button);
      });
      return row;
    }));
    get("apply").disabled = !layout.length;
    if (focusIndex !== undefined) {
      const buttons = list.children[focusIndex]?.querySelectorAll("button");
      const button = buttons?.[focusAction + 1];
      (button && !button.disabled ? button : get("apply").disabled ? get("close") : get("apply")).focus();
    }
  }

  async function renderPage() {
    const view = ++generation;
    const index = source.selectedIndex;
    total = 0;
    status.textContent = "Открываем страницу…";
    preview.replaceChildren();
    for (const id of ["prev", "next", "add", "all"]) get(id).disabled = true;
    try {
      if (!loaders.has(index)) {
        const item = files[index];
        const config = item.file ? { data: new Uint8Array(await item.file.arrayBuffer()) } : { url: item.url };
        if (options.signal.aborted || view !== generation) return;
        loaders.set(index, loadPdf(config));
      }
      const pdf = await loaders.get(index).promise;
      if (view !== generation) return;
      total = pdf.numPages;
      if (populateFirstFile && index === 0 && !layout.length && pageNumber === 1 && total <= limit) {
        populateFirstFile = false;
        for (let page = 1; page <= total; page++) layout.push([files[index], page, 0]);

        renderList();
      }
      const page = await pdf.getPage(pageNumber);
      if (view !== generation) return;
      const rotation = (page.rotate + previewAngle) % 360;
      const base = page.getViewport({ scale: 1, rotation });
      const viewport = page.getViewport({
        scale: Math.min(1.5, 640 / Math.max(base.width, base.height)),
        rotation,
      });
      const canvas = document.createElement("canvas");
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      await page.render({ canvas, viewport }).promise;
      if (view !== generation) return;
      preview.replaceChildren(canvas);
      page.cleanup();
      status.textContent = "Страница " + pageNumber + " из " + total;
      get("prev").disabled = pageNumber <= 1;
      get("next").disabled = pageNumber >= total;
      get("add").disabled = false;
      get("all").disabled = false;
    } catch {
      if (view === generation) status.textContent = "Не удалось открыть страницу PDF.";
    }
  }

  function add(pages) {
    if (layout.length + pages.length > limit) {
      status.textContent = "Можно добавить до 2000 страниц.";
      return;
    }
    layout.push(...pages.map(page => [files[source.selectedIndex], page, 0]));
    renderList();
    list.lastElementChild?.scrollIntoView({ block: "nearest" });
  }

  source.replaceChildren(...files.map(file => new Option(file.name)));
  source.addEventListener("change", () => { pageNumber = 1; previewAngle = 0; renderPage(); }, options);
  get("prev").addEventListener("click", () => { pageNumber--; previewAngle = 0; renderPage(); }, options);
  get("next").addEventListener("click", () => { pageNumber++; previewAngle = 0; renderPage(); }, options);
  get("add").addEventListener("click", () => add([pageNumber]), options);
  get("all").addEventListener("click", () => add(Array.from({ length: Math.min(total, limit + 1) }, (_, index) => index + 1)), options);
  get("close").addEventListener("click", () => dialog.close(), options);
  get("apply").addEventListener("click", () => { result = layout; dialog.close(); }, options);
  renderList();
  dialog.showModal();
  renderPage();
  return new Promise(resolve => dialog.addEventListener("close", () => {
    generation++;
    listeners.abort();
    loaders.forEach(task => task.destroy().catch(() => {}));
    resolve(result);
  }, { once: true }));
}
