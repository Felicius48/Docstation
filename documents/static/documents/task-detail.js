(() => {
  const select = (selector) => document.querySelector(selector);
  const form = select("#comment-form");
  const message = select("#task-sync-message");
  let refreshing = false;
  let submitting = false;

  const workspace = select(".task-workspace");
  const dialog = select("#comment-dialog");
  const views = select(".task-views");
  const narrow = matchMedia("(max-width: 900px)");
  let view = narrow.matches ? "brief" : "discussion";

  function showView(next) {
    view = next === "brief" && !narrow.matches ? "discussion" : next;
    for (const button of views.querySelectorAll("[data-view]")) {
      const active = button.dataset.view === view;
      button.setAttribute("aria-selected", String(active));
      button.classList.toggle("active", active);
      button.tabIndex = active ? 0 : -1;
      const pane = document.getElementById(button.getAttribute("aria-controls"));
      const standalone = !narrow.matches && button.dataset.view === "brief";
      pane.setAttribute("role", standalone ? "region" : "tabpanel");
      pane.hidden = !active && (narrow.matches || button.dataset.view !== "brief");
    }
  }
  function followCommentLink() {
    const target = document.getElementById(location.hash.slice(1));
    if (!target?.closest("#document-comments")) return;
    showView("discussion");
    if (target.id !== "document-comments") target.scrollIntoView({block: "nearest"});
  }
  views.addEventListener("click", event => {
    const button = event.target.closest("[data-view]");
    if (button) showView(button.dataset.view);
  });
  views.addEventListener("keydown", event => {
    const tabs = Array.from(views.querySelectorAll("[data-view]")).filter(tab => tab.offsetWidth);
    const index = tabs.indexOf(event.target);
    if (index < 0) return;
    const positions = {ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: tabs.length - 1};
    if (!Object.hasOwn(positions, event.key)) return;
    event.preventDefault();
    const tab = tabs[(positions[event.key] + tabs.length) % tabs.length];
    showView(tab.dataset.view);
    tab.focus();
  });
  narrow.addEventListener("change", () => showView(view));
  window.addEventListener("hashchange", followCommentLink);
  workspace.classList.add("is-interactive");
  views.hidden = select(".task-workspace-actions").hidden = false;
  showView(view);
  followCommentLink();
  dialog.close();

  const taskList = select(".task-switcher-items");
  const scrollKey = "docstation-task-list-scroll";
  try {
    const saved = JSON.parse(sessionStorage.getItem(scrollKey));
    sessionStorage.removeItem(scrollKey);
    if (saved?.path === location.pathname) taskList.scrollTop = saved.top;
  } catch {
    // Navigation also works when browser storage is unavailable.
  }
  taskList.addEventListener("click", event => {
    const link = event.target.closest("a");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    try {
      sessionStorage.setItem(scrollKey, JSON.stringify({path: link.pathname, top: taskList.scrollTop}));
    } catch {
      // Follow the link normally.
    }
  });


  function setVersionControl(enabled) {
    const control = select("#attachment-controls");
    control.hidden = !enabled;
    control.querySelectorAll("input, button").forEach(field => { field.disabled = !enabled; });
  }

  function openComposer(returning = false) {
    const control = select("#attachment-controls");
    if (control.hasAttribute("data-return-only")) setVersionControl(returning);
    const publish = form.querySelector("[data-publish]");
    publish.hidden = publish.disabled = returning;
    form.querySelector('[value="return"]').hidden = !returning;
    select("#compose-title").textContent = returning ? "Вернуть на переделку" : "Комментарий";
    if (!dialog.open) dialog.showModal();
  }
  if (dialog.hasAttribute("data-invalid")) openComposer(dialog.dataset.invalid === "return");
  select("[data-compose]").addEventListener("click", () => openComposer());
  select("[data-compose-return]").addEventListener("click", () => openComposer(true));
  select("[data-close-compose]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => {
    if (event.target.closest('a[href^="#comment-"]')) {
      dialog.close();
      showView("discussion");
    }
  });
  const replyContext = select("#reply-context");
  const replyInput = form.elements.reply_to;
  function selectReply(id) {
    const source = document.getElementById("comment-" + id)?.querySelector("template[data-quote]");
    replyInput.value = source ? id : "";
    replyContext.hidden = !source;
    replyContext.querySelector("[data-selected-quote]").replaceChildren(...(source ? [source.content.cloneNode(true)] : []));
  }
  document.addEventListener("click", event => {
    const reply = event.target.closest("[data-reply]");
    if (!reply && !event.target.closest("[data-cancel-reply]")) return;
    if (reply) openComposer();
    selectReply(reply?.dataset.reply || "");
    const frame = form.querySelector("[data-editor-frame]");
    const fields = frame.contentDocument.querySelectorAll('[contenteditable=true],textarea');
    (Array.from(fields).find(field => field.getClientRects().length) || form.elements.comment).focus();
  });
  selectReply(replyInput.value);
  form.addEventListener("reset", () => selectReply(""));

  function update(page) {
    for (const id of ["#task-header", "#task-file", "#task-comments", "#task-history"]) {
      select(id).replaceWith(page.querySelector(id));
    }
    const controls = select("#attachment-controls");
    const updated = page.querySelector("#attachment-controls");
    const returning = !form.querySelector('[value="return"]').hidden;
    controls.toggleAttribute("data-return-only", updated.hasAttribute("data-return-only"));
    setVersionControl(!updated.hidden && (!controls.hasAttribute("data-return-only") || returning));
    controls.querySelector("[data-file-status]").textContent = updated.querySelector("[data-file-status]").textContent;
    const returnButton = form.querySelector('[value="return"]');
    select("[data-compose]").className = page.querySelector("[data-compose]").className;
    const canReturn = !page.querySelector("[data-compose-return]").hidden;
    select("[data-compose-return]").hidden = returnButton.disabled = !canReturn;
    if (!canReturn && dialog.open && !returnButton.hidden) openComposer();
  }

  const parse = async (response) => new DOMParser().parseFromString(await response.text(), "text/html");

  async function refresh() {
    if (document.hidden || refreshing || submitting) return;
    refreshing = true;
    try {
      const receipt = select('#task-actions button[value="received"]');
      if (receipt) {
        const body = new FormData(receipt.form, receipt);
        await fetch(receipt.form.action, { method: "POST", body, redirect: "manual" });
      }
      const url = new URL(location.href);
      url.searchParams.set("after", select("#task-header").dataset.revision);
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok || response.status === 204 || response.redirected) return;
      const page = await parse(response);
      if (!submitting && Number(page.querySelector("#task-header").dataset.revision) >
          Number(select("#task-header").dataset.revision)) update(page);
    } catch {
      // Retry when the connection returns.
    } finally {
      refreshing = false;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting) return;
    submitting = true;
    const body = new FormData(form, event.submitter);
    form.inert = true;
    try {
      const response = await fetch(form.getAttribute("action"), { method: "POST", body });
      const page = await parse(response);
      if (!response.ok || !page.querySelector("#task-header")) throw new Error();
      if (!response.redirected) {
        message.textContent = page.querySelector("#comment-form .errorlist")?.textContent || "Проверь данные.";
        return;
      }
      form.reset();
      message.textContent = "";
      update(page);
      dialog.close();
      showView("discussion");
      select("#document-comments").scrollTop = 0;
      select("#comment-feedback").textContent = body.get("action") === "return"
        ? "Задача возвращена на переделку" : "Комментарий опубликован";
    } catch {
      message.textContent = "Не удалось подтвердить отправку. Проверь историю задачи.";
    } finally {
      submitting = false;
      form.inert = false;
    }
  });

  document.addEventListener("visibilitychange", refresh);
  refresh();
  setInterval(refresh, 3000);
})();
