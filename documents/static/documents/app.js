const converter = document.querySelector("#converter");

if (converter) {
  const form = document.querySelector("#conversion-form");
  const csrf = form.elements.csrfmiddlewaretoken.value;
  const fileInput = document.querySelector("#file-input");
  const dropzone = document.querySelector("#dropzone");
  const list = document.querySelector("#file-list");
  const status = document.querySelector("#form-status");
  const operation = form.elements.operation;
  const target = form.elements.target;
  const strength = form.elements.strength;
  const compressionValue = document.querySelector("#compression-value");
  const run = document.querySelector("#run");
  const rules = JSON.parse(document.querySelector("#file-rules").textContent);
  const matrix = rules.targets;
  const targets = Array.from(target.options);
  const sections = document.querySelectorAll(".workspace-section");
  const drafts = new Map();
  const toolsBySection = new Map();
  const sectionByFormat = new Map();
  for (const [key, config] of Object.entries(rules.sections)) {
    for (const format of config.formats) {
      if (!sectionByFormat.has(format)) sectionByFormat.set(format, key);
    }
  }
  fileInput.accept = [...sectionByFormat.keys()].map((format) => "." + format).join(",");
  let section = converter.dataset.section;
  let files = [];
  let busy = false;
  let pickingPages = false;
  let extractionMode = "combined";
  let previewRevision = 0;
  let previewTimer;

  const suffix = (name) => {
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  };

  if (converter.dataset.version) {
    const name = converter.dataset.versionName;
    for (const [key, config] of Object.entries(rules.sections)) {
      if (config.formats.includes(suffix(name))) {
        drafts.set(key, [{ id: Number(converter.dataset.version), name, url: converter.dataset.versionUrl }]);
      }
    }
  }
  files = drafts.get(section) ?? [];

  function message(text, kind = "") {
    status.textContent = text;
    status.hidden = !text;
    status.className = "form-status " + kind;
  }

  function compatible(name) {
    const ext = suffix(name);
    if (!rules.sections[section].formats.includes(ext)) return false;
    if (operation.value === "convert") return matrix[ext]?.includes(target.value);
    return rules.inputs[operation.value]?.includes(ext);
  }

  function updateTargets() {
    if (operation.value !== "convert") return;
    const formats = files.length ? files.map((item) => suffix(item.name)) : rules.sections[section].formats;
    const possible = new Set(formats.flatMap((ext) => matrix[ext] ?? []));
    const options = targets.filter((option) => possible.has(option.value));
    const previous = target.value;
    if (options.length === target.options.length && options.every((option, index) => option === target.options[index])) return;
    target.replaceChildren(...options);
    if (possible.has(previous)) target.value = previous;
  }

  function formatSize(bytes) {
    const unit = bytes >= 1_000_000 ? [1_000_000, "МБ"] : bytes >= 1_000 ? [1_000, "КБ"] : [1, "Б"];
    return new Intl.NumberFormat("ru", { maximumFractionDigits: 2 }).format(bytes / unit[0]) + " " + unit[1];
  }

  async function previewRequest(url, body) {
    const response = await fetch(url, body ? {
      method: "POST", headers: { "X-CSRFToken": csrf }, body,
    } : { cache: "no-store" });
    if (!response.ok || response.redirected) {
      const error = new Error("Размер не удалось рассчитать.");
      error.expired = response.status === 404;
      throw error;
    }
    return response.json();
  }

  async function previewSize(item, value, revision) {
    try {
      if (!item.preview) {
        const body = new FormData();
        body.append("strength", value);
        if (item.id) body.append("version", item.id);
        else body.append("file", item.file);
        item.preview = previewRequest(converter.dataset.previewUrl, body).catch((error) => {
          item.preview = null;
          throw error;
        });
      }
      const { url } = await item.preview;
      if (revision !== previewRevision) return;
      const update = (item.previewUpdate ?? Promise.resolve()).catch(() => {}).then(() => {
        if (revision === previewRevision) return previewRequest(url, new URLSearchParams({ strength: value }));
      });
      item.previewUpdate = update;
      let result = await update;
      if (!result || revision !== previewRevision) return;
      while (revision === previewRevision && ["queued", "running"].includes(result.state)) {
        await new Promise((resolve) => window.setTimeout(resolve, 1100));
        if (revision !== previewRevision) return;
        result = await previewRequest(url);
      }
      if (revision !== previewRevision || result.strength !== Number(value)) return;
      if (result.state !== "done") throw new Error();
      item.compressionLabel.textContent = formatSize(result.original) + " → " + formatSize(result.size);
    } catch (error) {
      if (error.expired && item.preview) {
        item.preview = null;
        if (revision === previewRevision) return previewSize(item, value, revision);
      }
      if (revision === previewRevision) item.compressionLabel.textContent = "Размер не рассчитан";
    }
  }

  function scheduleCompressionPreview() {
    const revision = ++previewRevision;
    window.clearTimeout(previewTimer);
    if (busy || operation.value !== "compress") return;
    const pending = files.filter((item) => compatible(item.name));
    for (const item of pending) item.compressionLabel.textContent = "Расчёт размера…";
    previewTimer = window.setTimeout(() => {
      const value = strength.value;
      async function measure() {
        while (pending.length && revision === previewRevision) {
          await previewSize(pending.shift(), value, revision);
        }
      }
      for (let index = 0; index < Math.min(3, pending.length); index++) measure();
    }, 350);
  }

  function render() {
    updateTargets();
    const scrollTop = list.scrollTop;
    list.hidden = !files.length;
    list.replaceChildren();
    files.forEach((item, index) => {
      const row = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = item.name;
      name.className = "file-name";
      const detail = document.createElement("small");
      detail.textContent = item.error || item.progress ||
        (!compatible(item.name) ? "Другой формат" : item.id ? "Загружен" : "");
      if (item.error) detail.className = "file-error";
      item.progressLabel = detail;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.className = "remove";
      remove.setAttribute("aria-label", "Убрать " + item.name);
      remove.disabled = busy;
      remove.addEventListener("click", () => {
        const restoreFocus = document.activeElement === remove;
        files.splice(index, 1);
        message("");
        render();
        if (restoreFocus) {
          const next = list.children[Math.min(index, files.length - 1)];
          (next?.querySelector("button.remove:not(.reorder)") || document.querySelector("#choose-files")).focus();
        }
      });
      row.append(name, detail);
      if (operation.value === "compress" && compatible(item.name)) {
        const size = document.createElement("small");
        size.className = "compression-size";
        size.setAttribute("role", "status");
        item.compressionLabel = size;
        row.append(size);
      }
      if (operation.value === "extract" && compatible(item.name)) {
        const pick = document.createElement("button");
        pick.type = "button";
        pick.className = "button secondary pick-pages";
        pick.textContent = item.pages ? "Страниц: " + item.pages.length : "Выбрать страницы";
        pick.setAttribute("aria-label", "Выбрать страницы: " + item.name);
        pick.disabled = busy;
        pick.addEventListener("click", () => selectPdfPages(item, document.activeElement === pick));
        row.append(pick);
      }
      if (rules.grouped.includes(operation.value) && files.length > 1) {
        for (const [direction, label, icon] of [[-1, "Выше", "↑"], [1, "Ниже", "↓"]]) {
          const move = document.createElement("button");
          move.type = "button";
          move.textContent = icon;
          move.className = "remove reorder";
          move.setAttribute("aria-label", label + ": " + item.name);
          const position = index + direction;
          move.disabled = busy || position < 0 || position >= files.length;
          move.addEventListener("click", () => {
            [files[index], files[position]] = [files[position], files[index]];
            render();
            const next = list.children[position].querySelectorAll("button.reorder");
            const sameDirection = next[direction === -1 ? 0 : 1];
            (sameDirection.disabled ? next[direction === -1 ? 1 : 0] : sameDirection).focus();
          });
          row.append(move);
        }
      }
      row.append(remove);
      list.append(row);
    });
    list.scrollTop = scrollTop;
    for (const button of sections) {
      const key = button.dataset.section;
      const count = key === section ? files.length : drafts.get(key)?.length ?? 0;
      button.querySelector(".workspace-count").textContent = count || "";
    }
    run.disabled = busy || !files.length || !files.some((item) => compatible(item.name));
    run.firstChild.textContent = operation.value === "extract" && files.some((item) => !item.pages)
      ? "Выбрать страницы " : "Запустить обработку ";
    scheduleCompressionPreview();
  }

  function pickTool(tool) {
    operation.value = tool;
    for (const element of document.querySelectorAll("[data-option]")) {
      element.hidden = !({
        target: tool === "convert", strength: tool === "compress",
      })[element.dataset.option];
    }
    message("");
    render();
  }

  async function selectPdfPages(item, restoreFocus = false) {
    if (!item || busy || pickingPages) return;
    pickingPages = true;
    try {
      const { openPdfPicker } = await import("./pdf-picker.js");
      const selection = await openPdfPicker(item, extractionMode);
      if (selection) {
        item.pages = selection.pages;
        extractionMode = selection.mode;
        render();
        if (restoreFocus) list.children[files.indexOf(item)]?.querySelector(".pick-pages")?.focus();
      }
    } catch {
      message("Не удалось загрузить окно предпросмотра PDF.", "error");
    } finally {
      pickingPages = false;
    }
  }

  function showSection(key) {
    converter.dataset.section = key;
    const config = rules.sections[key];
    for (const button of sections) {
      const active = button.dataset.section === key;
      button.setAttribute("aria-pressed", String(active));
      if (active) document.querySelector("#converter-title").textContent = button.querySelector(".workspace-label").textContent;
    }
    operation.replaceChildren(...Object.entries(config.tools).map(([value, label]) => new Option(label, value)));
    document.querySelector("#supported-formats").textContent = config.formats.map((ext) => ext.toUpperCase()).join(", ");
    pickTool(toolsBySection.get(key) ?? operation.value);
  }

  function switchSection(key) {
    if (key === section) return;
    toolsBySection.set(section, operation.value);
    section = key;
    files = drafts.get(key) ?? [];
    list.scrollTop = 0;
    showSection(key);
  }

  function addFiles(incoming) {
    if (busy || pickingPages) return;
    drafts.set(section, files);
    const errors = [];
    const destinations = new Set();
    let limitReached = false;
    for (const file of incoming) {
      const key = sectionByFormat.get(suffix(file.name));
      if (!key) {
        errors.push(file.name + " — формат не поддерживается.");
        continue;
      }
      if (!file.size || file.size > rules.maxBytes) {
        errors.push(file.name + " — проверь размер файла.");
        continue;
      }
      const destination = section === "markdown" ? section : key;
      const draft = drafts.get(destination) ?? [];
      if (draft.length >= rules.maxFiles) {
        limitReached = true;
        continue;
      }
      draft.push({ file, name: file.name });
      drafts.set(destination, draft);
      destinations.add(destination);
    }
    const destination = destinations.values().next().value ?? section;
    if (destination === section) render();
    else switchSection(destination);
    if (limitReached) errors.push("В каждом разделе можно выбрать до " + rules.maxFiles + " файлов.");
    const notice = destinations.size > 1 ? "Файлы распределены по разделам." : "";
    message([notice, ...errors].filter(Boolean).join(" "), errors.length ? "error" : "");
    if (operation.value === "extract") selectPdfPages(files.find((item) => !item.pages));
  }

  async function post(url, data) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "X-CSRFToken": csrf, "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (response.redirected) {
      window.location.assign(response.url);
      throw new Error("Войди в аккаунт.");
    }
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "Сервер не смог выполнить запрос.");
    return result;
  }

  function upload(url, item) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", url);
      request.setRequestHeader("X-CSRFToken", csrf);
      request.responseType = "json";
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          item.progress = Math.floor(event.loaded / event.total * 100) + "%";
          if (item.progressLabel.textContent !== item.progress) {
            item.progressLabel.textContent = item.progress;
          }
        }
      };
      request.onload = () => {
        if (request.status === 201) resolve(request.response.id);
        else reject(new Error(request.response?.error || "Не удалось загрузить файл."));
      };
      request.onerror = () => reject(new Error("Соединение прервалось."));
      const body = new FormData();
      body.append("file", item.file);
      request.send(body);
    });
  }

  async function send() {
    if (busy || pickingPages) return;
    const grouped = rules.grouped.includes(operation.value);
    if (grouped && (files.some((item) => !compatible(item.name)) ||
        (operation.value === "merge" && files.length < 2))) {
      message("Проверь тип файлов и их количество для этой операции.", "error");
      return;
    }
    if (operation.value === "extract") {
      const unselected = files.find((item) => !item.pages);
      if (unselected) {
        await selectPdfPages(unselected);
        return;
      }
    }
    busy = true;
    const options = Object.fromEntries(new FormData(form));
    delete options.csrfmiddlewaretoken;
    form.querySelectorAll("select, input, [type=button]").forEach((element) => { element.disabled = true; });
    message("Подготовка загрузки…");
    render();
    try {
      const batch = await post(converter.dataset.batchUrl, {});
      const pending = files.filter((item) => !item.id);
      for (const [index, item] of pending.entries()) {
        message("Загрузка " + (index + 1) + " из " + pending.length + "…");
        item.error = "";
        try {
          item.id = await upload(batch.upload, item);
        } catch (error) {
          item.error = error.message;
        }
        item.progress = "";
        render();
      }
      const uploaded = files.filter((item) => item.id);
      const failed = files.filter((item) => !item.id);
      if (grouped && failed.length) {
        message("Не загрузились: " + failed.map((item) => item.name).join(", ") + ". Для этой операции нужны все файлы. Повтори загрузку или убери их из списка.", "error");
        return;
      }
      if (!uploaded.length) {
        message("Не удалось загрузить файлы. Повтори попытку или убери их из списка.", "error");
        return;
      }
      const url = (await post(batch.submit, {
          ...options,
          versions: uploaded.map((item) => item.id),
          ...(options.operation === "extract" ? {
            mode: extractionMode,
            selections: Object.fromEntries(uploaded.map((item) => [item.id, item.pages])),
          } : {}),
        })).url;
      files = failed;
      fileInput.value = "";
      if (failed.length) {
        const link = document.createElement("a");
        link.href = url;
        link.textContent = "Открыть обработку загруженных файлов";
        const description = "Не загрузились: " + failed.map((item) => item.name).join(", ") + ". ";
        message(description, "error");
        status.append(link);
      } else {
        window.location.assign(url);
      }
    } catch (error) {
      message(error.message, "error");
    } finally {
      busy = false;
      form.querySelectorAll("select, input, [type=button]").forEach((element) => { element.disabled = false; });
      render();
    }
  }

  document.querySelector("#choose-files").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    addFiles(fileInput.files);
    fileInput.value = "";
  });
  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (!busy) dropzone.classList.add("drag");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("drag");
    addFiles(event.dataTransfer.files);
  });
  operation.addEventListener("change", () => {
    pickTool(operation.value);
    if (operation.value === "extract") selectPdfPages(files.find((item) => !item.pages));
  });
  target.addEventListener("change", render);
  strength.addEventListener("input", () => {
    compressionValue.value = strength.value;
    scheduleCompressionPreview();
  });
  for (const button of sections) {
    button.addEventListener("click", () => {
      if (busy || pickingPages) return;
      drafts.set(section, files);
      switchSection(button.dataset.section);
    });
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });
  showSection(section);
}

