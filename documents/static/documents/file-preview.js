const dialog = document.querySelector("#file-preview");
const control = (name) => dialog.querySelector("#preview-" + name);
const surface = control("surface");
const status = control("status");
let session;

function update(view) {
  control("page").textContent = view.pdf ? view.page + " / " + view.pdf.numPages : "";
  control("prev").disabled = view.page <= 1;
  control("next").disabled = !view.pdf || view.page >= view.pdf.numPages;
  control("smaller").disabled = !view.ready || view.zoom <= 0.5;
  control("larger").disabled = !view.ready || view.zoom >= 3;
}

async function draw(view) {
  const revision = ++view.revision;
  view.render?.cancel();
  status.textContent = "Загрузка страницы…";
  try {
    const page = await view.pdf.getPage(view.page);
    if (session !== view || revision !== view.revision) return;
    const base = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({
      scale: Math.min(surface.clientWidth / base.width, 2) * view.zoom,
    });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    view.render = page.render({ canvas, viewport });
    await view.render.promise;
    if (session !== view || revision !== view.revision) return;
    canvas.setAttribute("aria-label", "Страница " + view.page);
    surface.replaceChildren(canvas);
    status.textContent = "";
  } catch {
    if (session === view && revision === view.revision) status.textContent = "Не удалось открыть страницу.";
  }
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-file-preview]");
  if (!button || dialog.open) return;
  const { filePreview, download, name, type, size, version } = button.dataset;
  const view = { page: 1, zoom: 1, revision: 0 };
  session = view;
  control("title").textContent = name;
  const description = button.hasAttribute("data-comment-image") ? "Скриншот комментария"
    : version ? "Исходник · версия " + version : "Результат";
  control("meta").textContent = description + " · " + size;
  control("download").href = download;
  control("download").textContent = "Скачать";
  status.textContent = "Загрузка…";
  control("prev").hidden = control("next").hidden = type !== "application/pdf";
  update(view);
  dialog.showModal();
  try {
    if (type === "application/pdf") {
      const { loadPdf } = await import("./pdf-document.js");
      if (session !== view) return;
      view.task = loadPdf({ url: filePreview });
      view.pdf = await view.task.promise;
    } else if (type.startsWith("text/plain")) {
      const { loadTextPreview } = await import("./preview-text.js");
      view.text = await loadTextPreview(filePreview);
    } else {
      view.image = new Image();
      view.image.alt = name;
      view.image.src = filePreview;
      await view.image.decode();
    }
    if (session !== view) return;
    view.ready = true;
    update(view);
    if (view.pdf) draw(view);
    else if (view.text) {
      surface.replaceChildren(view.text);
      control("smaller").disabled = control("larger").disabled = true;
      status.textContent = "";
    } else {
      view.image.style.width = Math.min(view.image.naturalWidth, surface.clientWidth) + "px";
      if (button.dataset.marks) {
        const marked = document.createElement("marked-image");
        marked.dataset.marks = button.dataset.marks;
        marked.append(view.image);
        surface.replaceChildren(marked);
        control("download").textContent = "Скачать оригинал";
      } else {
        surface.replaceChildren(view.image);
      }
      status.textContent = "";
    }
  } catch (error) {
    if (session === view) status.textContent = error.name === "PasswordException"
      ? "PDF защищён паролем."
      : "Не удалось открыть файл.";
  }
});

for (const [name, delta] of [["prev", -1], ["next", 1], ["smaller", -0.25], ["larger", 0.25]]) {
  control(name).addEventListener("click", () => {
    const view = session;
    if (name === "prev" || name === "next") view.page += delta;
    else view.zoom += delta;
    update(view);
    if (view.pdf) draw(view);
    else view.image.style.width = Math.min(view.image.naturalWidth, surface.clientWidth) * view.zoom + "px";
  });
}

dialog.addEventListener("close", () => {
  const view = session;
  session = null;
  view.render?.cancel();
  view.task?.destroy().catch(() => {});
  view.image?.removeAttribute("src");
  surface.replaceChildren();
});
