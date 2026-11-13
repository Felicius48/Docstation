const container = document.querySelector("[data-inline-preview]");

if (container) {
  const surface = container.querySelector(".preview-surface");
  const status = container.querySelector("[role=status]");
  const controls = container.querySelector(".pdf-picker-controls");
  const previous = container.querySelector("[data-previous]");
  const next = container.querySelector("[data-next]");
  const pageLabel = container.querySelector("[data-page]");
  const url = container.dataset.inlinePreview;
  const type = container.dataset.type;
  let task;
  let rendering;
  let pageNumber = 1;
  let revision = 0;

  try {
    if (type === "application/pdf") {
      const { loadPdf } = await import("./pdf-document.js");
      task = loadPdf({ url });
      const pdf = await task.promise;

      async function draw() {
        const currentRevision = ++revision;
        rendering?.cancel();
        status.textContent = "Загрузка страницы…";
        previous.disabled = pageNumber === 1;
        next.disabled = pageNumber === pdf.numPages;
        pageLabel.textContent = pageNumber + " / " + pdf.numPages;
        try {
          const number = pageNumber;
          const page = await pdf.getPage(number);
          if (currentRevision !== revision) return;
          const original = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: Math.min(surface.clientWidth / original.width, 1.5) });
          const canvas = document.createElement("canvas");
          canvas.width = Math.ceil(viewport.width);
          canvas.height = Math.ceil(viewport.height);
          rendering = page.render({ canvas, viewport });
          await rendering.promise;
          if (currentRevision !== revision) return;
          canvas.setAttribute("aria-label", "Страница " + number);
          surface.replaceChildren(canvas);
          status.textContent = "";
        } catch (error) {
          if (currentRevision === revision && error.name !== "RenderingCancelledException") {
            status.textContent = "Не удалось открыть страницу.";
          }
        }
      }

      previous.addEventListener("click", () => { pageNumber--; draw(); });
      next.addEventListener("click", () => { pageNumber++; draw(); });
      controls.hidden = pdf.numPages <= 1;
      await draw();
    } else if (type.startsWith("text/plain")) {
      const { loadTextPreview } = await import("./preview-text.js");
      surface.replaceChildren(await loadTextPreview(url));
      status.textContent = "";
    } else {
      const image = new Image();
      image.alt = "Текущая версия документа";
      image.src = url;
      await image.decode();
      surface.replaceChildren(image);
      status.textContent = "";
    }
  } catch (error) {
    status.textContent = error.name === "PasswordException" ? "PDF защищён паролем." : "Не удалось открыть файл.";
  }

  window.addEventListener("pagehide", () => {
    revision++;
    rendering?.cancel();
    task?.destroy().catch(() => {});
  }, { once: true });
}
