const messagePrefix = "docstation-rich:v1\n";
const messageTags = new Set("a b blockquote br div em font i ins li ol p s span strike strong sub sup table tbody td th thead tr u ul h1 h2 h3 pre code hr cite".split(" "));
const messageStyles = {
  color: /^(#[a-f\d]{3,8}|rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\))$/i,
  backgroundColor: /^(#[a-f\d]{3,8}|rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\))$/i,
  textAlign: /^(left|center|right|justify)$/,
  marginLeft: /^(40|80|120|160)px$/,
  fontSize: /^(small|medium|large|x-large)$/,
};

function messageHtml(html) {
  const template = document.createElement("template");
  template.innerHTML = html;

  function clean(parent) {
    const fragment = document.createDocumentFragment();
    for (const node of parent.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) {
        fragment.append(node.textContent);
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const tag = node.localName;
        if (!messageTags.has(tag)) {
          if (!["script", "style", "iframe", "svg", "math", "template"].includes(tag)) fragment.append(clean(node));
          continue;
        }
        const element = document.createElement(tag);
        if (tag === "a") {
          const href = node.getAttribute("href") || "";
          if (/^(https?:|mailto:)/i.test(href) && !/[\u0000-\u001f]/.test(href)) element.setAttribute("href", href);
        }
        if (tag === "font") {
          const size = node.getAttribute("size");
          const color = node.getAttribute("color");
          if (/^[2-5]$/.test(size)) element.setAttribute("size", size);
          if (/^#[a-f\d]{6}$/i.test(color)) element.setAttribute("color", color);
        }
        const alignment = node.getAttribute("align");
        if (["left", "center", "right", "justify"].includes(alignment)) element.style.textAlign = alignment;
        for (const [property, pattern] of Object.entries(messageStyles)) {
          if (pattern.test(node.style?.[property] || "")) element.style[property] = node.style[property];
        }
        element.append(clean(node));
        fragment.append(element);
      }
    }
    return fragment;
  }

  return clean(template.content);
}

for (const editor of document.querySelectorAll("[data-message-editor]")) {
  const frame = editor.querySelector("[data-editor-frame]");
  const doc = frame.contentDocument;
  doc.open();
  doc.write("<!doctype html><html lang='ru'><head><meta charset='utf-8'></head><body></body></html>");
  doc.close();
  const sheet = doc.createElement("link");
  sheet.rel = "stylesheet";
  sheet.href = frame.dataset.css;
  doc.head.append(sheet);
  doc.body.append(doc.importNode(editor.querySelector("[data-editor-markup]").content, true));
  const $ = selector => doc.querySelector(selector);
  const ed = $("#editor"), src = $("#source"), panel = $("#panel");
  const value = editor.querySelector("textarea[name]");
  const form = value.form;
  const required = value.required;
  let mode = "visual";
  let savedRange = null;
  function sanitize(html) {
    const container = document.createElement("div");
    container.append(messageHtml(html));
    return container.innerHTML;
  }
  function sync() {
    const body = document.createElement("div");
    body.append(messageHtml(mode === "visual" ? ed.innerHTML : src.value));
    const text = body.textContent;
    value.value = !text.trim() ? "" : !body.children.length && !text.startsWith(messagePrefix)
      ? text : messagePrefix + body.innerHTML;
  }
  const input = editor.querySelector("[data-attachment-input]");
  const attach = editor.querySelector("[data-attach]");
  const list = editor.querySelector("[data-attachment-list]");
  const status = editor.querySelector("[data-file-status]");
  const help = status.textContent.trim();
  const maxFiles = input.multiple ? 5 : 1;
  let files = [];
  function push() {
    if (ed.innerHTML === '<br>') ed.innerHTML = '';
    src.value = ed.innerHTML;
    sync();
  }

  doc.addEventListener('selectionchange', function () {
    var s = frame.contentWindow.getSelection();
    if (s && s.rangeCount && ed.contains(s.anchorNode)) {
      savedRange = s.getRangeAt(0).cloneRange();
      updateState();
    }
  });
  function restore() {
    ed.focus();
    if (savedRange) {
      var s = frame.contentWindow.getSelection();
      s.removeAllRanges();
      s.addRange(savedRange);
    }
  }
  function cmd(name, val) {
    restore();
    doc.execCommand(name, false, val || null);
    push();
    updateState();
  }

  function updateState() {
    panel.querySelectorAll('[data-state]').forEach(function (b) {
      var on = false;
      try { on = doc.queryCommandState(b.getAttribute('data-state')); } catch (e) {}
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    var v = '';
    try { v = String(doc.queryCommandValue('formatBlock') || '').toLowerCase(); } catch (e) {}
    var map = { h1: 'h1', h2: 'h2', h3: 'h3', blockquote: 'blockquote', pre: 'pre' };
    $('#blockSelect').value = map[v] || 'p';
  }

  $('#toolbar').addEventListener('mousedown', function (e) {
    if (e.target.closest('button')) e.preventDefault();
  });
  panel.querySelectorAll('[data-cmd]').forEach(function (b) {
    b.addEventListener('click', function () { cmd(b.getAttribute('data-cmd')); });
  });
  $('#blockSelect').addEventListener('change', function (e) {
    cmd('formatBlock', '<' + e.target.value + '>');
  });
  $('#clearFmt').addEventListener('click', function () {
    restore();
    doc.execCommand('removeFormat');
    doc.execCommand('formatBlock', false, '<p>');
    push();
    updateState();
  });

  var linkbar = $('#linkbar'), linkUrl = $('#linkUrl');
  function closeLink() { linkbar.hidden = true; ed.focus(); }
  $('#linkBtn').addEventListener('click', function () {
    var href = '';
    if (savedRange) {
      var n = savedRange.commonAncestorContainer;
      var a = (n.nodeType === 1 ? n : n.parentElement).closest('a');
      if (a) href = a.getAttribute('href') || '';
    }
    linkbar.hidden = false;
    linkUrl.value = href;
    linkUrl.focus();
    linkUrl.select();
  });
  function applyLink() {
    var url = linkUrl.value.trim();
    if (!url) { closeLink(); return; }
    if (!/^(https?:|mailto:|tel:|#|\/)/i.test(url)) url = 'https://' + url;
    if (/^javascript:/i.test(url)) return;
    restore();
    var sel = frame.contentWindow.getSelection();
    if (sel.isCollapsed) {
      var a = doc.createElement('a');
      a.href = url; a.textContent = url;
      doc.execCommand('insertHTML', false, a.outerHTML);
    } else {
      doc.execCommand('createLink', false, url);
    }
    linkbar.hidden = true;
    push();
  }
  $('#linkApply').addEventListener('click', applyLink);
  $('#linkCancel').addEventListener('click', closeLink);
  $('#linkRemove').addEventListener('click', function () { cmd('unlink'); linkbar.hidden = true; });
  linkUrl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); applyLink(); }
    if (e.key === 'Escape') { e.preventDefault(); closeLink(); }
  });

  /* Text color and highlight */
  var colorbar = $('#colorbar'), swatchesEl = $('#swatches'), colorLabel = $('#colorLabel'), colorCustom = $('#colorCustom');
  var colorKind = null;
  var SENT = '#010203';
  var PALETTES = {
    text: { label: 'Цвет текста', items: [
      ['Цвет по умолчанию', null], ['Чёрный', '#111111'], ['Серый', '#6b7280'], ['Красный', '#d92d20'],
      ['Оранжевый', '#dc6803'], ['Зелёный', '#16803c'], ['Синий', '#2563eb'], ['Фиолетовый', '#7c3aed'], ['Розовый', '#db2777']
    ] },
    mark: { label: 'Выделение цветом', items: [
      ['Без выделения', null], ['Жёлтый', '#fff176'], ['Зелёный', '#b9f6ca'], ['Голубой', '#a5e1ff'],
      ['Розовый', '#ffc1dc'], ['Оранжевый', '#ffd6a0'], ['Сиреневый', '#d9c4ff'], ['Серый', '#d1d5db']
    ] }
  };
  function renderSwatches(kind) {
    swatchesEl.textContent = '';
    PALETTES[kind].items.forEach(function (it) {
      var b = doc.createElement('button');
      b.type = 'button';
      b.className = 'sw' + (it[1] ? '' : ' none');
      b.title = it[0];
      b.setAttribute('aria-label', it[0]);
      if (it[1]) b.style.background = it[1];
      b.addEventListener('click', function () { applyColor(kind, it[1]); });
      swatchesEl.appendChild(b);
    });
  }
  function closeColor() { colorbar.hidden = true; ed.focus(); }
  function openColor(kind) {
    if (!colorbar.hidden && colorKind === kind) { closeColor(); return; }
    colorKind = kind;
    linkbar.hidden = true;
    colorLabel.textContent = PALETTES[kind].label;
    renderSwatches(kind);
    colorbar.hidden = false;
  }
  function cleanSentinel() {
    var S = 'rgb(1, 2, 3)';
    ed.querySelectorAll('[style]').forEach(function (el) {
      if (el.style.color === S) el.style.removeProperty('color');
      if (el.style.backgroundColor === S) el.style.removeProperty('background-color');
      if (!el.getAttribute('style')) el.removeAttribute('style');
      if (el.tagName === 'SPAN' && !el.attributes.length) {
        while (el.firstChild) el.parentNode.insertBefore(el.firstChild, el);
        el.remove();
      }
    });
  }
  function applyColor(kind, color) {
    restore();
    var c = color || SENT;
    doc.execCommand('styleWithCSS', false, true);
    if (kind === 'text') {
      doc.execCommand('foreColor', false, c);
    } else if (!doc.execCommand('hiliteColor', false, c)) {
      doc.execCommand('backColor', false, c);
    }
    doc.execCommand('styleWithCSS', false, false);
    cleanSentinel();
    if (kind === 'text') $('#textBar').style.background = color || '';
    else $('#markIco').style.background = color || '';
    push();
    closeColor();
  }
  $('#textColorBtn').addEventListener('click', function () { openColor('text'); });
  $('#markBtn').addEventListener('click', function () { openColor('mark'); });
  $('#colorClose').addEventListener('click', closeColor);
  $('#linkBtn').addEventListener('click', function () { colorbar.hidden = true; });
  colorbar.addEventListener('mousedown', function (e) {
    if (e.target.closest('button')) e.preventDefault();
  });
  colorCustom.addEventListener('change', function () { if (colorKind) applyColor(colorKind, colorCustom.value); });

  /* Tables and quotes */
  var tablebar = $('#tablebar'), quotebar = $('#quotebar');
  var tblBtns = Array.prototype.slice.call(tablebar.querySelectorAll('[data-tbl]'));
  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function openBar(bar) {
    var willOpen = bar.hidden;
    [linkbar, colorbar, tablebar, quotebar].forEach(function (b) { b.hidden = true; });
    if (willOpen) bar.hidden = false;
    return willOpen;
  }
  ['#linkBtn', '#textColorBtn', '#markBtn'].forEach(function (id) {
    $(id).addEventListener('click', function () { tablebar.hidden = true; quotebar.hidden = true; });
  });
  [tablebar, quotebar].forEach(function (bar) {
    bar.addEventListener('mousedown', function (e) {
      if (e.target.closest('button')) e.preventDefault();
    });
  });
  $('#tblClose').addEventListener('click', function () { tablebar.hidden = true; ed.focus(); });
  $('#quoteClose').addEventListener('click', function () { quotebar.hidden = true; ed.focus(); });
  $('#tableBtn').addEventListener('click', function () { openBar(tablebar); });
  $('#quoteBtn').addEventListener('click', function () {
    if (openBar(quotebar)) $('#quoteSrc').focus();
  });

  function cellFromNode(n) {
    if (!n) return null;
    var el = n.nodeType === 1 ? n : n.parentElement;
    var c = el && el.closest('td,th');
    return c && ed.contains(c) ? c : null;
  }
  function inCell() { return savedRange ? cellFromNode(savedRange.startContainer) : null; }
  doc.addEventListener('selectionchange', function () {
    var s = frame.contentWindow.getSelection();
    if (s && s.rangeCount && ed.contains(s.anchorNode)) {
      var on = !!inCell();
      tblBtns.forEach(function (b) { b.disabled = !on; });
    }
  });

  $('#tblInsert').addEventListener('click', function () {
    var r = Math.min(30, Math.max(1, parseInt($('#tblRows').value, 10) || 3));
    var c = Math.min(12, Math.max(1, parseInt($('#tblCols').value, 10) || 3));
    var head = $('#tblHead').checked;
    var h = '<table><tbody>';
    for (var i = 0; i < r; i++) {
      h += '<tr>';
      for (var j = 0; j < c; j++) {
        var tag = (head && i === 0) ? 'th' : 'td';
        h += '<' + tag + '><br></' + tag + '>';
      }
      h += '</tr>';
    }
    h += '</tbody></table><p><br></p>';
    restore();
    doc.execCommand('insertHTML', false, h);
    push();
    tablebar.hidden = true;
  });

  function addRowAfter(row) {
    var nr = doc.createElement('tr');
    for (var i = 0; i < row.cells.length; i++) {
      var td = doc.createElement('td');
      td.innerHTML = '<br>';
      nr.appendChild(td);
    }
    row.after(nr);
    return nr;
  }
  tblBtns.forEach(function (b) {
    b.addEventListener('click', function () {
      var cell = inCell();
      if (!cell) return;
      var table = cell.closest('table'), row = cell.parentElement, idx = cell.cellIndex;
      switch (b.getAttribute('data-tbl')) {
        case 'addRow': addRowAfter(row); break;
        case 'addCol':
          Array.prototype.forEach.call(table.rows, function (tr) {
            var ref = tr.cells[idx] || tr.cells[tr.cells.length - 1];
            if (!ref) return;
            var nc = doc.createElement(ref.tagName.toLowerCase());
            nc.innerHTML = '<br>';
            ref.after(nc);
          });
          break;
        case 'delRow':
          row.remove();
          if (!table.rows.length) table.remove();
          break;
        case 'delCol':
          Array.prototype.forEach.call(table.rows, function (tr) { if (tr.cells[idx]) tr.cells[idx].remove(); });
          if (!table.querySelector('td,th')) table.remove();
          break;
        case 'delTable': table.remove(); break;
      }
      savedRange = null;
      tblBtns.forEach(function (d) { d.disabled = true; });
      push();
      ed.focus();
    });
  });

  /* Tab moves between table cells; Tab in the last cell adds a row */
  ed.addEventListener('keydown', function (e) {
    if (e.key !== 'Tab') return;
    var s = frame.contentWindow.getSelection();
    if (!s.rangeCount) return;
    var cell = cellFromNode(s.anchorNode);
    if (!cell) return;
    e.preventDefault();
    var table = cell.closest('table');
    var cells = Array.prototype.slice.call(table.querySelectorAll('td,th'));
    var i = cells.indexOf(cell) + (e.shiftKey ? -1 : 1);
    if (i < 0) return;
    if (i >= cells.length) {
      addRowAfter(cell.parentElement);
      cells = Array.prototype.slice.call(table.querySelectorAll('td,th'));
      push();
    }
    var r = doc.createRange();
    r.selectNodeContents(cells[i]);
    r.collapse(true);
    s.removeAllRanges();
    s.addRange(r);
  });

  $('#quoteInsert').addEventListener('click', function () {
    restore();
    var sel = frame.contentWindow.getSelection();
    var text = sel.isCollapsed ? '' : sel.toString().trim();
    var body = text
      ? text.split(/\n+/).map(function (p) { return '<p>' + esc(p) + '</p>'; }).join('')
      : '<p>Текст цитаты</p>';
    var who = $('#quoteSrc').value.trim();
    var h = '<blockquote>' + body + (who ? '<cite>— ' + esc(who) + '</cite>' : '') + '</blockquote><p><br></p>';
    doc.execCommand('insertHTML', false, h);
    push();
    quotebar.hidden = true;
    $('#quoteSrc').value = '';
  });
  $('#quoteUndo').addEventListener('click', function () {
    cmd('formatBlock', '<p>');
    quotebar.hidden = true;
  });

  function setMode(m) {
    if (m === mode) return;
    if (mode !== 'visual') ed.innerHTML = sanitize(src.value);
    src.value = ed.innerHTML;
    mode = m;
    panel.className = 'panel mode-' + m;
    $('#paneVisual').hidden = (m === 'html');
    $('#paneHtml').hidden = (m === 'visual');
    panel.querySelectorAll('.modes button').forEach(function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-mode') === m ? 'true' : 'false');
    });
    var off = (m === 'html');
    panel.querySelectorAll('#toolbar button, #toolbar select').forEach(function (c) { c.disabled = off; });
    if (off) { linkbar.hidden = colorbar.hidden = tablebar.hidden = quotebar.hidden = true; }
    (m === 'visual' ? ed : src).focus();
  }
  panel.querySelectorAll('.modes button').forEach(function (b) {
    b.addEventListener('click', function () { setMode(b.getAttribute('data-mode')); });
  });

  ed.addEventListener('input', push);
  ed.addEventListener('paste', function (e) {
    var cd = e.clipboardData;
    if (!cd) return;
    var html = cd.getData('text/html');
    if (html) {
      e.preventDefault();
      doc.execCommand('insertHTML', false, sanitize(html));
      push();
    }
  });
  var srcTimer;
  src.addEventListener('input', function () {
    clearTimeout(srcTimer);
    srcTimer = setTimeout(function () {
      if (mode === 'split') ed.innerHTML = sanitize(src.value);
      sync();
    }, 250);
  });

  function renderAttachments() {
    const selection = new DataTransfer();
    files.forEach(file => selection.items.add(file));
    input.files = selection.files;
    list.hidden = !files.length;
    list.replaceChildren(...files.map((file, index) => {
      const row = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = file.name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "button secondary";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Убрать " + file.name);
      remove.onclick = () => {
        files.splice(index, 1);
        renderAttachments();
        (list.children[Math.min(index, files.length - 1)]?.querySelector("button") || attach).focus();
      };
      row.append(name, remove);
      return row;
    }));
  }

  attach.onclick = () => input.click();
  input.addEventListener("change", () => {
    const chosen = input.multiple ? [...files, ...input.files] : [...input.files];
    if (chosen.length > maxFiles || chosen.reduce((sum, file) => sum + file.size, 0) > 50_000_000) {
      status.textContent = "Проверь число файлов и общий размер. Максимум 50 МБ.";
    } else {
      files = chosen;
      status.textContent = help;
    }
    renderAttachments();
  });

  ed.addEventListener("drop", event => event.preventDefault());
  form.addEventListener("submit", event => {
    sync();
    const error = required && !value.value.trim() ? "Напиши сообщение." :
      value.maxLength >= 0 && value.value.length > value.maxLength ? "Сообщение слишком длинное." : "";
    editor.parentElement.querySelector("[data-editor-error]").textContent = error;
    if (error) {
      event.preventDefault();
      event.stopImmediatePropagation();
      (mode === "html" ? src : ed).focus();
    }
  }, true);
  form.addEventListener("reset", () => {
    clearTimeout(srcTimer);
    src.value = "";
    ed.replaceChildren();
    files = [];
    list.replaceChildren();
    list.hidden = true;
    savedRange = null;
    setMode("visual");
  });
  value.required = false;
  value.hidden = true;
  input.hidden = true;
  attach.hidden = false;
  frame.hidden = false;
  const resize = () => { frame.style.height = Math.ceil(doc.body.getBoundingClientRect().height) + "px"; };
  new ResizeObserver(resize).observe(doc.body);
  resize();
  ed.setAttribute("aria-required", String(required));
  src.value = ed.innerHTML;
}
