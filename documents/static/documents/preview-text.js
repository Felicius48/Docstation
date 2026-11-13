export async function loadTextPreview(url) {
  const response = await fetch(url);
  if (!response.ok || response.redirected) throw new Error("Не удалось загрузить файл.");
  const text = document.createElement("pre");
  text.className = "preview-text";
  text.textContent = await response.text();
  return text;
}
