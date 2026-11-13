import * as pdfjs from "./vendor/pdfjs/legacy/build/pdf.min.mjs";

const assets = new URL("./vendor/pdfjs/", import.meta.url);
pdfjs.GlobalWorkerOptions.workerSrc = new URL("legacy/build/pdf.worker.min.mjs", assets).href;

export function loadPdf(source) {
  return pdfjs.getDocument({
    ...source,
    cMapUrl: new URL("cmaps/", assets).href,
    cMapPacked: true,
    standardFontDataUrl: new URL("standard_fonts/", assets).href,
    wasmUrl: new URL("wasm/", assets).href,
    iccUrl: new URL("iccs/", assets).href,
    isEvalSupported: false,
    enableXfa: false,
  });
}