async function refreshJobs() {
  const panel = document.querySelector("#batch-results[data-poll]");
  if (!panel) return;
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("fragment", "jobs");
    const response = await fetch(url, { cache: "no-store" });
    if (response.redirected) {
      window.location.reload();
      return;
    }
    if (response.ok) {
      const documentUpdate = new DOMParser().parseFromString(await response.text(), "text/html");
      const replacement = documentUpdate.querySelector("#batch-results");
      if (replacement && !panel.isEqualNode(replacement) && !panel.contains(document.activeElement)) {
        panel.replaceWith(replacement);
      }
    }
  } catch {
    // Poll again when the server is reachable.
  }
  window.setTimeout(refreshJobs, 1800);
}

if (document.querySelector("[data-poll]")) window.setTimeout(refreshJobs, 1800);

const profileMenu = document.querySelector(".profile-menu");
if (profileMenu) {
  document.addEventListener("click", (event) => {
    if (profileMenu.open && !profileMenu.contains(event.target)) profileMenu.open = false;
  });
  profileMenu.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !profileMenu.open) return;
    profileMenu.open = false;
    profileMenu.querySelector("summary").focus();
  });
}

for (const picker of document.querySelectorAll("[data-recipient-picker]")) {
  const search = picker.querySelector("[data-recipient-search]");
  const status = picker.querySelector("[data-recipient-status]");
  const choices = [...picker.querySelectorAll(".recipient-options label")];
  search.closest("label").hidden = false;
  status.hidden = false;
  function updateRecipients() {
    const query = search.value.trim().toLocaleLowerCase();
    let selected = 0;
    let matches = 0;
    for (const choice of choices) {
      const checked = choice.querySelector("input").checked;
      const matchesQuery = choice.textContent.toLocaleLowerCase().includes(query);
      choice.hidden = !matchesQuery;
      selected += Number(checked);
      matches += Number(matchesQuery);
    }
    status.textContent = (matches ? "" : "Пользователи не найдены. ") + "Выбрано: " + selected;
  }
  search.addEventListener("input", updateRecipients);
  picker.addEventListener("change", updateRecipients);
  updateRecipients();
}

