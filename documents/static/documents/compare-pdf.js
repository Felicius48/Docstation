import { loadPdf } from "./pdf-document.js";

const panes = [...document.querySelectorAll("[data-compare-pdf]")];
const status = document.querySelector("#compare-controls [role=status]");
const buttons = [...document.querySelectorAll("#compare-controls button")];

try {
  const pdfs = await Promise.all(panes.map(pane => loadPdf({ url: pane.dataset.comparePdf }).promise));
  const last = Math.max(...pdfs.map(pdf => pdf.numPages));
  let number = 1;

  async function render() {
    buttons.forEach(button => { button.disabled = true; });
    status.textContent = "Страница " + number + " из " + last;
    await Promise.all(pdfs.map(async (pdf, index) => {
      const pane = panes[index];
      if (number > pdf.numPages) {
        pane.textContent = "В этой версии нет такой страницы.";
        return;
      }
      try {
        const page = await pdf.getPage(number);
        const base = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: Math.min(2, 900 / Math.max(base.width, base.height)) });
        const canvas = document.createElement("canvas");
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        await page.render({ canvas, viewport }).promise;
        pane.replaceChildren(canvas);
        page.cleanup();
      } catch {
        pane.textContent = "Не удалось показать страницу.";
      }
    }));
    buttons[0].disabled = number === 1;
    buttons[1].disabled = number === last;
  }

  buttons.forEach(button => button.addEventListener("click", () => {
    number += Number(button.dataset.step);
    render();
  }));
  await render();
} catch {
  status.textContent = "Не удалось открыть PDF.";
}
