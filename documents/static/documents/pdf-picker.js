import { loadPdf } from "./pdf-document.js";

const dialog = document.querySelector("#pdf-picker");
const element = (name) => dialog.querySelector("#pdf-picker-" + name);
const grid = element("pages");
const status = element("status");
const apply = element("apply");
const limit = 2000;
const groupSize = 24;

export function openPdfPicker(item, mode) {
  const selected = new Set(item.pages);
  const listeners = new AbortController();
  const options = { signal: listeners.signal };
  let task;
  let pdf;
  let first = 1;
  let activeRender;
  let revision = 0;
  let result;

  function updateCount() {
    element("count").textContent = "Выбрано: " + selected.size;
    apply.disabled = !selected.size;
    for (const checkbox of grid.querySelectorAll("input")) {
      checkbox.checked = selected.has(Number(checkbox.value));
    }
  }

  async function showPages() {
    const view = ++revision;
    activeRender?.cancel();
    grid.replaceChildren();
    const last = Math.min(first + groupSize - 1, pdf.numPages);
    element("range").textContent = first + "–" + last + " из " + pdf.numPages;
    element("prev").disabled = first === 1;
    element("next").disabled = last === pdf.numPages;
    const previews = [];
    for (let number = first; number <= last; number++) {
      const label = document.createElement("label");
      label.className = "pdf-thumbnail";
      const preview = document.createElement("span");
      preview.className = "pdf-thumbnail-preview";
      preview.setAttribute("aria-hidden", "true");
      preview.textContent = "Загрузка…";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = number;
      checkbox.addEventListener("change", () => {
        if (!checkbox.checked) selected.delete(number);
        else if (selected.size < limit) selected.add(number);
        else status.textContent = "Можно выбрать до " + limit + " страниц.";
        updateCount();
      });
      label.append(preview, checkbox, "Страница " + number);
      grid.append(label);
      previews.push([number, preview]);
    }
    updateCount();

    for (const [number, preview] of previews) {
      if (view !== revision) return;
      let page;
      try {
        page = await pdf.getPage(number);
        if (view !== revision) return;
        const original = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: Math.min(155 / original.width, 142 / original.height) });
        const canvas = document.createElement("canvas");
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        activeRender = page.render({ canvas, viewport });
        await activeRender.promise;
        if (view === revision) preview.replaceChildren(canvas);
      } catch {
        if (view === revision) preview.textContent = "Нет предпросмотра";
      } finally {
        page?.cleanup();
      }
    }
  }

  element("name").textContent = item.name;
  element("mode").value = mode;
  status.textContent = "Открываем PDF…";
  grid.replaceChildren();
  element("controls").hidden = true;
  element("navigation").hidden = true;
  apply.disabled = true;

  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      revision++;
      activeRender?.cancel();
      listeners.abort();
      grid.replaceChildren();
      task?.destroy().catch(() => {});
      resolve(result);
    }, { once: true });
    element("close").addEventListener("click", () => dialog.close(), options);
    apply.addEventListener("click", () => {
      result = { pages: [...selected].sort((a, b) => a - b), mode: element("mode").value };
      dialog.close();
    }, options);
    element("all").addEventListener("click", () => {
      if (pdf.numPages > limit) {
        status.textContent = "Можно выбрать до " + limit + " страниц.";
        return;
      }
      for (let number = 1; number <= pdf.numPages; number++) selected.add(number);
      updateCount();
    }, options);
    element("clear").addEventListener("click", () => {
      selected.clear();
      status.textContent = "";
      updateCount();
    }, options);
    for (const [button, direction] of [["prev", -1], ["next", 1]]) {
      element(button).addEventListener("click", () => {
        first += direction * groupSize;
        showPages();
      }, options);
    }
    dialog.showModal();

    (async () => {
      try {
        const source = item.file ? { data: new Uint8Array(await item.file.arrayBuffer()) } : { url: item.url };
        if (listeners.signal.aborted) return;
        task = loadPdf(source);
        pdf = await task.promise;
        if (listeners.signal.aborted) return;
        if (!pdf.numPages) throw new Error("Empty PDF");
        status.textContent = "";
        element("controls").hidden = false;
        element("navigation").hidden = pdf.numPages <= groupSize;
        showPages();
      } catch (error) {
        if (!listeners.signal.aborted) {
          status.textContent = error.name === "PasswordException"
            ? "Этот PDF защищён паролем."
            : "Не удалось открыть PDF. Проверь, что файл читается.";
        }
      }
    })();
  });
}