const taskDialog = document.querySelector("#task-dialog");
if (taskDialog) {
  document.querySelector("#add-task").addEventListener("click", () => taskDialog.showModal());
  taskDialog.querySelector("[data-close-task]").addEventListener("click", () => taskDialog.close());
  if (taskDialog.hasAttribute("data-invalid")) taskDialog.showModal();
}

const taskNotice = document.querySelector("[data-task-notice]");
if (taskNotice) {
  const links = taskNotice.querySelector("[data-task-links]");
  let displayed = "";
  let loading = false;

  async function refreshTaskNotice() {
    if (document.hidden || loading) return;
    loading = true;
    try {
      const response = await fetch(taskNotice.dataset.taskNotice, { cache: "no-store" });
      if (!response.ok || response.redirected) return;
      const { tasks } = await response.json();
      const ids = tasks.map((task) => task.id + ":" + task.state).join(",");
      if (ids === displayed) return;
      displayed = ids;
      links.replaceChildren(...tasks.map((task) => {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = task.url;
        link.textContent = task.state === "rework" ? "На переделку: " + task.title : task.title;
        item.append(link);
        return item;
      }));
      taskNotice.hidden = !tasks.length;
    } catch {
      // Retry on the next refresh.
    } finally {
      loading = false;
    }
  }

  taskNotice.querySelector("[data-dismiss-tasks]").addEventListener("click", () => {
    taskNotice.hidden = true;
  });
  document.addEventListener("visibilitychange", refreshTaskNotice);
  refreshTaskNotice();
  setInterval(refreshTaskNotice, 15000);
}
