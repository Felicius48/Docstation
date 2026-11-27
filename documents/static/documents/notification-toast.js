(() => {
  const container = document.querySelector("#notification-toasts");
  if (!container) return;
  const key = "docstation-notification:" + container.dataset.user;
  const pending = [];
  let lastShown = null;

  function cursor() {
    try {
      const stored = localStorage.getItem(key);
      if (stored !== null && /^\d+$/.test(stored)) {
        lastShown = Math.max(lastShown || 0, Number(stored));
      }
    } catch {
      // The current page still tracks displayed notifications when storage is unavailable.
    }
    return lastShown;
  }

  function remember(id) {
    lastShown = id;
    try { localStorage.setItem(key, String(id)); } catch {}
  }

  let loading = false;
  let showing = false;

  function showNext() {
    if (showing || document.hidden) return;
    const shown = cursor();
    while (pending.length && pending[0].id <= shown) pending.shift();
    if (!pending.length) return;
    showing = true;
    const notice = pending.shift();
    const card = document.createElement("div");
    card.className = "toast notification-toast";
    const link = document.createElement("a");
    link.href = notice.url;
    link.textContent = notice.label + " · " + notice.title;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close";
    close.setAttribute("aria-label", "Закрыть уведомление");
    const body = document.createElement("div");
    body.className = "toast-body d-flex align-items-start justify-content-between gap-3";
    body.append(link, close);
    card.append(body);
    container.append(card);
    const toast = tabler.Toast.getOrCreateInstance(card, { animation: false, autohide: false });
    toast.show();
    remember(notice.id);

    function dismiss() {
      if (card.classList.contains("leaving")) return;
      clearTimeout(timer);
      card.classList.add("leaving");
      setTimeout(() => {
        toast.hide();
        toast.dispose();
        card.remove();
        showing = false;
        showNext();
      }, 240);
    }

    let startX;
    let startY;
    card.addEventListener("pointerdown", event => {
      startX = event.clientX;
      startY = event.clientY;
    });
    card.addEventListener("pointerup", event => {
      const distance = Math.abs(event.clientX - startX);
      if (distance > 45 && distance > Math.abs(event.clientY - startY)) {
        card.addEventListener("click", click => click.preventDefault(), { capture: true, once: true });
        dismiss();
      }
      startX = startY = undefined;
    });
    card.addEventListener("pointercancel", () => { startX = startY = undefined; });
    close.addEventListener("click", dismiss);
    const timer = setTimeout(dismiss, 3000);
  }

  async function refresh() {
    if (document.hidden || loading) return;
    if (pending.length) {
      showNext();
      return;
    }
    loading = true;
    try {
      const previous = cursor();
      const url = new URL(container.dataset.url, location.href);
      if (previous !== null) url.searchParams.set("after", previous);
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok || response.redirected) return;
      const { notifications } = await response.json();
      if (previous === null) {
        if (notifications.length) pending.push(notifications[0]);
        else remember(0);
      } else {
        pending.push(...notifications);
      }
      showNext();
    } catch {
      // Retry when the connection returns.
    } finally {
      loading = false;
    }
  }

  document.addEventListener("visibilitychange", refresh);
  refresh();
  setInterval(refresh, 3000);
})();
