(() => {
  const select = (selector) => document.querySelector(selector);
  const form = select("#comment-form");
  const message = select("#task-sync-message");
  let refreshing = false;
  let submitting = false;

  function update(page) {
    for (const id of ["#task-header", "#task-file", "#task-comments", "#task-actions", "#task-history"]) {
      select(id).replaceWith(page.querySelector(id));
    }
    const controls = select("#attachment-controls");
    const updated = page.querySelector("#attachment-controls");
    controls.hidden = updated.hidden;
    controls.querySelector("input").disabled = updated.hidden;
    controls.querySelector("p").textContent = updated.querySelector("p").textContent;
    const returnButton = form.querySelector('[value="return"]');
    returnButton.hidden = returnButton.disabled = page.querySelector('[value="return"]').hidden;
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
    const fields = [...form.elements].filter(field => !field.disabled);
    fields.forEach(field => { field.disabled = true; });
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
      fields.forEach(field => { field.disabled = false; });
      update(page);
    } catch {
      message.textContent = "Не удалось подтвердить отправку. Проверь историю задачи.";
    } finally {
      submitting = false;
      for (const field of fields) {
        if (!field.closest("[hidden]")) field.disabled = false;
      }
    }
  });

  document.addEventListener("visibilitychange", refresh);
  refresh();
  setInterval(refresh, 3000);
})();
